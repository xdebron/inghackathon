"""Metin üretimi arayüzü.

KVKK gereği nihai sürümde lokal bir model kullanılacak. Şimdilik PlaceholderLLM yapılandırılmış karar
çıktısından deterministik bir taslak üretir. Lokal model aynı `LLMClient` arayüzünü uygulayıp
`get_client()` içinde LLM_PROVIDER ortam değişkeniyle seçilecek.

LLM'e karar verdirilmez: ürün listesi, dışlamalar ve sayılar deterministik katmandan gelir; LLM yalnızca
bunları RM için anlatır. Çıktı her durumda guard'dan geçer.
"""
from __future__ import annotations

import os
from typing import Protocol



def fmt_pct(x: float | None) -> str:
    if x is None:
        return "veri yok"
    return f"%{x * 100:.1f}".replace(".", ",")


class LLMClient(Protocol):
    name: str

    def draft_meeting_text(self, ctx: dict) -> str: ...


class PlaceholderLLM:
    name = "placeholder-sablon"

    def draft_meeting_text(self, ctx: dict) -> str:
        from .note import render
        return render(ctx)


def get_client() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "placeholder")
    if provider == "placeholder":
        return PlaceholderLLM()
    if provider == "ajan":
        from .agent import AgentClient   # lokal LLM (vLLM/OpenAI uyumlu) + tool calling
        return AgentClient()
    raise NotImplementedError(f"Bilinmeyen LLM sağlayıcısı '{provider}' (placeholder | ajan)")
