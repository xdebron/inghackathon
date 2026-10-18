"""Ajan işleri (arka planda): hazırlık notu yazma ve RM sorusu. Uzun süren LLM çağrıları HTTP isteğini bekletmez
(tünel/proxy zaman aşımına düşmesin); arayüz işi başlatır ve canlı izi yoklar.

POST /api/ajan/yaz   {"run_id"}           → iş kimliği. Bitince metin guard'dan geçtiyse kayıttaki taslak güncellenir.
POST /api/ajan/sor   {"run_id", "soru"}   → iş kimliği. Cevap ve iz kayıttaki "ajan_sorulari" listesine eklenir.
GET  /api/ajan/is/{id}                     → durum, canlı iz, sonuç
GET  /api/ajan/durum                       → LLM sunucusu erişilebilir mi, model adı
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import agent, evidence
from .config import now_iso

router = APIRouter(prefix="/api/ajan")
JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


class WriteRequest(BaseModel):
    run_id: str


class AskRequest(BaseModel):
    run_id: str
    soru: str


def _start(kind: str, run_id: str, fn) -> dict:
    rec = evidence.load(run_id)
    if rec is None:
        raise HTTPException(404, "Kayıt bulunamadı")
    if rec.get("status") == "rm_teyidi_bekliyor":
        raise HTTPException(409, "Kayıt RM teyidi bekliyor; önce teyit soruları yanıtlanmalı")
    job = {"id": uuid.uuid4().hex[:12], "tur": kind, "run_id": run_id, "durum": "calisiyor", "baslangic": now_iso(),
           "iz": [], "sonuc": None, "hata": None}
    with _LOCK:
        JOBS[job["id"]] = job

    def work():
        try:
            job["sonuc"] = fn(rec, lambda step: job["iz"].append(step))
            job["durum"] = "bitti"
        except Exception as e:  # sunucu yok, model hatası vb.
            job["hata"] = f"{type(e).__name__}: {e}"
            job["durum"] = "hata"
        job["bitis"] = now_iso()

    threading.Thread(target=work, daemon=True).start()
    return {"is_id": job["id"]}


def _apply_write(rec: dict, res: dict) -> dict:
    """Guard'dan geçen ajan metnini kayda yazar; RM kararı verilmiş kaydın taslağını değiştirmez."""
    log = {k: v for k, v in res.items() if k != "izin_verilen_oranlar"}
    cur = evidence.load(rec["run_id"]) or rec
    applied = not res["ihlaller"] and cur.get("status") in ("rm_onayi_bekliyor", "oneri_yok")
    if applied:
        cur["draft_text"] = res["metin"]
        cur["llm"] = f"ajan:{res['model']}"
        cur["guard"] = {**(cur.get("guard") or {}), "violations": [], "fallback_used": False, "ajan_duzeltme_turu": res["duzeltme_turu"]}
    cur["ajan"] = {**log, "uygulandi": applied,
                   "neden": None if applied else ("guard ihlali kaldı" if res["ihlaller"] else "kayıt için RM kararı verilmiş")}
    cur.setdefault("history", []).append({"event": "ajan_taslagi", "at": now_iso(), "uygulandi": applied, "model": res["model"]})
    evidence.save(cur)
    return {"uygulandi": applied, "ihlaller": res["ihlaller"], "metin": res["metin"], "sure_sn": res["sure_sn"],
            "arac_sayisi": res["arac_sayisi"], "duzeltme_turu": res["duzeltme_turu"]}


def _apply_answer(rec: dict, question: str, res: dict) -> dict:
    cur = evidence.load(rec["run_id"]) or rec
    entry = {"soru": question, "cevap": res["metin"], "ihlaller": res["ihlaller"], "zaman": now_iso(), "model": res["model"],
             "iz": res["iz"], "sure_sn": res["sure_sn"]}
    cur.setdefault("ajan_sorulari", []).append(entry)
    evidence.save(cur)
    return {k: v for k, v in entry.items() if k != "iz"}


