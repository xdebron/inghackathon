"""DİBS ihale sonuçları: Hazine ve Maliye Bakanlığı "Kamu Finansmanı" duyuruları (sitenin portal API'si) ve her duyurudaki
resmi ihale sonucu PDF'i. Ham PDF'ler kanıt olarak data/raw/hmb_ihale altında saklanır; önceden indirilen PDF'e tekrar
istek atılmaz. Ayrıştırılamayan sayfa hata olarak kaydedilir, değer uydurulmaz.

Yazılanlar: ham_tablo 'dibs_ihaleler' (ihale × senet) ve seriler dibs_tl_sabit_<bant>, dibs_tufe_reel_<bant>
(ihale tarihli ortalama yıllık bileşik faiz; aynı gün aynı bantta birden çok ihale: satış ağırlıklı ortalama).
Not: ihale getirisi birincil piyasa (ihraç) getirisidir; ikincil piyasa getiri eğrisi değildir.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

from ..config import DATA_DIR
from ..db import store
from .base import Polite, write_series

API = "https://www.hmb.gov.tr/portal/v2/posts"
RAW = DATA_DIR / "raw" / "hmb_ihale"
BANDS = [("1y", 0, 1.5), ("2y", 1.5, 3.5), ("5y", 3.5, 7.5), ("10y", 7.5, 99)]
SOURCE = "Hazine ve Maliye Bakanlığı ihale sonucu duyuruları (PDF)"
SOURCE_URL = "https://www.hmb.gov.tr/kategori/kamu-finansmani"


def _num(s: str | None) -> float | None:
    if not s or s.strip() in ("-", ""):
        return None
    return float(s.strip().replace(".", "").replace(",", "."))


def _d(s: str | None) -> date | None:
    return datetime.strptime(s, "%d.%m.%Y").date() if s else None


def classify(name: str) -> str:
    n = name.lower()
    for keys, kind in ((("tüfe", "tufe"), "tufe_endeksli"), (("tlref",), "tlref_endeksli"), (("değişken", "degisken"), "degisken_faizli"),
                       (("kira sertifika",), "kira_sertifikasi"), (("altın", "altin"), "altin"),
                       (("abd doları", "dolar", "avro", "euro"), "doviz"), (("kuponsuz", "bono"), "tl_sabit_kuponsuz"),
                       (("sabit kuponlu",), "tl_sabit_kuponlu")):
        if any(k in n for k in keys):
            return kind
    return "diger"


def parse_page(text: str) -> dict | None:
    def g(pat):
        m = re.search(pat, text)
        return m.group(1).strip() if m else None
    isin = g(r"ISIN Kodu\s*:\s*([A-Z0-9]{12})")
    if not isin:
        return None

    def pair(label):
        m = re.search(label + r"\s*:\s*([\d.,-]+)\s+([\d.,-]+)", text)
        return (_num(m.group(1)), _num(m.group(2))) if m else (None, None)
    name = g(r"Senet Tanımı\s*:\s*(.+?)\s+(?:Ortalama|En |$)") or g(r"Senet Tanımı\s*:\s*(.+)")
    total = re.search(r"\n\s*Toplam\s*:\s*([\d.\-]+)\s+([\d.\-]+)", text)
    rec = {"ihale_no": g(r"İhale No\s*:\s*(\d+)"), "isin": isin, "senet": name, "tur": classify(name or ""),
           "ihale_tarihi": _d(g(r"İhale Tarihi\s*:\s*(\d{2}\.\d{2}\.\d{4})")),
           "valor": _d(g(r"İhraç \(Valör\) Tarihi\s*:\s*(\d{2}\.\d{2}\.\d{4})")),
           "vade": _d(g(r"Vade Tarihi\s*:\s*(\d{2}\.\d{2}\.\d{4})")),
           "bilesik_faiz": pair(r"Ortalama Yıllık Bileşik")[1], "basit_faiz": pair(r"Ortalama Yıllık Basit")[1],
           "ortalama_fiyat": pair(r"Ortalama Fiyat")[1], "satis_milyon_tl": _num(total.group(2)) if total else None}
    if rec["valor"] and rec["vade"]:
        rec["vade_yil"] = round((rec["vade"] - rec["valor"]).days / 365.25, 3)
    return rec


def _list_pdfs(cli: Polite, after: str) -> list[tuple[str, str, str]]:
    out, page = [], 1
    while True:
        r = cli.get(API, params={"search": "ihale", "after": f"{after}T00:00:00", "per_page": 100, "page": page,
                                 "_fields": "id,date,title,content"})
        if r is None:
            raise RuntimeError("Hazine duyuru listesi alınamadı")
        for p in r.json():
            for u in re.findall(r'href="([^"]+\.pdf)"', p["content"]["rendered"]):
                if "ihale_sonuc" in u.lower():
                    out.append((p["date"][:10], re.sub(r"<[^>]+>", "", p["title"]["rendered"]), u))
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return sorted(set(out))


def run(after: str = "2021-01-01", pause: float = 1.5, log=print) -> dict:
    from pypdf import PdfReader
    RAW.mkdir(parents=True, exist_ok=True)
    cli = Polite(pause=pause)
    with store.Run("hazine_dibs") as run_:
        pdfs = _list_pdfs(cli, after)
        log(f"  {len(pdfs)} ihale sonucu PDF'i")
        rows, errors = [], []
        for post_date, _title, url in pdfs:
            f = RAW / url.rsplit("/", 1)[1]
            if not f.exists():
                r = cli.get(url)
                if r is None:
                    errors.append({"url": url, "hata": "indirilemedi"})
                    continue
                f.write_bytes(r.content)
            try:
                for pg in PdfReader(f).pages:
                    rec = parse_page(pg.extract_text() or "")
                    if rec:
                        rows.append({**rec, "duyuru_tarihi": post_date, "kaynak_pdf": url})
            except Exception as exc:
                errors.append({"url": url, "hata": f"ayristirma_hatasi: {type(exc).__name__}"})
        df = pd.DataFrame(rows).drop_duplicates(["ihale_no", "isin"]).sort_values(["ihale_tarihi", "ihale_no", "isin"])
        store.save_raw_table("dibs_ihaleler", df.astype({c: str for c in ("ihale_tarihi", "valor", "vade")}), run_)
        base = {"kaynak": SOURCE, "url": SOURCE_URL, "alinma": store.now_iso(), "siklik": "ihale tarihleri", "birim": "oran",
                "not": "Birincil piyasa (ihraç) ortalama yıllık bileşik faizi, gerçekleşme; ikincil piyasa getirisi değildir."}
        for prefix, kinds, label in (("dibs_tl_sabit", ("tl_sabit_kuponsuz", "tl_sabit_kuponlu"), "sabit faizli nominal TL"),
                                     ("dibs_tufe_reel", ("tufe_endeksli",), "TÜFE'ye endeksli (reel)")):
            sub = df[df["tur"].isin(kinds) & df["bilesik_faiz"].notna() & df["vade_yil"].notna()]
            for band, lo, hi in BANDS:
                b = sub[(sub["vade_yil"] >= lo) & (sub["vade_yil"] < hi)]
                w = b.assign(w=b["satis_milyon_tl"].fillna(1.0), fw=b["bilesik_faiz"] * b["satis_milyon_tl"].fillna(1.0))
                g = w.groupby("ihale_tarihi")[["fw", "w"]].sum()
                out = pd.DataFrame({"tarih": [str(x) for x in g.index], "deger": (g["fw"] / g["w"] / 100).values})
                m = write_series(f"{prefix}_{band}", out, {**base, "ad": f"DİBS ihale getirisi, {label}, vade bandı {band} ({lo}-{hi} yıl)",
                                                          "donusum": "yüzde → oran; aynı gün birden çok ihale: satış ağırlıklı ortalama"}, run_)
                log(f"  {prefix}_{band:4} {m['satir']:>4} ihale günü  {m['ilk']} → {m['son']}")
        run_.durum = "kismi" if errors else "basarili"
        run_.mesaj = f"{len(errors)} PDF hatası" if errors else None
    cli.close()
    return {"senet_satiri": len(df), "pdf": len(pdfs), "hata": len(errors)}
