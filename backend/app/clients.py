"""Müşteriler: brief personaları (data/personas/*.json, P01-P05) ve RM'in eklediği müşteriler (veri tabanı, 'musteri').

Eklenen müşteri personayla aynı alanlara sahiptir ve aynı akıştan geçer: eksik, çelişkili ya da tanınmayan alan varsa
öneri üretilmez, RM'e teyit sorusu sorulur. Kimlik verilmezse sıradaki boş kimlik (P06, P07, ...) atanır.
"""
from __future__ import annotations

import json
import re

from pydantic import ValidationError
from sqlalchemy import select

from .config import PERSONA_DIR, constraint_rules, decision_config, now_iso
from .db import models as M, store
from .db.session import session_scope
from .models import Persona

ID_RE = re.compile(r"^[A-Z0-9_-]{2,16}$")
LIST_FIELDS = ("oncelikler", "acik_kisitlar")


class ClientError(ValueError):
    pass


def _brief() -> dict[str, dict]:
    return {json.loads(p.read_text(encoding="utf-8"))["persona_id"]: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(PERSONA_DIR.glob("*.json"))}


def _added() -> dict[str, dict]:
    store.ready()
    with session_scope() as s:
        return {r.persona_id: r.veri for r in s.scalars(select(M.Musteri).order_by(M.Musteri.persona_id))}


def list_all() -> list[dict]:
    """Önce brief personaları, sonra eklenenler (kimlik sırasıyla)."""
    return list(_brief().values()) + list(_added().values())


def get(persona_id: str) -> Persona:
    pid = str(persona_id).upper()
    data = _brief().get(pid) or _added().get(pid)
    if data is None:
        raise KeyError(persona_id)
    return Persona(**data)


def is_added(persona_id: str) -> bool:
    return str(persona_id).upper() in _added()


def _taken() -> set[str]:
    """Kullanılmış kimlikler: brief, eklenen ve kanıt kaydı olanlar (silinen müşterinin kayıtları yeni müşteriye karışmasın)."""
    from . import evidence
    return set(_brief()) | set(_added()) | {r["persona_id"] for r in evidence.list_light() if r.get("persona_id")}


def _next_id(taken: set[str]) -> str:
    n = max([int(m.group(1)) for t in taken if (m := re.fullmatch(r"P(\d+)", t))] + [5]) + 1
    return f"P{n:02d}"


def _normalize(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ClientError("Her müşteri bir JSON nesnesi olmalı.")
    data = {k: v for k, v in raw.items() if k in Persona.model_fields}
    unknown = sorted(set(raw) - set(Persona.model_fields))
    for k in LIST_FIELDS:                       # "a, b" ya da satır satır metin de kabul edilir
        if isinstance(data.get(k), str):
            data[k] = [x.strip() for x in re.split(r"[\n;,]", data[k]) if x.strip()]
    for k, v in list(data.items()):
        if isinstance(v, str):
            data[k] = v.strip() or None
    if data.get("yas") in ("", None):
        data["yas"] = None
    try:
        p = Persona(**data)
    except ValidationError as e:
        raise ClientError("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None
    if not p.profil:
        raise ClientError("'profil' (müşterinin kısa tanımı, ör. 'Emekli öğretmen') gerekli.")
    if p.yas is not None and not 18 <= p.yas <= 110:
        raise ClientError("'yas' 18-110 arasında olmalı.")
    out = p.model_dump()
    if unknown:
        out["_tanınmayan_alanlar"] = unknown
    return out


def add(raw: dict, yontem: str) -> dict:
    """Bir müşteriyi doğrular ve ekler. Kimlik yoksa atanır; brief personasının kimliği ya da var olan kimlik reddedilir."""
    data = _normalize(raw)
    unknown = data.pop("_tanınmayan_alanlar", None)
    taken = _taken()
    pid = (data.get("persona_id") or "").upper() or _next_id(taken)
    if not ID_RE.fullmatch(pid):
        raise ClientError(f"Geçersiz müşteri kimliği '{pid}' (harf, rakam, - ve _; 2-16 karakter).")
    if pid in taken:
        raise ClientError(f"'{pid}' kimliği zaten kullanılıyor (müşteri ya da kanıt kaydı var).")
    data["persona_id"] = pid
    store.ready()
    with session_scope() as s:
        s.add(M.Musteri(persona_id=pid, veri=data, yontem=yontem, eklenme=now_iso()))
    return {"persona": data, "tanınmayan_alanlar": unknown or []}


def import_json(payload) -> dict:
    """JSON içe aktarım: tek nesne ya da nesne listesi. Her kayıt ayrı doğrulanır; hatalı olanlar raporlanır, diğerleri eklenir."""
    items = payload if isinstance(payload, list) else [payload]
    added, errors = [], []
    for i, item in enumerate(items, 1):
        try:
            added.append(add(item, "json"))
        except ClientError as e:
            errors.append({"sira": i, "kimlik": item.get("persona_id") if isinstance(item, dict) else None, "hata": str(e)})
    return {"eklenen": added, "hatalar": errors}


def delete(persona_id: str) -> None:
    pid = str(persona_id).upper()
    if pid in _brief():
        raise ClientError("Brief personaları silinemez.")
    store.ready()
    with session_scope() as s:
        r = s.get(M.Musteri, pid)
        if r is None:
            raise KeyError(persona_id)
        s.delete(r)


def options() -> dict:
    """Form seçenekleri: yorumlayıcının tanıdığı değerler (config'ten). Başka değer girilirse akış RM'e teyit sorusu sorar."""
    cfg = decision_config()
    levels = [k for k in cfg["seviye_degerleri"] if not k.startswith("_")]
    return {
        "risk_profili": [k for k in cfg["risk_profilleri"] if not k.startswith("_")],
        "yatirim_ufku": [k["anahtar"] + " yıl" if not k["anahtar"].endswith("yıl") else k["anahtar"] for k in cfg["ufuk_kaliplari"]["kaliplar"]],
        "likidite_ihtiyaci": levels, "gelir_nakit_akisi_ihtiyaci": levels, "anapara_koruma_tercihi": levels,
        "doviz_maruz_kalma_tercihi": [k["anahtar"].capitalize() for k in cfg["doviz_tercihi_kaliplari"]["kaliplar"]],
        # Brief personalarındaki öncelikler (puanlama bunları anahtar kelimeyle tanır); serbest metin de girilebilir
        "oncelikler": sorted({x for p in _brief().values() for x in (p.get("oncelikler") or [])}),
        "acik_kisitlar": [{"metin": r["ornek"], "kural": r["id"], "aciklama": r["aciklama"]}
                          for r in constraint_rules()["kurallar"] if r.get("ornek")],
        "sonraki_kimlik": _next_id(_taken()),
        "ornek": _brief().get("P05"),
    }
