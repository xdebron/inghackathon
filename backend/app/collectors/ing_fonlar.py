"""ING platformunda sunulan fonlar. İki sayfa birleştirilir:
  - "fon fiyatları (detaylı)": öne çıkan fonlar, her satır 'KOD - FON ADI' (kod doğrudan sayfada)
  - "fon fiyatları": tam liste, yalnızca fon adı; kod, TEFAS'taki fon adıyla birebir eşleştirilerek bulunur
    (Türkçe harfler katlanır, sınıf eki '- TL/USD/EUR' atılır). Eşleşmeyen ad uydurulmaz, listeye girmez.
Bilgi amaçlıdır (fonun ING'de sunulup sunulmadığı); skora girmez, 'K_ING_PLATFORM' kısıtı seçilirse süzgeç olur.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..db import models as M, store
from .base import Polite

URL = "https://www.ing.com.tr/tr/bilgi-destek/yatirim/fon-fiyatlari-detayli"
URL_ALL = "https://www.ing.com.tr/tr/bilgi-destek/yatirim/fon-fiyatlari"
_FOLD = str.maketrans({"İ": "I", "ı": "I", "Ş": "S", "ş": "S", "Ğ": "G", "ğ": "G", "Ü": "U", "ü": "U", "Ö": "O", "ö": "O",
                       "Ç": "C", "ç": "C", "i": "İ"})


def name_key(name: str) -> str:
    s = re.sub(r"\s+", " ", str(name)).strip().translate(_FOLD).upper().translate(_FOLD)
    return re.sub(r"\s*-\s*(TL|USD|EUR)$", "", s).strip()


def parse_names(html: str) -> list[str]:
    t = BeautifulSoup(html, "lxml").find("table")
    if t is None:
        raise ValueError("ING tam fon listesi tablosu bulunamadı")
    out = []
    for tr in t.find_all("tr")[1:]:
        c = tr.find_all(["td", "th"])
        if c:
            n = re.sub(r"\s+", " ", c[0].get_text(" ", strip=True))
            if n:
                out.append(n)
    return out


SOURCE = "ING fon fiyatları sayfası"


def parse_codes(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    table = next((t for t in soup.find_all("table") if "Yatırım Fonları" in t.get_text(" ", strip=True)[:60]), None)
    if table is None:
        raise ValueError("ING fon tablosu bulunamadı (sayfa yapısı değişmiş olabilir)")
    codes = set()
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        title = re.sub(r"\s+", " ", tds[0].get_text(" ", strip=True))
        code = title.partition(" - ")[0].strip()
        if re.fullmatch(r"[A-Z0-9]{3}", code):
            codes.add(code)
    return sorted(codes)


def run(log=print) -> dict:
    cli = Polite(pause=1.0)
    with store.Run("ing_fonlar") as run_:
        r = cli.get(URL)
        if r is None:
            raise RuntimeError("ING fon fiyatları sayfası alınamadı")
        direct = parse_codes(r.text)
        from ..sources import tefas_import
        by_key = {}
        for code, name in tefas_import.price_names().items():
            by_key.setdefault(name_key(name), code)
        r2 = cli.get(URL_ALL)
        names = parse_names(r2.text) if r2 is not None else []
        matched = {by_key[name_key(n)] for n in names if name_key(n) in by_key}
        codes = sorted(set(direct) | matched)
        if not codes:
            raise RuntimeError("Sayfalarda fon kodu bulunamadı")
        now = store.now_iso()
        rows = [{"platform": "ING", "kod": c, "kaynak": SOURCE, "url": URL if c in direct else URL_ALL, "as_of": now[:10], "fetched_at": now,
                 "notlar": "Bilgi amaçlı; skora girmez." + ("" if c in direct else " Kod, ING'deki fon adının TEFAS adıyla eşleştirilmesiyle bulundu.")}
                for c in codes]
        store.replace_rows(M.PlatformFon, rows, M.PlatformFon.platform == "ING", run_)
        unmatched = len({name_key(n) for n in names}) - len(matched)
        run_.mesaj = f"{len(direct)} kod sayfada, {len(matched)} ad TEFAS ile eşleşti, {unmatched} ad eşleşmedi"
        log(f"  ING platformunda {len(codes)} fon ({run_.mesaj})")
    cli.close()
    return {"fon": len(codes), "eslesmeyen_ad": unmatched}
