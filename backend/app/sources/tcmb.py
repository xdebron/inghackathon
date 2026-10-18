"""TCMB gösterge döviz kurları (today.xml). Canlı çekilemezse son başarılı önbellek 'stale' olarak döner."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import httpx

from ..config import CACHE_DIR, now_iso
from ..models import DataPoint

URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
SOURCE = "TCMB gösterge kurlar"
CACHE = CACHE_DIR / "tcmb_kurlar.json"
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
        CACHE.write_text(json.dumps([p.model_dump() for p in points], ensure_ascii=False), encoding="utf-8")
        return points
    except Exception as exc:  # ağ hatası, parse hatası
        if CACHE.exists():
            cached = [DataPoint(**p) for p in json.loads(CACHE.read_text(encoding="utf-8"))]
            for p in cached:
                p.status = "stale"
                p.note = f"Canlı çekim başarısız ({type(exc).__name__}); son başarılı çekim kullanıldı."
            return cached
        return [DataPoint(key=f"kur.{c}TRY", source=SOURCE, url=URL, fetched_at=now_iso(), status="missing",
                          note=f"Canlı çekim başarısız ({type(exc).__name__}) ve önbellek yok.")
                for c in CURRENCIES]
