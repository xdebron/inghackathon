"""TCMB EVDS (resmi API, anahtarlı): kur, altın/gümüş, BIST 100, TLREF, fonlama maliyeti, mevduat faizleri, eurobond,
TÜFE ve politika faizi serileri (2021 → bugün). EVDS tek istekte en çok 1000 satır döndürdüğü için 2 yıllık parçalarla
istenir. Değer uydurulmaz; boş gözlem atlanır, çekilemeyen seri hata olarak kaydedilir.
"""
from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd

from ..db import store
from ..sources import evds
from .base import Polite, write_series

# id: (seri kodu, ad, birim, sıklık, çarpan, yüzde mi)
SERIES = {
    "usdtry": ("TP.DK.USD.S.YTL", "USD/TRY döviz satış (TCMB)", "TRY", "günlük", 1, False),
    "eurtry": ("TP.DK.EUR.S.YTL", "EUR/TRY döviz satış (TCMB)", "TRY", "günlük", 1, False),
    "gbptry": ("TP.DK.GBP.S.YTL", "GBP/TRY döviz satış (TCMB)", "TRY", "günlük", 1, False),
    "altin_gram_tl": ("TP.ALTINPIYASA.KAP02", "Altın kapanış (BİST, TL/kg ÷ 1000 = TL/gr)", "TRY/gr", "iş günü", 0.001, False),
    "altin_usd_ons": ("TP.ALTINPIYASA.KAP03", "Altın kapanış (BİST, USD/ons)", "USD/ons", "iş günü", 1, False),
    "gumus_gram_tl": ("TP.GUMUSPIYASA.KAP02", "Gümüş kapanış (BİST, TL/kg ÷ 1000 = TL/gr)", "TRY/gr", "iş günü", 0.001, False),
    "gumus_usd_ons": ("TP.GUMUSPIYASA.KAP03", "Gümüş kapanış (BİST, USD/ons)", "USD/ons", "iş günü", 1, False),
    "bist100_fiyat": ("TP.MK.F.BILESIK", "BIST 100 fiyat endeksi (XU100)", "endeks", "iş günü", 1, False),
    "bist100_getiri": ("TP.MK.G.BILESIK", "BIST 100 getiri endeksi (XU100-CFNNTLTL, temettü dahil)", "endeks", "iş günü", 1, False),
    "tcmb_aofm": ("TP.APIFON4", "TCMB ağırlıklı ortalama fonlama maliyeti", "oran", "iş günü", 1, True),
    "tlref": ("TP.BISTTLREF.ORAN", "BIST TLREF gecelik referans faiz", "oran", "iş günü", 1, True),
    "mevduat_tl_1a": ("TP.TRYTAS.MT01", "TL tasarruf mevduatı faizi, 1 aya kadar (akım)", "oran", "haftalık", 1, True),
    "mevduat_tl_3a": ("TP.TRYTAS.MT02", "TL tasarruf mevduatı faizi, 3 aya kadar (akım)", "oran", "haftalık", 1, True),
    "mevduat_tl_6a": ("TP.TRYTAS.MT03", "TL tasarruf mevduatı faizi, 6 aya kadar (akım)", "oran", "haftalık", 1, True),
    "mevduat_tl_1y": ("TP.TRYTAS.MT04", "TL tasarruf mevduatı faizi, 1 yıla kadar (akım)", "oran", "haftalık", 1, True),
    "mevduat_tl_1y_ustu": ("TP.TRYTAS.MT05", "TL tasarruf mevduatı faizi, 1 yıl ve üzeri (akım)", "oran", "haftalık", 1, True),
    "mevduat_usd_1a": ("TP.USDTAS.MT01", "USD tasarruf mevduatı faizi, 1 aya kadar (akım)", "oran", "haftalık", 1, True),
    "mevduat_eur_1a": ("TP.EURTAS.MT01", "EUR tasarruf mevduatı faizi, 1 aya kadar (akım)", "oran", "haftalık", 1, True),
    "eurobond_hazine_piyasa_degeri": ("TP.EBONDPIYDEG.ST", "Genel yönetim eurobondları, piyasa değeri (tüm yatırımcılar)", "milyon USD", "haftalık", 1, False),
    "eurobond_hazine_yazili_deger": ("TP.EBONDYAZDEG.ST", "Genel yönetim eurobondları, yazılı (nominal) değer (tüm yatırımcılar)", "milyon USD", "haftalık", 1, False),
    "tufe_endeks": ("TP.GENENDEKS.T1", "TÜFE genel endeks (2003=100)", "endeks", "aylık", 1, False),
    "politika_faizi_bis": ("TP.BISPOLFAIZ.TUR", "TCMB politika faizi (BIS veri seti)", "oran", "aylık", 1, True),
}


