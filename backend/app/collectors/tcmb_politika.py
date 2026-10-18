"""TCMB politika faizi (1 hafta repo): TCMB'nin kendi sayfasındaki faiz değişikliği tablosu.

Tablo yalnızca faizin DEĞİŞTİĞİ tarihleri listeler; sonraki tarihlerde faiz sabittir. Yazılanlar:
  gösterge 'politika_faizi'          güncel oran, veri tarihi = son değişikliğin yürürlük tarihi
  seri 'politika_faizi_karar'        2021'den itibaren adım serisi (her satır o tarihten itibaren geçerli oran)
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from bs4 import BeautifulSoup

from ..db import store
from .base import Polite, write_series

URL = ("https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Temel+Faaliyetler/"
       "Para+Politikasi/Merkez+Bankasi+Faiz+Oranlari/1+Hafta+Repo")
SOURCE = "TCMB 1 hafta repo faizi sayfası"


def parse_repo_table(html: str) -> list[dict]:
    t = BeautifulSoup(html, "lxml").find("table", id="midTable")
    if t is None:
        raise ValueError("TCMB 1 Hafta Repo tablosu bulunamadı (sayfa yapısı değişmiş olabilir)")
    rows = []
    for tr in t.find_all("tr")[1:]:
        c = [x.get_text(strip=True) for x in tr.find_all("td")]
        if len(c) < 3:
            continue
        num = lambda s: None if s in ("", "-") else float(s.replace(",", "."))  # noqa: E731
        rows.append({"effective_date": datetime.strptime(c[0], "%d.%m.%Y").date().isoformat(),
                     "borrowing_rate_pct": num(c[1]), "lending_rate_pct": num(c[2])})
    return sorted(rows, key=lambda r: r["effective_date"])


def run(start: str = "2021-01-01", log=print) -> dict:
    cli = Polite(pause=1.0)
    with store.Run("tcmb_politika") as run_:
        r = cli.get(URL)
        if r is None:
            raise RuntimeError("TCMB sayfası alınamadı")
        fetched = store.now_iso()
        series = [x for x in parse_repo_table(r.text) if x["lending_rate_pct"] is not None]
        cur = series[-1]
        store.put_indicator("politika_faizi", cur["lending_rate_pct"] / 100, "oran", SOURCE, URL, cur["effective_date"], fetched,
                            f"Yürürlük tarihi {cur['effective_date']}; sayfa {fetched[:10]} tarihinde okundu.", run_)
        s0 = date.fromisoformat(start)
        pts = [(date.fromisoformat(x["effective_date"]), x["lending_rate_pct"] / 100) for x in series]
        before = [p for p in pts if p[0] < s0]
        pts = ([(s0, before[-1][1])] if before else []) + [p for p in pts if p[0] >= s0]
        m = write_series("politika_faizi_karar", pd.DataFrame([(str(d), v) for d, v in pts], columns=["tarih", "deger"]), {
            "ad": "TCMB politika faizi (1 hafta repo), karar yürürlük tarihleri; adım serisi", "kaynak": SOURCE, "url": URL,
            "birim": "oran", "siklik": "karar bazlı", "alinma": fetched,
            "not": "Her satır o tarihten itibaren geçerli oran; aradaki günler bir önceki satırın değerini taşır."}, run_)
        log(f"  politika faizi %{cur['lending_rate_pct']} ({cur['effective_date']} itibarıyla); adım serisi {m['satir']} karar")
    cli.close()
    return {"oran": cur["lending_rate_pct"], "yururluk": cur["effective_date"]}
