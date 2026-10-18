"""RM çalışma alanı ve müşteri portalı uçları.

RM tarafı
  GET  /api/portal/personalar                         her persona için son kayıt özeti (arayüz kenar çubuğu)
  GET  /api/portal/arsiv/{persona_id}                 personanın tüm kayıtları (arşiv)
  POST /api/portal/runs/{run_id}/musteri-ozeti/karar  RM: müşteri özetini onayla / reddet (düzenlenmiş metinle)
  POST /api/portal/runs/{run_id}/musteri-soru/{id}/karar  RM: müşteri sorusunun yanıtını onayla / reddet
Müşteri tarafı (yalnızca RM onaylı içerik döner; iç değerlendirme verisi döndürülmez)
  GET  /api/musteri/{persona_id}
  POST /api/musteri/{persona_id}/soru
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import evidence, guard, pipeline
from .config import now_iso

router = APIRouter()

PUBLIC_PERSONA = ("persona_id", "profil", "yas", "risk_profili", "yatirim_amaci", "yatirim_ufku", "likidite_ihtiyaci",
                  "doviz_maruz_kalma_tercihi", "anapara_koruma_tercihi", "oncelikler")


class Decision(BaseModel):
    karar: str            # "onay" | "red"
    rm_name: str
    metin: str | None = None


class CustomerQuestion(BaseModel):
    soru: str


def _runs_by_persona() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in evidence.list_runs():               # en yeni önce
        out.setdefault(r["persona_id"] or "?", []).append(r)
    return out


def _summary(rec: dict) -> dict:
    mo = rec.get("musteri_ozeti") or {}
    gelen = rec.get("musteri_gelen") or []
    return {"run_id": rec["run_id"], "created_at": rec["created_at"], "status": rec["status"], "llm": rec.get("llm"),
            "rm_decision": rd.get("decision") if isinstance(rd := rec.get("rm_decision"), dict) else rd,
            "musteri_ozeti": mo.get("durum"),
            "bekleyen_musteri_sorusu": sum(1 for q in gelen if q.get("durum") in ("bekliyor", "taslak"))}


def _guard_customer(rec: dict, text: str) -> list[str]:
    codes = [e["product_id"].split(":", 1)[1] for e in rec.get("excluded", []) if e["product_id"].startswith("FON:")]
    names = [s["product"]["name"] for s in rec.get("shortlist", [])]
    from .config import decision_config
    req = decision_config()["metin_guard"]["zorunlu_ibare"]
    # RM'in yazdığı oranlar RM sorumluluğundadır; yasaklı ifade ve dışlanan ürün kontrol edilir
    return [v for v in guard.check(text + "\n" + req, codes, set(), names) if not v.startswith("Kaynağı olmayan oran")]


@router.get("/api/portal/personalar")
def personas_overview():
    runs = _runs_by_persona()
    out = []
    for p in pipeline.list_personas():
        pid = p["persona_id"]
        lst = runs.get(pid, [])
        latest = evidence.list_light(pid)[0] if lst else None
        out.append({"persona": p, "kayit_sayisi": len(lst), "son": _summary(latest) if latest else None})
    return out


@router.get("/api/portal/arsiv/{persona_id}")
def archive(persona_id: str):
    return _runs_by_persona().get(persona_id.upper(), [])


@router.post("/api/portal/runs/{run_id}/musteri-ozeti/karar")
def decide_summary(run_id: str, d: Decision):
    rec = evidence.load(run_id)
    if rec is None or not rec.get("musteri_ozeti"):
        raise HTTPException(404, "Müşteri özeti bulunamadı")
    mo = rec["musteri_ozeti"]
    if d.karar == "onay":
        text = d.metin or mo["metin"]
        v = _guard_customer(rec, text)
        if v:
            raise HTTPException(422, {"message": "Özet onaylanamaz", "violations": v})
        mo.update(metin=text, durum="onaylandi", onaylayan=d.rm_name, onay_zamani=now_iso(), duzenlendi=bool(d.metin))
    else:
        mo.update(durum="reddedildi", onaylayan=d.rm_name, onay_zamani=now_iso())
    rec.setdefault("history", []).append({"event": "musteri_ozeti_karari", "at": now_iso(), "karar": d.karar, "rm": d.rm_name})
    evidence.save(rec)
    return rec


@router.post("/api/portal/runs/{run_id}/musteri-soru/{qid}/karar")
def decide_answer(run_id: str, qid: str, d: Decision):
    rec = evidence.load(run_id)
    q = next((x for x in (rec or {}).get("musteri_gelen", []) if x["id"] == qid), None)
    if q is None:
        raise HTTPException(404, "Müşteri sorusu bulunamadı")
    if d.karar == "onay":
        text = (d.metin or q.get("taslak") or "").strip()
        if not text:
            raise HTTPException(400, "Yanıt metni boş")
        v = _guard_customer(rec, text)
        if v:
            raise HTTPException(422, {"message": "Yanıt onaylanamaz", "violations": v})
        q.update(cevap=text, durum="yanitlandi", yanitlayan=d.rm_name, yanit_zamani=now_iso())
    else:
        q.update(durum="reddedildi", yanitlayan=d.rm_name, yanit_zamani=now_iso())
    evidence.save(rec)
    return rec


def _persona_records(persona_id: str) -> list[dict]:
    """Personanın tüm kayıtlarının hafif görünümü (run_id, müşteri özeti, müşteri soruları...), en yeni önce."""
    return evidence.list_light(persona_id)


def _customer_run(persona_id: str) -> dict | None:
    """Müşteriye gösterilecek özet: RM onaylı müşteri özeti olan en yeni kayıt."""
    return next((r for r in _persona_records(persona_id) if (r.get("musteri_ozeti") or {}).get("durum") == "onaylandi"), None)


@router.get("/api/musteri/{persona_id}")
def customer_view(persona_id: str):
    try:
        persona = pipeline.load_persona(persona_id)
    except KeyError:
        raise HTTPException(404, "Profil bulunamadı")
    recs = _persona_records(persona_id)
    rec = _customer_run(persona_id)
    mo = (rec or {}).get("musteri_ozeti") or {}
    ozet = ({"metin": mo["metin"], "onay_zamani": mo.get("onay_zamani"), "danisman": mo.get("onaylayan")}
            if mo.get("durum") == "onaylandi" else None)
    sorular = [{"id": q["id"], "soru": q["soru"], "zaman": q["zaman"],
                "durum": "yanitlandi" if q.get("durum") == "yanitlandi" else "danismaninizda",
                "cevap": q.get("cevap") if q.get("durum") == "yanitlandi" else None,
                "yanit_zamani": q.get("yanit_zamani") if q.get("durum") == "yanitlandi" else None}
               # Sorular personanın tüm kayıtlarından (yeni hazırlık yapılınca eski sorular kaybolmaz), en yeni önce
               # RM'in geri çektiği yanıtlar müşteriye gösterilmez (soru yeni kayıtta yeniden RM kuyruğundadır)
               for q in sorted((q for r in recs for q in (r.get("musteri_gelen") or []) if q.get("durum") != "geri_cekildi"),
                               key=lambda q: q["zaman"], reverse=True)]
    p = persona.model_dump()
    return {"profil": {k: p.get(k) for k in PUBLIC_PERSONA}, "ozet": ozet, "sorular": sorular,
            "durum": "hazir" if ozet else "hazirlaniyor"}


@router.post("/api/musteri/{persona_id}/soru")
def customer_ask(persona_id: str, q: CustomerQuestion):
    text = q.soru.strip()
    if not text or len(text) > 1000:
        raise HTTPException(400, "Soru 1-1000 karakter olmalı")
    # Soru, RM'in çalışma alanında açtığı kayda (personanın en yeni kaydı) düşer; yoksa RM soruyu hiç görmez
    recs = _persona_records(persona_id)
    if not recs:
        raise HTTPException(409, "Danışmanınız henüz bir hazırlık başlatmadı")
    rec = evidence.load(recs[0]["run_id"])
    entry = {"id": uuid.uuid4().hex[:10], "soru": text, "zaman": now_iso(), "durum": "bekliyor"}
    rec.setdefault("musteri_gelen", []).append(entry)
    evidence.save(rec)
    return {"id": entry["id"], "durum": "danismaninizda"}