def parse_evds_date(s: str) -> date:
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    y, m = s.split("-")[:2]  # aylık: '2026-9'
    return date(int(y), int(m), 1)


def _fetch(cli: Polite, code: str, start: date, key: str) -> list[tuple[date, float]]:
    base = evds.series_config()["base_url"]
    prefix = code.replace(".", "_")
    pts: list[tuple[date, float]] = []
    y = start.year
    while y <= date.today().year:
        s = start if y == start.year else date(y, 1, 1)
        e = min(date(y + 1, 12, 31), date.today())
        r = cli.get(f"{base}series={code}&startDate={s:%d-%m-%Y}&endDate={e:%d-%m-%Y}&type=json", headers={"key": key})
        if r is None:
            raise RuntimeError(f"EVDS yanıt vermedi ({code})")
        for it in r.json().get("items", []):
            for k, v in it.items():
                if k.startswith(prefix) and v not in (None, "", "null"):
                    pts.append((parse_evds_date(it["Tarih"]), float(v)))
        y += 2
    return pts


def run(start: str = "2021-01-01", pause: float = 1.0, only: list[str] | None = None, log=print) -> dict:
    key = os.getenv("EVDS_API_KEY")
    if not key:
        raise RuntimeError("EVDS_API_KEY tanımlı değil (backend/.env)")
    s0 = date.fromisoformat(start)
    base = evds.series_config()["base_url"]
    cli = Polite(pause=pause)
    ok, bad = 0, []
    with store.Run("evds_seriler") as run_:
        for sid, (code, name, unit, freq, mult, pct) in SERIES.items():
            if only and sid not in only:
                continue
            info = {"ad": name, "kaynak": f"TCMB EVDS ({code})", "seri_kodu": code, "url": base, "birim": unit, "siklik": freq,
                    "alinma": store.now_iso(), "baslangic_istegi": start,
                    "donusum": "; ".join(x for x in ("yüzde → oran (÷100)" if pct else "", f"× {mult}" if mult != 1 else "") if x) or None}
            try:
                df = pd.DataFrame(_fetch(cli, code, s0, key), columns=["tarih", "deger"])
                df["deger"] = df["deger"] * mult / (100 if pct else 1)
                m = write_series(sid, df, info, run_)
                ok += 1
                log(f"  {sid:30} {m['satir']:>5} satır  {m['ilk']} → {m['son']}")
            except Exception as exc:
                bad.append(f"{sid}: {type(exc).__name__}: {exc}")
                log(f"  {sid:30} HATA {exc}")
        if not only or "tufe_yillik" in only:
            try:   # TÜFE yıllık: endeksten (12 ay önceki endeks için bir yıl öncesinden istenir)
                idx = pd.DataFrame(_fetch(cli, "TP.GENENDEKS.T1", date(s0.year - 1, 1, 1), key), columns=["tarih", "endeks"])
                idx = idx.drop_duplicates("tarih").sort_values("tarih").set_index("tarih")["endeks"]
                yoy = (idx / idx.shift(12) - 1).dropna()
                yoy = yoy[yoy.index >= s0]
                m = write_series("tufe_yillik", pd.DataFrame({"tarih": yoy.index, "deger": yoy.values}), {
                    "ad": "TÜFE yıllık değişim (genel endeks 2003=100'den hesaplandı)", "kaynak": "TCMB EVDS (TP.GENENDEKS.T1) + hesaplama",
                    "seri_kodu": "TP.GENENDEKS.T1", "url": base, "birim": "oran", "siklik": "aylık", "alinma": store.now_iso(),
                    "donusum": "endeks_t / endeks_(t-12) − 1"}, run_)
                ok += 1
                log(f"  {'tufe_yillik':30} {m['satir']:>5} satır  {m['ilk']} → {m['son']}")
                last = yoy.index[-1]   # gösterge (canlı EVDS çekimi başarısız olursa kullanılan yedek değer)
                store.put_indicator("tufe_yillik", float(yoy.iloc[-1]), "oran", "TCMB EVDS (TP.GENENDEKS.T1) + hesaplama", base,
                                    f"{last.year}-{last.month}", store.now_iso(), "Genel endeksin 12 ay önceye göre değişimi.", run_)
            except Exception as exc:
                bad.append(f"tufe_yillik: {exc}")
        run_.durum = "kismi" if bad else "basarili"
        run_.mesaj = "; ".join(bad) or None
    cli.close()
    return {"seri": ok, "hata": bad}
