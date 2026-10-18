"""TCMB gösterge döviz kurları (today.xml). Canlı çekilemezse son başarılı önbellek 'stale' olarak döner."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from ..config import now_iso
from ..db import store
from ..models import DataPoint

URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
SOURCE = "TCMB gösterge kurlar"
CURRENCIES = ("USD", "EUR", "GBP")


def parse(xml_text: str, fetched_at: str) -> list[DataPoint]:
    root = ET.fromstring(xml_text)
    as_of = root.attrib.get("Tarih")
    points = []
    for cur in root.findall("Currency"):
        code = cur.attrib.get("CurrencyCode")
        if code not in CURRENCIES:
            continue
        raw = (cur.findtext("ForexSelling") or "").strip()
        if not raw:
            points.append(DataPoint(key=f"kur.{code}TRY", source=SOURCE, url=URL, as_of=as_of,
                                    fetched_at=fetched_at, status="missing",
                                    note="Kaynakta döviz satış değeri boş"))
            continue
        points.append(DataPoint(key=f"kur.{code}TRY", value=float(raw), unit="TRY", source=SOURCE,
                                url=URL, as_of=as_of, fetched_at=fetched_at))
    return points


def fetch(timeout: float = 10.0) -> list[DataPoint]:
    try:
        resp = httpx.get(URL, timeout=timeout)
        resp.raise_for_status()
        points = parse(resp.text, now_iso())
        with store.Run("tcmb_kurlar") as run:
            for p in points:
                if p.status == "ok":
                    store.put_indicator(f"tcmb:{p.key}", p.value, p.unit, p.source, p.url, p.as_of, p.fetched_at, p.note, run)
        return points
    except Exception as exc:  # ağ hatası, parse hatası
        cached = [(c, store.get_indicator(f"tcmb:kur.{c}TRY")) for c in CURRENCIES]
        if any(r for _, r in cached):
            note = f"Canlı çekim başarısız ({type(exc).__name__}); son başarılı çekim kullanıldı."
            return [DataPoint(key=f"kur.{c}TRY", status="stale", **{**r, "note": note}) if r else
                    DataPoint(key=f"kur.{c}TRY", source=SOURCE, url=URL, fetched_at=now_iso(), status="missing",
                              note=f"Canlı çekim başarısız ({type(exc).__name__}) ve kayıt yok.")
                    for c, r in cached]
        return [DataPoint(key=f"kur.{c}TRY", source=SOURCE, url=URL, fetched_at=now_iso(), status="missing",
                          note=f"Canlı çekim başarısız ({type(exc).__name__}) ve önbellek yok.")
                for c in CURRENCIES]
