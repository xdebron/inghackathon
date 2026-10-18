"""KAP fon bildirim geçmişi (2021 → bugün) ve Yatırımcı Bilgi Formu'ndan (YBF) RD / ücret tarihçesi.

Kaynak: KAP'ın kendi bildirim sorgu API'si (www.kap.org.tr/tr/api/disclosure/funds/byCriteria), bildirim detay
sayfası (/tr/Bildirim/<index>) ve ek indirme ucu (/tr/api/file/download/<oid>). İstekler sıralı ve beklemeli; 429'da
sunucunun söylediği kadar (yoksa artan sürede) beklenir, art arda 5 hatada durulur. İndirilen dosyalar önbelleğe
alınır: komut tekrar çalıştırıldığında kaldığı yerden devam eder.

Adımlar:
  list   1026 fonun YBF / İzahname / İzahname (Değişiklik) / Toplam Gider Oranı bildirimleri, aylık pencerelerle
         -> snapshots/gecmis/kap/kap_bildirimler.csv
  ybf    ING fon listesindeki (snapshots/ing_fon_listesi.json) fonların YBF bildirimleri: detay sayfası + PDF ekleri
         -> data/raw/kap_ybf/<index>.html, <index>_<n>.pdf
  parse  PDF'lerden risk değeri (RD), yıllık yönetim ücreti ve yıllık azami toplam gider oranı
         -> snapshots/gecmis/kap/ybf_rd_ucret.csv (+ KAP 15.10 anlık görüntüsüyle doğrulama)

Kullanım: backend/.venv/Scripts/python backend/tools/fetch_kap_history.py list|ybf|parse [--pause 2.5]
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import SNAPSHOT_DIR, now_iso  # noqa: E402

BASE = "https://www.kap.org.tr"
OUT = SNAPSHOT_DIR / "gecmis" / "kap"
RAW = ROOT / "backend" / "data" / "raw" / "kap_ybf"
REGISTRY = ROOT / "gamze_work" / "AGENTIC AI HACKATHON-ING" / "data" / "raw" / "kap_yatirim_fonlari_20261009T190450Z.html.gz"
SUBJECTS = {
    "Yatırımcı Bilgi Formu": "8aca490d51153da6015115455c5f00b9",
    "İzahname": "8aca490d502dd03b01502deb982000e2",
    "İzahname (Değişiklik)": "8aca490d51153da601511548b25900fb",
    "Fon Toplam Gider Oranı ve Fon Toplam Giderinin Dağılımı": "4028328d537aad0a015383d09b184552",
}
CAP = 2000  # API tek yanıtta en çok 2000 kayıt döndürüyor
H = {"User-Agent": "Mozilla/5.0", "Accept-Language": "tr", "Content-Type": "application/json"}


class Polite:
    """Sıralı, beklemeli istemci: 429'da geri çekilir, art arda hatada durur."""

    def __init__(self, pause: float, max_consecutive: int = 5):
        self.c = httpx.Client(headers=H, timeout=60, follow_redirects=True)
        self.pause, self.max_consecutive, self.errors = pause, max_consecutive, 0

    def request(self, method: str, url: str, **kw) -> httpx.Response | None:
        for attempt in range(1, 5):
            try:
                r = self.c.request(method, url, **kw)
            except httpx.HTTPError as exc:
                r, err = None, f"{type(exc).__name__}"
            else:
                err = None if r.status_code < 400 else f"HTTP {r.status_code}"
            time.sleep(self.pause)
            if r is not None and r.status_code == 429:
                wait = float(r.headers.get("Retry-After") or 0) or 60 * attempt
                print(f"  429: {wait:.0f} sn bekleniyor", flush=True)
                time.sleep(wait)
                continue
            if err is None:
                self.errors = 0
                return r
            if r is not None and r.status_code < 500:
                print(f"  {err} {url}", flush=True)
                break  # 4xx: tekrar denemenin anlamı yok
            # bağlantı kopması / 5xx: sunucu kısıtlıyor olabilir, artan sürede geri çekil
            wait = 60 * attempt
            print(f"  {err}: {wait} sn geri çekiliniyor ({url.rsplit('/', 1)[-1]})", flush=True)
            time.sleep(wait)
            self.c.close()
            self.c = httpx.Client(headers=H, timeout=60, follow_redirects=True)
        self.errors += 1
        if self.errors >= self.max_consecutive:
            raise SystemExit(f"{self.errors} ardışık hata: DURULDU (sunucu zorlanmıyor). Aynı komutla devam edilebilir.")
        return None


