"""Değerlendirme sonuçları: tools/degerlendir.py çıktısını arayüze verir.

  GET /api/degerlendirme   son değerlendirme takımı sonucu (senaryolar, kontroller, özet)
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .config import BASE_DIR

router = APIRouter()
OUT = BASE_DIR / "data" / "degerlendirme" / "son.json"


@router.get("/api/degerlendirme")
def latest() -> dict:
    if not OUT.exists():
        raise HTTPException(404, "Değerlendirme henüz çalıştırılmadı: backend klasöründe 'python tools/degerlendir.py'.")
    return json.loads(OUT.read_text(encoding="utf-8"))
