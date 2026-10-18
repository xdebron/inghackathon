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

from .config import decision_config


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
        p = ctx["persona"]
        lines = [
            f"GÖRÜŞME HAZIRLIK NOTU – {p['persona_id']} ({p['profil']}, {p['yas']})",
            "",
            "1) Görüşmenin amacı",
            f"Müşterinin hedefi: {p['yatirim_amaci']}. RM notu: {p.get('rm_notu') or '-'}",
            "",
            "2) Profil özeti",
            f"Risk profili {p['risk_profili']}, ufuk {p['yatirim_ufku']}, likidite ihtiyacı {p['likidite_ihtiyaci']}, "
            f"döviz tercihi '{p['doviz_maruz_kalma_tercihi']}', anapara koruma {p['anapara_koruma_tercihi']}.",
            f"Öncelikler: {', '.join(p['oncelikler'])}. Açık kısıtlar: {', '.join(p['acik_kisitlar']) or 'yok'}.",
            "",
            "Piyasa bağlamı",
        ]
        lines += [f"- {m}" for m in ctx.get("market_lines", [])] or ["- Piyasa göstergesi verisi yok."]
        lines += ["", "3) Tartışılabilecek alternatifler"]
        if not ctx["shortlist"]:
            lines.append("Uygunluk kurallarını geçen ürün bulunamadı; görüşmede veri eksikleri ve müşteri ihtiyaçları teyit edilmeli.")
        for i, s in enumerate(ctx["shortlist"], 1):
            lines.append(f"{i}. {s['name']} ({s['category_name']})")
            lines.append(f"   Portföydeki rolü: {s['role']}")
            lines.append(f"   Öne çıkan gerekçe: {s['top_reasons']}")
            lines.append(f"   Göstergeler: 1 yıllık getiri {s['ret']}, yıllık oynaklık {s['vol']}, risk değeri {s['rd']}, "
                         f"likidite {s['liq']}. Geçmiş performans gelecekteki sonuçların göstergesi değildir.")
            if s.get("extras"):
                lines.append(f"   Karşılaştırma: {s['extras']}.")
            if s["caveats"]:
                lines.append(f"   Dikkat: {s['caveats']}")
        if ctx.get("rl_lines"):
            lines += ["", "Dinamik dağılım (pekiştirmeli öğrenme simülasyonu)"]
            lines += [f"- {l}" for l in ctx["rl_lines"]]
        lines += ["", "4) Listede yer almayan seçenekler"]
        if ctx["exclusion_summary"]:
            for rule, info in ctx["exclusion_summary"].items():
                lines.append(f"- {info['count']} ürün: {info['example']}")
        else:
            lines.append("- Dışlanan ürün yok.")
        lines += ["", "5) Görüşmeden önce teyit edilmesi gerekenler"]
        lines += [f"- {t}" for t in ctx["confirmations"]] or ["- Yok."]
        lines += ["", "6) Müşteriye sorulabilecek sorular"]
        lines += [f"- {q}" for q in ctx["client_questions"]]
        lines += ["", decision_config()["metin_guard"]["zorunlu_ibare"]]
        return "\n".join(lines)


def get_client() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "placeholder")
    if provider == "placeholder":
        return PlaceholderLLM()
    if provider == "ajan":
        from .agent import AgentClient   # lokal LLM (vLLM/OpenAI uyumlu) + tool calling
        return AgentClient()
    raise NotImplementedError(f"Bilinmeyen LLM sağlayıcısı '{provider}' (placeholder | ajan)")
