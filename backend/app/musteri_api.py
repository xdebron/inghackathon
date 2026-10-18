"""Müşteri ekleme (RM çalışma alanı).

  GET    /api/musteriler                 tüm müşteriler (brief personaları + eklenenler; 'eklenen' işaretiyle)
  GET    /api/musteriler/secenekler      form seçenekleri (yorumlayıcının tanıdığı değerler, kısıt kataloğu, sıradaki kimlik)
  POST   /api/musteriler                 tek müşteri (form)
  POST   /api/musteriler/ice-aktar       JSON içe aktarım: tek nesne ya da liste (gövde ya da .json dosyası)
  DELETE /api/musteriler/{persona_id}    eklenen müşteriyi siler (brief personaları silinemez; kanıt kayıtları kalır)
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from . import clients

router = APIRouter(prefix="/api/musteriler")


@router.get("")
def list_clients():
    added = {p["persona_id"] for p in clients.list_all() if clients.is_added(p["persona_id"])}
    return [{**p, "eklenen": p["persona_id"] in added} for p in clients.list_all()]


@router.get("/secenekler")
def form_options():
    return clients.options()


@router.post("")
async def add_client(request: Request):
    try:
        return clients.add(await request.json(), "form")
    except clients.ClientError as e:
        raise HTTPException(400, str(e))


@router.post("/ice-aktar")
async def import_clients(request: Request, dosya: UploadFile | None = File(None)):
    try:
        payload = json.loads((await dosya.read()).decode("utf-8-sig")) if dosya is not None else await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"Geçerli bir JSON değil: {e}")
    res = clients.import_json(payload)
    if not res["eklenen"] and res["hatalar"]:
        raise HTTPException(400, {"message": "Hiçbir müşteri eklenemedi", "hatalar": res["hatalar"]})
    return res


@router.delete("/{persona_id}")
def delete_client(persona_id: str):
    try:
        clients.delete(persona_id)
    except KeyError:
        raise HTTPException(404, "Müşteri bulunamadı")
    except clients.ClientError as e:
        raise HTTPException(400, str(e))
    return {"silindi": persona_id.upper()}
