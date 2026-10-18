"""Yatırım fonu stopajı: ING'nin "yatırım fonlarında vergilendirme" sayfasındaki gerçek kişi tablosu (kural 7: oran koda
sabit yazılmaz, kaynaktan alınır, RM teyit eder).

Yazılanlar: stopaj (kategori başına güncel oran: tablonun son sütunu) ve stopaj_donem (tüm dönemler; RL eğitiminde
geçmiş senaryolara o dönemin oranı uygulanır). Kategori ↔ tablo satırı eşlemesi bizim yorumumuzdur; RM teyit eder.
Okunamayan hücre boş kalır, değer uydurulmaz.
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..db import models as M, store
from .base import Polite

URL = "https://www.ing.com.tr/tr/bilgi-destek/yatirim-fonlarinda-vergilendirme"
ROWMAP = {
    "para_piyasasi": "Para Piyasası", "borclanma": "Borçlanma Araçları (Euroband,Yabancı hariç)",
    "fon_sepeti": "Fon Sepeti (Yabancı, Döviz fonları hariç)", "kiymetli_maden": "Kıymetli Madenler",
    "katilim": "Katılım (Döviz fonları hariç)", "serbest": "Serbest (Döviz fonlar dahil)",
    "karma_degisken": "Karma ve Değişken", "hisse": "Hisse Senedi Yoğun",
}
NOTE = ("Gerçek kişiler için; iktisap (alım) tarihi esas, FIFO. Kaynak banka sayfasıdır, resmî karar metni değildir. "
        "Müşterinin vergi statüsü, fon türü ve elde tutma süresine göre oran RM tarafından teyit edilmelidir.")


def parse_table(html: str) -> tuple[list[str], dict[str, dict[str, float | None]]]:
    s = BeautifulSoup(html, "lxml")
    for tb in s.find_all("table"):
        rows = [[re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])] for tr in tb.find_all("tr")]
        if rows and rows[0][:1] == ["Fon Türü"] and len(rows) > 1:
            periods = rows[0][1:]
            out = {}
            for r in rows[1:]:
                vals = {}
                for per, v in zip(periods, r[1:]):
                    m = re.fullmatch(r"%\s*(\d+(?:,\d+)?)", v)
                    vals[per] = float(m.group(1).replace(",", ".")) / 100 if m else None
                out[r[0]] = vals
            return periods, out
    raise ValueError("ING stopaj tablosu bulunamadı (sayfa yapısı değişmiş olabilir)")


def _period_bounds(p: str) -> tuple[str | None, str | None]:
    dates = [datetime.strptime(d, "%d.%m.%Y").date().isoformat() for d in re.findall(r"\d{2}\.\d{2}\.\d{4}", p)]
    if "öncesi" in p.lower():
        return None, dates[0] if dates else None
    if "sonrası" in p.lower():
        return (dates[0] if dates else None), None
    return (dates[0], dates[1]) if len(dates) >= 2 else (None, None)


def run(log=print) -> dict:
    cli = Polite(pause=1.0)
    with store.Run("ing_stopaj") as run_:
        r = cli.get(URL)
        if r is None:
            raise RuntimeError("ING vergilendirme sayfası alınamadı")
        now = store.now_iso()
        periods, table = parse_table(r.text)
        last = periods[-1]
        current, hist = [], []
        for cat, row in ROWMAP.items():
            if row not in table:
                continue
            current.append({"kategori": cat, "oran": table[row].get(last), "kaynak": f"ING yatırım fonlarında vergilendirme tablosu ('{row}', {last})",
                            "url": URL, "as_of": now[:10], "fetched_at": now, "notlar": NOTE})
            for i, p in enumerate(periods):
                a, b = _period_bounds(p)
                hist.append({"kategori": cat, "donem": p, "baslangic": a, "bitis": b, "oran": table[row].get(p), "sira": i,
                             "kaynak": "ING yatırım fonlarında vergilendirme tablosu (gerçek kişi)", "url": URL, "as_of": now[:10],
                             "fetched_at": now, "notlar": "Eurobond/yabancı fonlar tabloda yok. " + NOTE})
        if not current:
            raise RuntimeError("Tabloda beklenen fon türü satırları yok")
        store.replace_rows(M.Stopaj, current, M.Stopaj.kategori.in_(list(ROWMAP)), run_)
        store.replace_rows(M.StopajDonem, hist, run=run_)
        log(f"  {len(current)} kategori, {len(periods)} dönem; güncel sütun '{last}'")
    cli.close()
    return {"kategori": len(current), "donem": len(periods)}
