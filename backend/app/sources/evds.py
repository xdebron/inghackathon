"""TCMB EVDS göstergeleri (TÜFE, sektör mevduat faizleri, altın, politika faizi).

Anahtar yoksa veya seri çekilemezse gösterge 'missing' döner; son başarılı çekim varsa 'stale' olarak kullanılır.
Günlük önbellek: aynı gün içinde EVDS'e tekrar gidilmez.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from functools import lru_cache

import httpx

from ..config import CONFIG_DIR, now_iso
from ..db import store
from ..models import DataPoint

SOURCE = "TCMB EVDS"


@lru_cache
def series_config() -> dict:
    return json.loads((CONFIG_DIR / "evds_series.json").read_text(encoding="utf-8"))


def _cached(ind: str) -> DataPoint | None:
    r = store.get_indicator(f"evds:{ind}")
    return DataPoint(key=ind, **r) if r else None


def parse_latest(payload: dict, code: str) -> tuple[float, str] | None:
    """EVDS JSON yanıtından en son boş olmayan değeri ve tarihini döndürür."""
    prefix = code.replace(".", "_")
    for item in reversed(payload.get("items", [])):
        for k, v in item.items():
            if k.startswith(prefix) and v not in (None, "", "null"):
                try:
                    return float(str(v).replace(",", ".")), item.get("Tarih")
                except ValueError:
                    continue
    return None


def _missing(ind: str, cfg: dict, note: str) -> DataPoint:
    return DataPoint(key=ind, source=SOURCE, url=series_config()["base_url"], fetched_at=now_iso(),
                     status="missing", note=f"{cfg.get('ad', ind)}: {note}")


def fetch(ind: str, timeout: float = 15.0) -> DataPoint:
    cfg = series_config()["seriler"][ind]
    if not cfg.get("kod"):
        return _missing(ind, cfg, "EVDS seri kodu tanımlı değil (config/evds_series.json).")
    key = os.getenv("EVDS_API_KEY")
    cached = _cached(ind)
    if cached and cached.fetched_at and cached.fetched_at[:10] == date.today().isoformat():
        return cached
    if not key:
        if cached:
            return cached.model_copy(update={"status": "stale", "note": "EVDS_API_KEY yok; son başarılı çekim kullanıldı."})
        return _missing(ind, cfg, "EVDS_API_KEY tanımlı değil.")
    base = series_config()["base_url"]
    end = date.today()
    start = end - timedelta(days=series_config()["gun_geriye"])
    url = f"{base}series={cfg['kod']}&startDate={start:%d-%m-%Y}&endDate={end:%d-%m-%Y}&type=json"
    if cfg.get("formul"):
        url += f"&formulas={cfg['formul']}"
    try:
        resp = httpx.get(url, headers={"key": key}, timeout=timeout)
        resp.raise_for_status()
        latest = parse_latest(resp.json(), cfg["kod"])
        if latest is None:
            raise ValueError("seride değer bulunamadı (seri kodu doğrulanmalı)")
        value, as_of = latest
        value *= cfg.get("carpan", 1)
        notes = [n for n in (cfg.get("ad"), cfg.get("not"),
                             None if cfg.get("dogrulandi") else "Seri kodu henüz manuel olarak doğrulanmadı.") if n]
        dp = DataPoint(key=ind, value=value / 100 if cfg["yuzde"] else value, unit="oran" if cfg["yuzde"] else None,
                       source=f"{SOURCE} ({cfg['kod']})", url=base, as_of=as_of, fetched_at=now_iso(),
                       note=" ".join(notes) or None)
        with store.Run(f"evds:{ind}") as run:
            store.put_indicator(f"evds:{ind}", dp.value, dp.unit, dp.source, dp.url, dp.as_of, dp.fetched_at, dp.note, run)
        return dp
    except Exception as exc:
        if cached:
            return cached.model_copy(update={"status": "stale",
                                             "note": f"Canlı çekim başarısız ({type(exc).__name__}: {exc}); son başarılı çekim kullanıldı."})
        return _missing(ind, cfg, f"çekilemedi ({type(exc).__name__}: {exc}).")


def fetch_history(code: str, start: str = "01-01-2022", timeout: float = 30.0) -> dict:
    """Bir EVDS serisinin tüm geçmişi: {"points": [[tarih, değer], ...], "source", "fetched_at", "status"}.
    Günlük önbellek; çekilemezse son önbellek 'stale' döner, o da yoksa 'missing'."""
    doc = f"onbellek:evds_hist_{code.replace('.', '_')}_{start.replace('-', '')}"  # başlangıç tarihi anahtarda
    cached = store.document(doc)
    if cached and cached["fetched_at"][:10] == date.today().isoformat():
        return cached
    key = os.getenv("EVDS_API_KEY")
    base = series_config()["base_url"]
    try:
        if not key:
            raise RuntimeError("EVDS_API_KEY tanımlı değil")
        # EVDS tek istekte en fazla 1000 satır döndürür: 2 yıllık parçalar halinde iste
        prefix = code.replace(".", "_")
        pts, y0 = [], int(start[-4:])
        for y in range(y0, date.today().year + 1, 2):
            s = start if y == y0 else f"01-01-{y}"
            e = min(date(y + 1, 12, 31), date.today())
            url = f"{base}series={code}&startDate={s}&endDate={e:%d-%m-%Y}&type=json"
            resp = httpx.get(url, headers={"key": key}, timeout=timeout)
            resp.raise_for_status()
            pts += [[it["Tarih"], float(v)] for it in resp.json().get("items", [])
                    for k, v in it.items() if k.startswith(prefix) and v not in (None, "", "null")]
        if not pts:
            raise ValueError("seride değer yok")
        out = {"code": code, "points": pts, "source": f"{SOURCE} ({code})", "url": base, "fetched_at": now_iso(), "status": "ok"}
        store.save_document(doc, out)
        return out
    except Exception as exc:
        if cached:
            return {**cached, "status": "stale", "note": f"Canlı çekim başarısız ({type(exc).__name__}); önbellek kullanıldı."}
        return {"code": code, "points": [], "source": f"{SOURCE} ({code})", "url": base, "fetched_at": now_iso(),
                "status": "missing", "note": f"{type(exc).__name__}: {exc}"}


def has_key() -> bool:
    return bool(os.getenv("EVDS_API_KEY"))