def registry() -> dict[str, dict]:
    h = gzip.open(REGISTRY, "rt", encoding="utf-8").read().replace('\\"', '"')
    reg = {}
    for o in re.findall(r'\{"umbFundName":.*?"nonInactiveCount":\d+\}', h):
        j = json.loads(o)
        reg.setdefault(j["fundCode"], j)
    return reg


LIST_CACHE = ROOT / "backend" / "data" / "raw" / "kap_liste"


def query(cli: Polite, a: date, b: date, subject_oid: str) -> list[dict] | None:
    """Pencere sorgusu; başarılı yanıt diske yazılır, tekrar çalıştırmada istek atılmaz."""
    LIST_CACHE.mkdir(parents=True, exist_ok=True)
    cache = LIST_CACHE / f"{subject_oid[-8:]}_{a}_{b}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    body = {"fromDate": a.isoformat(), "toDate": b.isoformat(), "fundTypeList": ["YF"], "mkkMemberOidList": [],
            "fundOidList": [], "passiveFundOidList": [], "disclosureClass": "", "isLate": "",
            "subjectList": [subject_oid], "discIndex": [], "fromSrc": False, "srcCategory": ""}
    r = cli.request("POST", f"{BASE}/tr/api/disclosure/funds/byCriteria", json=body)
    if r is None:
        return None
    d = r.json()
    if not isinstance(d, list):
        print(f"  beklenmeyen yanıt {a}..{b}: {str(d)[:100]}", flush=True)
        return None
    cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def months(start: date, end: date):
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt - timedelta(days=1), end)
        cur = nxt


def step_list(cli: Polite, start: date):
    OUT.mkdir(parents=True, exist_ok=True)
    rows, gaps = [], []

    def fetch(a, b, name, oid):
        d = query(cli, a, b, oid)
        if d is None:
            gaps.append({"konu": name, "baslangic": str(a), "bitis": str(b)})
            return
        if len(d) >= CAP and a < b:  # tavana takıldı: pencereyi ikiye böl
            mid = a + (b - a) // 2
            fetch(a, mid, name, oid)
            fetch(mid + timedelta(days=1), b, name, oid)
            return
        for x in d:
            rows.append({"yayim": x["publishDate"], "fon": x.get("fundCode"), "fon_adi": x.get("kapTitle"), "konu": name,
                         "bildirim_no": x["disclosureIndex"], "ek_sayisi": x.get("attachmentCount"), "donem": x.get("ruleType"),
                         "yil": x.get("year"), "sinif": x.get("disclosureClass"), "ozet": x.get("summary")})

    for name, oid in SUBJECTS.items():
        n0 = len(rows)
        for a, b in months(start, date.today()):
            fetch(a, b, name, oid)
        print(f"{name:58} {len(rows) - n0:>6} bildirim", flush=True)
    df = pd.DataFrame(rows).drop_duplicates("bildirim_no")
    df["yayim_tarihi"] = pd.to_datetime(df["yayim"], format="%d.%m.%Y %H:%M:%S").dt.date
    df.sort_values(["fon", "yayim_tarihi"]).to_csv(OUT / "kap_bildirimler.csv", index=False)
    meta = {"kaynak": "KAP fon bildirim sorgusu (www.kap.org.tr/tr/api/disclosure/funds/byCriteria)", "alinma": now_iso(),
            "baslangic": str(start), "konular": SUBJECTS, "satir": int(len(df)), "fon_sayisi": int(df["fon"].nunique()),
            "eksik_pencereler": gaps, "not": "Fon türü: YF (yatırım fonları). Bildirim listesi; içerik değil."}
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"toplam {len(df)} bildirim, {df['fon'].nunique()} fon; eksik pencere {len(gaps)}", flush=True)