@router.post("/yaz")
def write(r: WriteRequest):
    return _start("yaz", r.run_id, lambda rec, cb: _apply_write(rec, agent.write(rec, cb)))


@router.post("/sor")
def ask(r: AskRequest):
    if not r.soru.strip():
        raise HTTPException(400, "Soru boş")
    return _start("sor", r.run_id, lambda rec, cb: _apply_answer(rec, r.soru, agent.answer(rec, r.soru, cb)))


@router.get("/is/{job_id}")
def job(job_id: str):
    j = JOBS.get(job_id)
    if j is None:
        raise HTTPException(404, "İş bulunamadı (sunucu yeniden başlamış olabilir)")
    return j


@router.get("/durum")
def status():
    a = agent.Agent()
    try:
        r = httpx.get(f"{a.base}/models", timeout=5)
        r.raise_for_status()
        return {"erisilebilir": True, "sunucu": a.base, "model": r.json()["data"][0]["id"]}
    except Exception as e:
        return {"erisilebilir": False, "sunucu": a.base, "hata": f"{type(e).__name__}: {e}"}


# --- Müşteri yüzü: özet ve soru yanıtı taslakları (RM onayı olmadan müşteriye gösterilmez) -------------

class CustomerAnswerRequest(BaseModel):
    run_id: str
    soru_id: str


def _apply_customer_summary(rec: dict, res: dict) -> dict:
    cur = evidence.load(rec["run_id"]) or rec
    cur["musteri_ozeti"] = {"metin": res["metin"], "durum": "ihlal" if res["ihlaller"] else "rm_onayi_bekliyor",
                            "ihlaller": res["ihlaller"], "olusturma": now_iso(), "model": res["model"], "iz": res["iz"],
                            "sure_sn": res["sure_sn"]}
    cur.setdefault("history", []).append({"event": "musteri_ozeti_taslagi", "at": now_iso(), "ihlal": bool(res["ihlaller"])})
    evidence.save(cur)
    return {"durum": cur["musteri_ozeti"]["durum"], "ihlaller": res["ihlaller"], "sure_sn": res["sure_sn"]}


def _apply_customer_answer(rec: dict, qid: str, res: dict) -> dict:
    cur = evidence.load(rec["run_id"]) or rec
    q = next((x for x in cur.get("musteri_gelen", []) if x["id"] == qid), None)
    if q is None:
        raise ValueError("Müşteri sorusu bulunamadı")
    q.update(taslak=res["metin"], taslak_ihlaller=res["ihlaller"], taslak_zaman=now_iso(), durum="taslak", taslak_iz=res["iz"])
    evidence.save(cur)
    return {"durum": "taslak", "ihlaller": res["ihlaller"], "sure_sn": res["sure_sn"]}


@router.post("/musteri-ozeti")
def customer_summary(r: WriteRequest):
    rec = evidence.load(r.run_id)
    if rec is None:
        raise HTTPException(404, "Kayıt bulunamadı")
    if rec.get("status") != "rm_onayladi":
        raise HTTPException(409, "Müşteri özeti yalnızca RM'in onayladığı paketten hazırlanır; önce hazırlık notunu onaylayın")
    return _start("musteri_ozeti", r.run_id, lambda rec, cb: _apply_customer_summary(rec, agent.customer_summary(rec, cb)))


@router.post("/musteri-cevap")
def customer_answer(r: CustomerAnswerRequest):
    rec = evidence.load(r.run_id)
    if rec is None:
        raise HTTPException(404, "Kayıt bulunamadı")
    q = next((x for x in rec.get("musteri_gelen", []) if x["id"] == r.soru_id), None)
    if q is None:
        raise HTTPException(404, "Müşteri sorusu bulunamadı")
    return _start("musteri_cevap", r.run_id,
                  lambda rec, cb: _apply_customer_answer(rec, r.soru_id, agent.customer_answer(rec, q["soru"], cb)))
