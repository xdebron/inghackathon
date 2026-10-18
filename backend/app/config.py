"""Konfigürasyon ve dosya yolları."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
PERSONA_DIR = DATA_DIR / "personas"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CACHE_DIR = DATA_DIR / "cache"
EVIDENCE_DIR = BASE_DIR / "evidence"

for _d in (SNAPSHOT_DIR, CACHE_DIR, EVIDENCE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path: Path) -> None:
    """backend/.env içindeki KEY=VALUE satırlarını, ortamda zaten tanımlı değilse yükler (git'e girmez)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv(BASE_DIR / ".env")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load(name: str) -> dict:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


@lru_cache
def decision_config() -> dict:
    return _load("decision_config.json")


@lru_cache
def constraint_rules() -> dict:
    return _load("constraint_rules.json")


def config_hash() -> str:
    h = hashlib.sha256()
    for name in ("decision_config.json", "constraint_rules.json", "evds_series.json"):
        h.update((CONFIG_DIR / name).read_bytes())
    return h.hexdigest()[:16]


def norm_text(s: str) -> str:
    """Türkçe büyük/küçük harf duyarsız karşılaştırma için normalize eder."""
    return s.replace("İ", "i").replace("I", "ı").lower().strip()
