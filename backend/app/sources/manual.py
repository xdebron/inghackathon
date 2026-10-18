"""Elle toplanan kamuya açık veriler (mevduat oranları, politika faizi, stopaj, KAP fon bilgileri)."""
from __future__ import annotations

import json

from ..config import SNAPSHOT_DIR
from ..models import DataPoint

FILE = SNAPSHOT_DIR / "manuel_veriler.json"
REQUIRED_PROVENANCE = ("source", "url", "as_of")


def load_raw() -> dict:
    if not FILE.exists():
        return {}
    return json.loads(FILE.read_text(encoding="utf-8"))


def point(key: str, value, rec: dict, unit: str | None = None, fetched_at: str | None = None) -> DataPoint:
    """Kayıttan DataPoint üretir; kaynak bilgisi eksikse veri kullanılmaz (missing). Alınma zamanı kayıtta varsa (toplayan
    script'in çekim zamanı) o kullanılır; elle girilen kayıtlarda yoksa boş kalır, uydurulmaz."""
    fetched_at = fetched_at or rec.get("fetched_at")
    lacking = [k for k in REQUIRED_PROVENANCE if not rec.get(k)]
    if lacking:
        return DataPoint(key=key, value=None, unit=unit, source=rec.get("source") or "Manuel veri",
                         url=rec.get("url"), as_of=rec.get("as_of"), fetched_at=fetched_at, status="missing",
                         note=f"Kaynak bilgisi eksik ({', '.join(lacking)}); değer kullanılmadı.")
    if value is None:
        return DataPoint(key=key, value=None, unit=unit, source=rec["source"], url=rec["url"],
                         as_of=rec["as_of"], fetched_at=fetched_at, status="missing", note="Değer girilmemiş.")
    return DataPoint(key=key, value=value, unit=unit, source=rec["source"], url=rec["url"], as_of=rec["as_of"],
                     fetched_at=fetched_at)