def step_ybf(cli: Polite):
    RAW.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(OUT / "kap_bildirimler.csv")
    ing = set(json.loads((SNAPSHOT_DIR / "ing_fon_listesi.json").read_text(encoding="utf-8"))["kodlar"])
    todo = df[(df["konu"] == "Yatırımcı Bilgi Formu") & df["fon"].isin(ing)].sort_values("yayim_tarihi")
    print(f"{len(todo)} YBF bildirimi, {todo['fon'].nunique()} fon", flush=True)
    for i, idx in enumerate(todo["bildirim_no"], 1):
        page = RAW / f"{idx}.html"
        if not page.exists():
            r = cli.request("GET", f"{BASE}/tr/Bildirim/{idx}")
            if r is None:
                continue
            page.write_text(r.text, encoding="utf-8")
        oids = list(dict.fromkeys(re.findall(r"api/file/download/([0-9a-f]{32})", page.read_text(encoding="utf-8"))))[:3]
        for n, oid in enumerate(oids):
            f = RAW / f"{idx}_{n}.pdf"
            if f.exists():
                continue
            r = cli.request("GET", f"{BASE}/tr/api/file/download/{oid}")
            if r is not None:
                f.write_bytes(r.content)
        if i % 50 == 0:
            print(f"{i}/{len(todo)}", flush=True)
    print("ybf indirme tamam", flush=True)


# --- PDF ayrıştırma ---------------------------------------------------------------------------

_PCT = r"%\s?(\d{1,2}(?:[.,]\d{1,4})?)"
RD_PATTERNS = [
    re.compile(r"[“\"']\s*([1-7])\s*[”\"']\s*risk\s+de[ğg]erine", re.I),
    re.compile(r"risk\s+de[ğg]eri\w*\s*(?:olarak\s*)?[:=]?\s*[“\"']?\s*([1-7])\b(?!\s*(?:y[ıi]l|g[üu]n|ay\b|%))", re.I),
    re.compile(r"\b([1-7])\s+risk\s+de[ğg]erine\s+sahip", re.I),
]


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_text(t: str) -> dict:
    t1 = re.sub(r"\s+", " ", t)
    rd, rd_ev = None, None
    for p in RD_PATTERNS:
        m = p.search(t1)
        if m:
            rd, rd_ev = int(m.group(1)), t1[max(0, m.start() - 60):m.end() + 20]
            break
    fee, fee_ev = None, None
    # Tablo satırı: "Yönetim ücreti (yıllık) - Kurucu/Yönetici - Fon Dağıtım Kuruluşu 1,00 * * Saklama ..."
    # Değer "%" ile ya da yalnızca sayı olarak (1,00) yazılabiliyor; ilk geçiş çoğu zaman dipnot cümlesi olduğu için
    # tüm geçişler denenir. Asgari/azami paylaşım oranları (%35, %65) atlanır.
    fee_note = None
    for m in re.finditer(r"Y[öo]netim\s+[üu]creti(?:\s*\(\s*y[ıi]ll[ıi]k\s*\))?\*?", t1, re.I):
        window = t1[m.end():m.end() + 350]
        stop = re.search(r"Saklama|Di[ğg]er\s+giderler|B\)|Ge[çc]mi[şs]\s+Performans|Getiri|Fon[’']un\s+Ge[çc]mi[şs]", window, re.I)
        window = window[:stop.start()] if stop else window
        if re.search(r"Grubu", window, re.I):  # pay gruplarına göre farklı ücret: hangisi olduğu belirsiz
            fee_note = "pay grubuna göre farklı ücret; ayrıştırılmadı"
            break
        for pm in re.finditer(r"%?\s?(\d{1,2},\d{1,4})\s?%?", window):
            pre = window[max(0, pm.start() - 14):pm.start()].lower()
            if "asgari" in pre or "azami" in pre:
                continue
            v = _f(pm.group(1))
            if v < 6:  # yıllık yönetim ücreti makul aralık; paylaşım oranları (%35 vb.) ve tablo dışı sayılar değil
                fee, fee_ev = v / 100, window[max(0, pm.start() - 40):pm.end() + 10]
                break
        if fee is not None:
            break
    if fee is None and fee_note:
        fee_ev = fee_note
    m = re.search(r"Y[ıi]ll[ıi]k\s+azami\s+fon\s+toplam\s+gider\s+oran[ıi]\s*" + _PCT, t1, re.I)
    ter = _f(m.group(1)) / 100 if m else None
    return {"rd": rd, "yonetim_ucreti": fee, "azami_toplam_gider": ter, "rd_kanit": rd_ev, "ucret_kanit": fee_ev}


