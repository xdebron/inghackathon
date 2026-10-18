"""2021'den bugüne gösterge tarihçesi: TCMB EVDS (resmi API, anahtarlı) + TCMB politika faizi karar tarihçesi.

Çıktı: backend/data/snapshots/gecmis/<id>.csv  (tarih, deger)  +  gecmis/meta.json (kaynak, seri kodu, birim, sıklık,
alınma zamanı, satır sayısı, ilk/son tarih, dönüşüm). Değer uydurulmaz; boş gözlemler atlanır, seri çekilemezse meta'da
'missing' olarak kalır. İstekler sıralı ve aralarında bekleme vardır (EVDS tek istekte en çok 1000 satır döndürür).

Kullanım:  backend/.venv/Scripts/python backend/tools/fetch_history.py [--start 01-01-2021] [--pause 1.0]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import SNAPSHOT_DIR, now_iso  # noqa: E402  (backend/.env de buradan yüklenir)
from app.sources import evds  # noqa: E402

OUT = SNAPSHOT_DIR / "gecmis"

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


def fetch(code: str, start: date, key: str, pause: float, timeout: float = 60) -> list[tuple[date, float]]:
    base = evds.series_config()["base_url"]
    prefix = code.replace(".", "_")
    pts: list[tuple[date, float]] = []
    y = start.year
    while y <= date.today().year:
        s = start if y == start.year else date(y, 1, 1)
        e = min(date(y + 1, 12, 31), date.today())  # 2 yıllık parça (< 1000 iş günü)
        url = f"{base}series={code}&startDate={s:%d-%m-%Y}&endDate={e:%d-%m-%Y}&type=json"
        r = httpx.get(url, headers={"key": key}, timeout=timeout)
        r.raise_for_status()
        for it in r.json().get("items", []):
            for k, v in it.items():
                if k.startswith(prefix) and v not in (None, "", "null"):
                    pts.append((parse_evds_date(it["Tarih"]), float(v)))
        time.sleep(pause)
        y += 2
    return pts


def write(sid: str, df: pd.DataFrame, meta: dict, info: dict):
    df = df.drop_duplicates("tarih", keep="last").sort_values("tarih")
    df.to_csv(OUT / f"{sid}.csv", index=False)
    meta[sid] = {**info, "satir": int(len(df)), "ilk": str(df["tarih"].min()) if len(df) else None,
                 "son": str(df["tarih"].max()) if len(df) else None, "status": "ok" if len(df) else "missing"}


def policy_decisions(start: date, meta: dict):
    """TCMB'nin kendi sayfasından (Gamze Akkurt, 09.10.2026) politika faizi karar tarihçesi: yürürlük tarihli adım serisi."""
    src = ROOT / "gamze_work" / "AGENTIC AI HACKATHON-ING" / "data" / "processed" / "tcmb_policy_rate.json"
    if not src.exists():
        return
    p = json.loads(src.read_text(encoding="utf-8"))
    rows = [(date.fromisoformat(r["effective_date"]), r["lending_rate_pct"] / 100) for r in p["series"] if r.get("lending_rate_pct") is not None]
    before = [r for r in rows if r[0] < start]
    rows = ([(start, before[-1][1])] if before else []) + [r for r in rows if r[0] >= start]
    write("politika_faizi_karar", pd.DataFrame(rows, columns=["tarih", "deger"]), meta, {
        "ad": "TCMB politika faizi (1 hafta repo), karar yürürlük tarihleri; adım serisi",
        "kaynak": "TCMB 1 hafta repo faizi sayfası", "url": p["source"], "birim": "oran", "siklik": "karar bazlı",
        "alinma": p["fetched_at"], "toplayan": "Gamze Akkurt (TCMB sayfasından)",
        "not": "Her satır o tarihten itibaren geçerli oran; aradaki günler bir önceki satırın değerini taşır."})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="01-01-2021")
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--only", default="", help="virgülle ayrılmış seri id'leri; boşsa hepsi (türetilmiş seriler yalnızca hepsi çekilirken)")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    start = datetime.strptime(a.start, "%d-%m-%Y").date()
    key = os.getenv("EVDS_API_KEY")
    if not key:
        sys.exit("EVDS_API_KEY yok (backend/.env)")
    OUT.mkdir(parents=True, exist_ok=True)
    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    base = evds.series_config()["base_url"]
    for sid, (code, name, unit, freq, mult, pct) in SERIES.items():
        if only and sid not in only:
            continue
        info = {"ad": name, "kaynak": f"TCMB EVDS ({code})", "seri_kodu": code, "url": base, "birim": unit, "siklik": freq,
                "alinma": now_iso(), "baslangic_istegi": str(start),
                "donusum": "; ".join(x for x in ("yüzde → oran (÷100)" if pct else "", f"× {mult}" if mult != 1 else "") if x) or None}
        try:
            pts = fetch(code, start, key, a.pause)
            df = pd.DataFrame(pts, columns=["tarih", "deger"])
            df["deger"] = df["deger"] * mult / (100 if pct else 1)
            write(sid, df, meta, info)
            print(f"{sid:22} {meta[sid]['satir']:>5} satır  {meta[sid]['ilk']} → {meta[sid]['son']}", flush=True)
        except Exception as exc:
            meta[sid] = {**info, "status": "missing", "hata": f"{type(exc).__name__}: {exc}"}
            print(f"{sid:22} HATA {type(exc).__name__}: {exc}", flush=True)
    if only:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    # Türetilmiş: TÜFE yıllık değişim (endeksten; 12 ay önceki endeks gerektiği için 2020 verisi de istenir)
    try:
        idx = pd.DataFrame(fetch("TP.GENENDEKS.T1", date(start.year - 1, 1, 1), key, a.pause), columns=["tarih", "endeks"])
        idx = idx.drop_duplicates("tarih").sort_values("tarih").set_index("tarih")["endeks"]
        yoy = (idx / idx.shift(12) - 1).dropna()
        yoy = yoy[yoy.index >= start]
        write("tufe_yillik", pd.DataFrame({"tarih": yoy.index, "deger": yoy.values}), meta, {
            "ad": "TÜFE yıllık değişim (genel endeks 2003=100'den hesaplandı)", "kaynak": "TCMB EVDS (TP.GENENDEKS.T1) + hesaplama",
            "seri_kodu": "TP.GENENDEKS.T1", "url": base, "birim": "oran", "siklik": "aylık", "alinma": now_iso(),
            "donusum": "endeks_t / endeks_(t-12) − 1"})
        print(f"{'tufe_yillik':22} {meta['tufe_yillik']['satir']:>5} satır  {meta['tufe_yillik']['ilk']} → {meta['tufe_yillik']['son']}")
    except Exception as exc:
        print("tufe_yillik HATA", exc)
    policy_decisions(start, meta)
    if "politika_faizi_karar" in meta:
        m = meta["politika_faizi_karar"]
        print(f"{'politika_faizi_karar':22} {m['satir']:>5} satır  {m['ilk']} → {m['son']}")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
