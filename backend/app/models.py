"""Paylaşılan veri modelleri. Her sayısal veri bir DataPoint olarak kaynağı ve zamanıyla taşınır."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DataStatus = Literal["ok", "missing", "stale", "conflict", "assumption"]


class DataPoint(BaseModel):
    key: str
    value: Any | None = None
    unit: str | None = None
    source: str
    url: str | None = None
    as_of: str | None = None  # verinin ait olduğu tarih (kaynaktaki)
    fetched_at: str | None = None  # sistemin veriyi aldığı an (ISO)
    status: DataStatus = "ok"
    note: str | None = None


class Persona(BaseModel):
    persona_id: str | None = None
    profil: str | None = None
    yas: int | None = None
    risk_profili: str | None = None
    durum: str | None = None
    yatirim_amaci: str | None = None
    yatirim_ufku: str | None = None
    likidite_ihtiyaci: str | None = None
    doviz_maruz_kalma_tercihi: str | None = None
    gelir_nakit_akisi_ihtiyaci: str | None = None
    anapara_koruma_tercihi: str | None = None
    oncelikler: list[str] | None = None
    acik_kisitlar: list[str] | None = None
    rm_notu: str | None = None
    nitelikli_yatirimci: bool | None = None   # SPK tanımı; serbest fonlar yalnızca nitelikli yatırımcıya satılabilir


class ConfirmationQuestion(BaseModel):
    field: str
    issue: Literal["eksik", "celiski", "taninmayan_deger", "eslesmeyen_kisit"]
    question: str
    detail: str


class Product(BaseModel):
    product_id: str
    name: str
    category: str
    currency: str
    fields: dict[str, DataPoint] = Field(default_factory=dict)

    def v(self, name: str) -> Any | None:
        dp = self.fields.get(name)
        if dp is None or dp.status == "missing":
            return None
        return dp.value


class Exclusion(BaseModel):
    product_id: str
    name: str
    category: str
    rule_id: str
    reason: str


class Flag(BaseModel):
    product_id: str | None = None
    category: str | None = None   # genel uyarı belirli bir ürün kategorisine aitse (notta yalnızca o kategori varsa gösterilir)
    level: Literal["bilgi", "uyari", "teyit"]
    message: str


class CriterionScore(BaseModel):
    criterion: str
    raw: Any | None
    normalized: float | None
    weight: float
    contribution: float
    explanation: str


class ScoredProduct(BaseModel):
    product: Product
    score: float
    confidence: float
    breakdown: list[CriterionScore]
    rank: int = 0
    selected: bool = False
    not_selected_reason: str | None = None


class TraceStep(BaseModel):
    step: str
    actor: Literal["deterministik", "rl", "llm", "rm"]
    started_at: str
    duration_ms: float
    summary: str


class RunRequest(BaseModel):
    persona_id: str | None = None
    persona: Persona | None = None  # jüri challenge: değiştirilmiş persona
    rm_answers: dict[str, Any] = Field(default_factory=dict)  # teyit sorularına RM yanıtları (alan -> değer)
    disabled_sources: list[str] = Field(default_factory=list)  # veri kesintisi simülasyonu
    mevcut_dagilim: dict[str, float] | None = None  # RM'in girdiği mevcut portföy (kategori -> oran), RL başlangıç durumu


class RMDecision(BaseModel):
    decision: Literal["onay", "duzenleyerek_onay", "red"]
    rm_name: str
    comment: str | None = None
    edited_text: str | None = None
