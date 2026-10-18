"""DİBS (Devlet İç Borçlanma Senedi) getirileri: Hazine ve Maliye Bakanlığı ihale sonuçları, 2021 → bugün.

Kaynak: hmb.gov.tr "Kamu Finansmanı" duyuruları (sitenin kendi portal API'si /portal/v2/posts) ve her duyurudaki
resmi ihale sonucu PDF'i (ms.hmb.gov.tr/uploads/...). Ham PDF'ler kanıt olarak saklanır. İstekler sıralı ve beklemeli.

Çıktılar (backend/data/snapshots/gecmis/):
  dibs_ihaleler.csv      ihale × senet: tarih, ihale_no, isin, senet, tur, valor, vade, vade_yil, bilesik_faiz,
                         basit_faiz, ortalama_fiyat, satis_milyon_tl, kaynak_pdf
  dibs_tl_sabit_<bant>.csv  sabit faizli nominal TL senetler (kuponsuz, sabit kuponlu, bono) için ihale tarihli
                         ortalama yıllık bileşik faiz; bant = vadeye kalan süre: 1y (<1,5), 2y (1,5-3,5), 5y (3,5-7,5), 10y (>7,5)
  dibs_tufe_reel_<bant>.csv  TÜFE'ye endeksli senetlerin reel bileşik faizi (aynı bantlar)
Ham PDF: backend/data/raw/hmb_ihale/<dosya>.pdf

Not: ihale getirisi birincil piyasa (ihraç) getirisidir; ikincil piyasa getiri eğrisi değildir ve günlük değil,
ihale yapılan günlerde gözlenir. Değer uydurulmaz; ayrıştırılamayan sayfa 'ayristirma_hatasi' olarak kaydedilir.

Kullanım: backend/.venv/Scripts/python backend/tools/fetch_dibs.py [--after 2021-01-01] [--pause 1.5]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import SNAPSHOT_DIR, now_iso  # noqa: E402

API = "https://www.hmb.gov.tr/portal/v2/posts"
RAW = ROOT / "backend" / "data" / "raw" / "hmb_ihale"
OUT = SNAPSHOT_DIR / "gecmis"
UA = {"User-Agent": "Mozilla/5.0"}
BANDS = [("1y", 0, 1.5), ("2y", 1.5, 3.5), ("5y", 3.5, 7.5), ("10y", 7.5, 99)]


def list_pdfs(after: str, pause: float) -> list[tuple[str, str, str]]:
    """(duyuru tarihi, duyuru başlığı, pdf url) listesi."""
    out, page = [], 1
    while True:
        r = httpx.get(API, params={"search": "ihale", "after": f"{after}T00:00:00", "per_page": 100, "page": page,
                                   "_fields": "id,date,title,content"}, headers=UA, timeout=60)
        r.raise_for_status()
        for p in r.json():
            for u in re.findall(r'href="([^"]+\.pdf)"', p["content"]["rendered"]):
                if "ihale_sonuc" in u.lower():
                    out.append((p["date"][:10], re.sub(r"<[^>]+>", "", p["title"]["rendered"]), u))
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
        time.sleep(pause)
    return sorted(set(out))


def _num(s: str | None) -> float | None:
    if not s or s.strip() in ("-", ""):
        return None
    return float(s.strip().replace(".", "").replace(",", "."))


def _d(s: str | None) -> date | None:
    return datetime.strptime(s, "%d.%m.%Y").date() if s else None


def classify(name: str) -> str:
    n = name.lower()
    if "tüfe" in n or "tufe" in n:
        return "tufe_endeksli"
    if "tlref" in n:
        return "tlref_endeksli"
    if "değişken" in n or "degisken" in n:
        return "degisken_faizli"
    if "kira sertifika" in n:
        return "kira_sertifikasi"
    if "altın" in n or "altin" in n:
        return "altin"
    if any(c in n for c in ("abd doları", "dolar", "avro", "euro")):
        return "doviz"
    if "kuponsuz" in n or "bono" in n:
        return "tl_sabit_kuponsuz"
    if "sabit kuponlu" in n:
        return "tl_sabit_kuponlu"
    return "diger"


def parse_page(text: str) -> dict | None:
    g = lambda pat: (m.group(1).strip() if (m := re.search(pat, text)) else None)
    isin = g(r"ISIN Kodu\s*:\s*([A-Z0-9]{12})")
    if not isin:
        return None
    # "Ortalama Yıllık Bileşik : <teklif> <gerçekleşme>" -> gerçekleşme (ikinci sayı)
    def pair(label):
        m = re.search(label + r"\s*:\s*([\d.,-]+)\s+([\d.,-]+)", text)
        return (_num(m.group(1)), _num(m.group(2))) if m else (None, None)
    name = g(r"Senet Tanımı\s*:\s*(.+?)\s+(?:Ortalama|En |$)") or g(r"Senet Tanımı\s*:\s*(.+)")
    total = re.search(r"\n\s*Toplam\s*:\s*([\d.\-]+)\s+([\d.\-]+)", text)
    rec = {
        "ihale_no": g(r"İhale No\s*:\s*(\d+)"), "isin": isin, "senet": name, "tur": classify(name or ""),
        "ihale_tarihi": _d(g(r"İhale Tarihi\s*:\s*(\d{2}\.\d{2}\.\d{4})")),
        "valor": _d(g(r"İhraç \(Valör\) Tarihi\s*:\s*(\d{2}\.\d{2}\.\d{4})")),
        "vade": _d(g(r"Vade Tarihi\s*:\s*(\d{2}\.\d{2}\.\d{4})")),
        "bilesik_faiz": pair(r"Ortalama Yıllık Bileşik")[1], "basit_faiz": pair(r"Ortalama Yıllık Basit")[1],
        "ortalama_fiyat": pair(r"Ortalama Fiyat")[1],
        "satis_milyon_tl": _num(total.group(2)) if total else None,
    }
    if rec["valor"] and rec["vade"]:
        rec["vade_yil"] = round((rec["vade"] - rec["valor"]).days / 365.25, 3)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--after", default="2021-01-01")
    ap.add_argument("--pause", type=float, default=1.5)
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    pdfs = list_pdfs(a.after, a.pause)
    print(f"{len(pdfs)} ihale sonucu PDF'i", flush=True)
    rows, errors = [], []
    for i, (post_date, title, url) in enumerate(pdfs, 1):
        f = RAW / url.rsplit("/", 1)[1]
        if not f.exists():  # önceden indirilmişse tekrar istek atma
            r = httpx.get(url, headers=UA, timeout=60)
            if r.status_code != 200:
                errors.append({"url": url, "hata": f"HTTP {r.status_code}"})
                time.sleep(a.pause)
                continue
            f.write_bytes(r.content)
            time.sleep(a.pause)
        try:
            for pg in PdfReader(f).pages:
                rec = parse_page(pg.extract_text() or "")
                if rec:
                    rows.append({**rec, "duyuru_tarihi": post_date, "kaynak_pdf": url})
        except Exception as exc:
            errors.append({"url": url, "hata": f"ayristirma_hatasi: {type(exc).__name__}: {exc}"})
        if i % 25 == 0:
            print(f"{i}/{len(pdfs)}", flush=True)
    df = pd.DataFrame(rows).drop_duplicates(["ihale_no", "isin"]).sort_values(["ihale_tarihi", "ihale_no", "isin"])
    df.to_csv(OUT / "dibs_ihaleler.csv", index=False)
    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    base = {"kaynak": "Hazine ve Maliye Bakanlığı ihale sonucu duyuruları (PDF)", "url": "https://www.hmb.gov.tr/kategori/kamu-finansmani",
            "alinma": now_iso(), "siklik": "ihale tarihleri", "birim": "oran",
            "not": "Birincil piyasa (ihraç) ortalama yıllık bileşik faizi, gerçekleşme; ikincil piyasa getirisi değildir."}
    meta["dibs_ihaleler"] = {**base, "ad": "DİBS ihale sonuçları (ihale × senet)", "satir": int(len(df)),
                             "ilk": str(df["ihale_tarihi"].min()), "son": str(df["ihale_tarihi"].max()), "status": "ok",
                             "pdf_sayisi": len(pdfs), "hatalar": errors, "tur_dagilimi": df["tur"].value_counts().to_dict()}
    for prefix, kinds, label in (("dibs_tl_sabit", ("tl_sabit_kuponsuz", "tl_sabit_kuponlu"), "sabit faizli nominal TL"),
                                 ("dibs_tufe_reel", ("tufe_endeksli",), "TÜFE'ye endeksli (reel)")):
        sub = df[df["tur"].isin(kinds) & df["bilesik_faiz"].notna() & df["vade_yil"].notna()]
        for band, lo, hi in BANDS:
            b = sub[(sub["vade_yil"] >= lo) & (sub["vade_yil"] < hi)]
            # Aynı gün aynı bantta birden çok ihale: satış miktarıyla ağırlıklı ortalama
            w = b.assign(w=b["satis_milyon_tl"].fillna(1.0), fw=b["bilesik_faiz"] * b["satis_milyon_tl"].fillna(1.0))
            g = w.groupby("ihale_tarihi")[["fw", "w"]].sum()
            out = pd.DataFrame({"tarih": g.index, "deger": (g["fw"] / g["w"] / 100).values})
            sid = f"{prefix}_{band}"
            out.to_csv(OUT / f"{sid}.csv", index=False)
            meta[sid] = {**base, "ad": f"DİBS ihale getirisi, {label}, vade bandı {band} ({lo}-{hi} yıl)", "satir": int(len(out)),
                         "ilk": str(out["tarih"].min()) if len(out) else None, "son": str(out["tarih"].max()) if len(out) else None,
                         "status": "ok" if len(out) else "missing", "donusum": "yüzde → oran; aynı gün birden çok ihale: satış ağırlıklı ortalama"}
            print(f"{sid:22} {len(out):>4} ihale günü  {meta[sid]['ilk']} → {meta[sid]['son']}", flush=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"toplam {len(df)} senet satırı; hata {len(errors)}", flush=True)


if __name__ == "__main__":
    main()
