"""RL açıklama araçlarının HTTP uçları (salt okunur).

GET  /api/rl-araclar/semalar   LLM tool tanımları (OpenAI / Ollama 'tools' biçimi)
POST /api/rl-araclar/cagir     {"ad": "rl_neden", "argumanlar": {...}} → aracın JSON çıktısı (LLM tool çağrısı bu uçtan yürür)
POST /api/rl-araclar/ozet      persona (+ RM yanıtları, mevcut dağılım, model) → RM özet kartı metni + araç çıktıları
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import rl_tools

router = APIRouter(prefix="/api/rl-araclar")


class ToolCall(BaseModel):
    ad: str
    argumanlar: dict[str, Any] = Field(default_factory=dict)


class SummaryRequest(BaseModel):
    persona_id: str | None = None
    persona: dict[str, Any] | None = None
    rm_answers: dict[str, Any] = Field(default_factory=dict)
    mevcut_dagilim: dict[str, float] | None = None
    model: str | None = None


@router.get("/semalar")
def tool_schemas():
    return rl_tools.schemas()


@router.post("/cagir")
def call_tool(c: ToolCall):
    try:
        return rl_tools.call(c.ad, c.argumanlar)
    except rl_tools.ToolError as e:
        raise HTTPException(400, str(e))


@router.post("/ozet")
def summary(r: SummaryRequest):
    try:
        return rl_tools.ozet(r.model_dump(exclude_none=True))
    except rl_tools.ToolError as e:
        raise HTTPException(400, str(e))
