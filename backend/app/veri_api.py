"""Veri tabanı ve toplayıcı durumu.

  GET  /api/veri/durum      tablo satır sayıları, her toplayıcının son çalıştırması, toplama sürüyor mu
  GET  /api/veri/cekimler   son çekim kayıtları (kaynak, yöntem, zaman, durum, satır, hata)
  POST /api/veri/topla      seçilen toplayıcıları (boşsa hepsini) arka planda sırayla çalıştırır; aynı anda tek toplama
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import collectors
from .db import store

router = APIRouter(prefix="/api/veri")
_JOB: dict = {"thread": None, "adlar": [], "log": [], "baslangic": None, "bitis": None}
_LOCK = threading.Lock()


class CollectRequest(BaseModel):
    adlar: list[str] = Field(default_factory=list)


@router.get("/durum")
def status():
    last: dict[str, dict] = {}
    for r in store.runs(1000):
        last.setdefault(r["kaynak"], r)
    running = _JOB["thread"] is not None and _JOB["thread"].is_alive()
    return {"tablolar": store.table_counts(),
            "toplayicilar": [{"ad": k, "aciklama": v, "son": last.get(k)} for k, v in collectors.COLLECTORS.items()],
            "yuklemeler": {k: last.get(k) for k in ("tefas_export", "tefas_getiri")},
            "toplama": {"calisiyor": running, "adlar": _JOB["adlar"], "baslangic": _JOB["baslangic"], "bitis": _JOB["bitis"],
                        "log": _JOB["log"][-60:]}}


@router.get("/cekimler")
def runs(limit: int = 100):
    return store.runs(min(limit, 1000))


def _work(names: list[str]):
    for name in names:
        _JOB["log"].append(f"[{name}] başladı")
        try:
            res = collectors.run(name, log=lambda m: _JOB["log"].append(m))
            _JOB["log"].append(f"[{name}] tamam: {res}")
        except Exception as exc:
            _JOB["log"].append(f"[{name}] HATA: {type(exc).__name__}: {exc}")
    _JOB["bitis"] = store.now_iso()


@router.post("/topla")
def collect(r: CollectRequest):
    names = r.adlar or list(collectors.COLLECTORS)
    unknown = [n for n in names if n not in collectors.COLLECTORS]
    if unknown:
        raise HTTPException(400, f"Bilinmeyen toplayıcı: {', '.join(unknown)}")
    with _LOCK:
        if _JOB["thread"] is not None and _JOB["thread"].is_alive():
            raise HTTPException(409, "Bir toplama zaten sürüyor")
        _JOB.update(adlar=names, log=[], baslangic=store.now_iso(), bitis=None)
        _JOB["thread"] = threading.Thread(target=_work, args=(names,), daemon=True)
        _JOB["thread"].start()
    return {"baslatildi": names}
