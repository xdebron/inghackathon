"""Kanıt kaydı: her çalıştırmanın girdileri, kullanılan veriler, kurallar, skorlar, metin ve RM kararı."""
from __future__ import annotations

import json
from pathlib import Path

from .config import EVIDENCE_DIR, now_iso


def _path(run_id: str) -> Path:
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")
    return EVIDENCE_DIR / f"{safe}.json"


def save(record: dict) -> None:
    _path(record["run_id"]).write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load(run_id: str) -> dict | None:
    p = _path(run_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# Kayıtlar büyük (piyasa verisi, ürün evreni, ajan izi: ~2-3 MB). Liste ve portal görünümleri yalnızca birkaç alan ister;
# her istekte tüm dosyaları ayrıştırmamak için bu alanlar dosyanın değişme zamanı ve boyutuyla önbelleğe alınır.
_LIGHT_FIELDS = ("run_id", "created_at", "status", "llm", "musteri_ozeti", "musteri_gelen")
_light_cache: dict[Path, tuple[tuple[int, int], dict]] = {}


def _light(p: Path) -> dict:
    st = p.stat()
    key = (st.st_mtime_ns, st.st_size)
    hit = _light_cache.get(p)
    if hit and hit[0] == key:
        return hit[1]
    r = json.loads(p.read_text(encoding="utf-8"))
    light = {k: r.get(k) for k in _LIGHT_FIELDS}
    light["persona_id"] = (r.get("persona") or {}).get("persona_id")
    light["rm_decision"] = (r.get("rm_decision") or {}).get("decision")
    _light_cache[p] = (key, light)
    return light


def list_light(persona_id: str | None = None) -> list[dict]:
    """Kayıtların hafif görünümü (en yeni önce). persona_id verilirse yalnızca o personanın kayıtları; kayıt kimliği
    '<tarih>-<saat>-<persona>-<ek>' biçiminde olduğundan diğer dosyalar hiç okunmaz."""
    paths = sorted(EVIDENCE_DIR.glob("*.json"), reverse=True)
    if persona_id:
        tag = f"-{persona_id.upper()}-"
        paths = [p for p in paths if tag in p.name]
    out = [_light(p) for p in paths]
    return [r for r in out if not persona_id or r["persona_id"] == persona_id.upper()]


def list_runs() -> list[dict]:
    return [{k: r[k] for k in ("run_id", "persona_id", "created_at", "status", "rm_decision")} for r in list_light()]


def add_decision(run_id: str, decision: dict) -> dict:
    r = load(run_id)
    if r is None:
        raise KeyError(run_id)
    r["rm_decision"] = {**decision, "decided_at": now_iso()}
    r["status"] = "rm_onayladi" if decision["decision"] != "red" else "rm_reddetti"
    r.setdefault("history", []).append({"event": "rm_karari", "at": r["rm_decision"]["decided_at"],
                                        "decision": decision["decision"], "rm": decision["rm_name"]})
    save(r)
    return r
