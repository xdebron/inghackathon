"""BIST 30 endeksi ve bileşen hisseleri: günlük fiyat tarihçesi (2021 → bugün).

KAYNAK UYARISI: Yahoo Finance chart API (query1.finance.yahoo.com/v8/finance/chart). Resmi Borsa İstanbul kaynağı
değildir ve Yahoo'nun kullanım koşulları otomatik çekime izin vermez. Kullanıcı kararıyla kullanılıyor; her dosya ve
meta kaydı bu etiketi taşır. BIST 100 de aynı kaynaktan çekilir ve resmi EVDS serisiyle (gecmis/bist100_fiyat.csv)
karşılaştırılarak kaynağın doğruluğu ölçülür.

BİLEŞİM UYARISI: Liste, Borsa İstanbul'un resmi tam listesine erişilemediği için bir haber kaynağından (09.09.2026
itibarıyla ekranlarda izlenen BIST 30) alınmıştır; 'teyit edilmedi' olarak işaretlenir. Mevcut bileşim 2021'e geriye
uygulanır (hayatta kalma yanlılığı: o tarihte endekste olmayan hisseler de dahil edilir).

Çıktı: backend/data/snapshots/gecmis/bist/<KOD>.csv  (tarih, kapanis, duzeltilmis_kapanis, hacim)  +  bist/meta.json
  kapanis             : Yahoo 'close'; bölünme/bedelsiz sermaye artırımına göre düzeltilmiş
  duzeltilmis_kapanis : Yahoo 'adjclose'; bölünme + temettüye göre düzeltilmiş (toplam getiri için bunu kullanın)
  Bugünkü (seans içi olabilecek) satır yazılmaz.
Kullanım: backend/.venv/Scripts/python backend/tools/fetch_bist.py [--start 2021-01-01] [--pause 2.0]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import SNAPSHOT_DIR, now_iso  # noqa: E402

OUT = SNAPSHOT_DIR / "gecmis" / "bist"
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
    "notlar": [
        "Borsa İstanbul'un resmi tam bileşen listesine erişilemedi; duyurular yalnızca giren/çıkan payları veriyor.",
        "1 Ekim-31 Aralık 2026 dönemi için haberlere göre Destek Finans Faktoring (DSTKF) çıkıyor, TR Anadolu Metal "
        "Madencilik giriyor; giren şirketin borsa kodu doğrulanamadığı için liste değiştirilmedi. RM/ekip teyit etmeli.",
        "Borsa İstanbul'un 19.06.2026 duyurusu (1 Temmuz-30 Eylül) BIST 30'a PASEU, HALKB, ULKER girişini gösteriyor; "
        "haber listesiyle çelişiyor, teyit edilmeli: https://www.borsaistanbul.com/en/announcement/15483/bist-stock-indices-periodic-review",
        "Güncel bileşim 2021'e geriye uygulanır (hayatta kalma yanlılığı).",
    ],
}
INDICES = {"XU030": "BIST 30 fiyat endeksi", "XU100": "BIST 100 fiyat endeksi (EVDS ile doğrulama için)"}


def fetch(sym: str, start: datetime, timeout: float = 30) -> tuple[pd.DataFrame, dict]:
    p1 = int(start.replace(tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.now(timezone.utc).timestamp())
    r = httpx.get(URL.format(sym=sym), params={"period1": p1, "period2": p2, "interval": "1d", "events": "div,split"},
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("Europe/Istanbul").tz_localize(None).normalize()
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    df = pd.DataFrame({"tarih": ts.date, "kapanis": q["close"], "duzeltilmis_kapanis": adj if adj else q["close"],
                       "hacim": q.get("volume")})
    df = df.dropna(subset=["kapanis"])
    df = df[df["kapanis"] > 0].drop_duplicates("tarih", keep="last").sort_values("tarih")
    df = df[df["tarih"] < datetime.now().date()]  # bugünkü satır seans içi fiyat olabilir: yalnızca kesinleşmiş günler
    ev = res.get("events", {})
    events = {
        "temettu": [{"tarih": str(pd.to_datetime(int(k), unit="s").date()), "tutar": v["amount"]} for k, v in ev.get("dividends", {}).items()],
        "bolunme": [{"tarih": str(pd.to_datetime(int(k), unit="s").date()), "oran": f"{v['numerator']}:{v['denominator']}"}
                    for k, v in ev.get("splits", {}).items()],
    }
    return df, {"para_birimi": res["meta"].get("currency"), "borsa": res["meta"].get("exchangeName"),
                "ad": res["meta"].get("longName") or res["meta"].get("shortName"), **events}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--pause", type=float, default=2.0)
    ap.add_argument("--group", choices=["bist30", "byf"], default="bist30",
                    help="byf: KAP'ın resmi BYF listesi (gecmis/byf/kap_byf_listesi.json), çıktı gecmis/byf/")
    a = ap.parse_args()
    start = datetime.fromisoformat(a.start)
    global OUT
    if a.group == "byf":
        OUT = SNAPSHOT_DIR / "gecmis" / "byf"
        lst = json.loads((OUT / "kap_byf_listesi.json").read_text(encoding="utf-8"))
        codes = [f["kod"] for f in lst["fonlar"]]
        composition = {"kodlar": codes, "kaynak": lst["kaynak"], "url": lst["url"], "alinma": lst["alinma"],
                       "durum": "resmi liste (KAP, yürürlükteki BYF'ler)"}
        indices = {}
    else:
        codes, composition, indices = BIST30, COMPOSITION, INDICES
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"kaynak": SOURCE, "url": URL.format(sym="<KOD>.IS"), "alinma": now_iso(), "baslangic_istegi": a.start,
            "bilesim": composition, "seriler": {}}
    for code in list(indices) + codes:
        try:
            df, info = fetch(f"{code}.IS", start)
            df.to_csv(OUT / f"{code}.csv", index=False)
            meta["seriler"][code] = {"tur": "endeks" if code in indices else ("byf" if a.group == "byf" else "hisse"), "aciklama": indices.get(code), **info,
                                     "satir": int(len(df)), "ilk": str(df["tarih"].min()), "son": str(df["tarih"].max()),
                                     "status": "ok"}
            print(f"{code:6} {len(df):>5} gün  {df['tarih'].min()} → {df['tarih'].max()}  "
                  f"temettü {len(info['temettu'])}, bölünme {len(info['bolunme'])}", flush=True)
        except Exception as exc:
            meta["seriler"][code] = {"status": "missing", "hata": f"{type(exc).__name__}: {exc}"}
            print(f"{code:6} HATA {type(exc).__name__}: {exc}", flush=True)
        time.sleep(a.pause)
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