def step_parse():
    from pypdf import PdfReader
    logging.disable(logging.CRITICAL)
    df = pd.read_csv(OUT / "kap_bildirimler.csv")
    ybf = df[df["konu"] == "Yatırımcı Bilgi Formu"].set_index("bildirim_no")
    rows = []
    for f in sorted(RAW.glob("*_*.pdf")):
        idx, n = (int(x) for x in f.stem.split("_"))
        if idx not in ybf.index:
            continue
        try:
            text = "\n".join(p.extract_text() or "" for p in PdfReader(f).pages)
            rec = parse_text(text)
            status = "ok" if rec["rd"] is not None or rec["yonetim_ucreti"] is not None else "bulunamadi"
        except Exception as exc:
            rec, status = {"rd": None, "yonetim_ucreti": None, "azami_toplam_gider": None, "rd_kanit": None,
                           "ucret_kanit": None}, f"okunamadi: {type(exc).__name__}"
        b = ybf.loc[idx]
        rows.append({"fon": b["fon"], "yayim_tarihi": b["yayim_tarihi"], "bildirim_no": idx, "ek": n, **rec, "durum": status,
                     "kaynak": f"{BASE}/tr/Bildirim/{idx}"})
    out = pd.DataFrame(rows).sort_values(["fon", "yayim_tarihi", "bildirim_no", "ek"])
    out.to_csv(OUT / "ybf_rd_ucret.csv", index=False)
    # Doğrulama: her fonun en son YBF değeri vs KAP fon sayfası (15.10.2026 anlık görüntüsü)
    kap = json.loads((SNAPSHOT_DIR / "kap_fon_bilgileri.json").read_text(encoding="utf-8"))["fonlar"]
    last = out[out["durum"] == "ok"].groupby("fon").last()
    cmp = []
    for kod, r in last.iterrows():
        k = kap.get(kod, {})
        cmp.append({"fon": kod, "ybf_rd": r["rd"], "kap_rd": k.get("resmi_rd"),
                    "ybf_ucret": r["yonetim_ucreti"], "kap_ucret": k.get("yonetim_ucreti_yillik")})
    c = pd.DataFrame(cmp)
    rd_ok = c.dropna(subset=["ybf_rd", "kap_rd"])
    fee_ok = c.dropna(subset=["ybf_ucret", "kap_ucret"])
    summary = {
        "pdf": int(len(out)), "ayristirildi": int((out["durum"] == "ok").sum()),
        "rd_bulunan": int(out["rd"].notna().sum()), "ucret_bulunan": int(out["yonetim_ucreti"].notna().sum()),
        "dogrulama_rd": {"karsilastirilan_fon": int(len(rd_ok)), "esit": int((rd_ok["ybf_rd"] == rd_ok["kap_rd"]).sum())},
        "dogrulama_ucret": {"karsilastirilan_fon": int(len(fee_ok)),
                            "esit": int(((fee_ok["ybf_ucret"] - fee_ok["kap_ucret"]).abs() < 1e-6).sum())},
        "not": "Doğrulama: fonun en son YBF'sinden okunan değer ile KAP fon sayfasının 15.10.2026 anlık görüntüsü.",
    }
    c.to_csv(OUT / "ybf_dogrulama.csv", index=False)
    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["ybf_ayristirma"] = {**summary, "alinma": now_iso()}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["list", "ybf", "parse"])
    ap.add_argument("--pause", type=float, default=2.5)
    ap.add_argument("--start", default="2021-01-01")
    a = ap.parse_args()
    if a.step == "parse":
        return step_parse()
    cli = Polite(a.pause)
    if a.step == "list":
        step_list(cli, date.fromisoformat(a.start))
    else:
        step_ybf(cli)


if __name__ == "__main__":
    main()
