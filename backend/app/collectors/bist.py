"""BIST 30 hisseleri ve endeksleri: Yahoo Finance chart API (resmi Borsa İstanbul kaynağı değil; notta teyit maddesi).
Günlük kapanış, temettü ve bölünme düzeltmeli kapanış, hacim. Bileşim listesi teyit edilmemiş bir derlemedir; RM'e gösterilir.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from ..db import store
from .base import Polite, write_series

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
SOURCE = "Yahoo Finance chart API: resmi Borsa İstanbul kaynağı değil"
BIST30 = ["AEFES", "AKBNK", "ASELS", "ASTOR", "BIMAS", "DSTKF", "EKGYO", "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF",
          "ISCTR", "KCHOL", "KRDMD", "MGROS", "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO",
          "TOASO", "TRALT", "TTKOM", "TUPRS", "VAKBN", "YKBNK"]
COMPOSITION = {
    "kodlar": BIST30,
    "kaynak": "Haber derlemesi: 09.09.2026 itibarıyla piyasa ekranlarında BIST 30 kapsamında izlenen hisseler",
    "url": "https://haberkenti.com/bist-30-hisseleri-hangileri-2026-guncel-bist-30-listesi-belli-oldu",
    "durum": "teyit edilmedi",
    "notlar": ["Borsa İstanbul'un resmi tam bileşen listesine erişilemedi; duyurular yalnızca giren/çıkan payları veriyor.",
               "Güncel bileşim 2021'e geriye uygulanır (hayatta kalma yanlılığı)."],
}
INDICES = {"XU030": "BIST 30 fiyat endeksi", "XU100": "BIST 100 fiyat endeksi (EVDS ile doğrulama için)"}


def _fetch(cli: Polite, sym: str, start: datetime) -> tuple[pd.DataFrame, dict]:
    p1 = int(start.replace(tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.now(timezone.utc).timestamp())
    r = cli.get(URL.format(sym=sym), params={"period1": p1, "period2": p2, "interval": "1d", "events": "div,split"})
    if r is None:
        raise RuntimeError(f"yanıt yok ({sym})")
    res = r.json()["chart"]["result"][0]
    ts = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("Europe/Istanbul").tz_localize(None).normalize()
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    df = pd.DataFrame({"tarih": ts.date, "kapanis": q["close"], "duzeltilmis_kapanis": adj if adj else q["close"], "hacim": q.get("volume")})
    df = df.dropna(subset=["kapanis"])
    df = df[df["kapanis"] > 0].drop_duplicates("tarih", keep="last").sort_values("tarih")
    df = df[df["tarih"] < datetime.now().date()]            # seans içi fiyat değil, kesinleşmiş günler
    df["tarih"] = df["tarih"].astype(str)
    ev = res.get("events", {})
    info = {"para_birimi": res["meta"].get("currency"), "borsa": res["meta"].get("exchangeName"),
            "ad": res["meta"].get("longName") or res["meta"].get("shortName"),
            "temettu": [{"tarih": str(pd.to_datetime(int(k), unit="s").date()), "tutar": v["amount"]} for k, v in ev.get("dividends", {}).items()],
            "bolunme": [{"tarih": str(pd.to_datetime(int(k), unit="s").date()), "oran": f"{v['numerator']}:{v['denominator']}"}
                        for k, v in ev.get("splits", {}).items()]}
    return df, info


def run(start: str = "2021-01-01", pause: float = 2.0, log=print) -> dict:
    cli = Polite(pause=pause)
    s0 = datetime.fromisoformat(start)
    meta = {"kaynak": SOURCE, "url": URL.format(sym="<KOD>.IS"), "alinma": store.now_iso(), "baslangic_istegi": start,
            "bilesim": COMPOSITION, "seriler": {}}
    bad = []
    with store.Run("bist") as run_:
        for code in list(INDICES) + BIST30:
            try:
                df, info = _fetch(cli, f"{code}.IS", s0)
                m = write_series(f"bist/{code}", df, {"tur": "endeks" if code in INDICES else "hisse", "aciklama": INDICES.get(code),
                                                      "kaynak": SOURCE, "url": URL.format(sym=f"{code}.IS"), "alinma": store.now_iso(), **info},
                                 run_, grup="bist")
                meta["seriler"][code] = m
                log(f"  {code:6} {m['satir']:>5} gün  {m['ilk']} → {m['son']}")
            except Exception as exc:
                bad.append(f"{code}: {exc}")
                meta["seriler"][code] = {"status": "missing", "hata": f"{type(exc).__name__}: {exc}"}
        store.save_document("bist_meta", meta, run_)
        run_.durum = "kismi" if bad else "basarili"
        run_.mesaj = "; ".join(bad) or None
    cli.close()
    return {"seri": len(meta["seriler"]) - len(bad), "hata": bad}
