"""Uçtan uca hazırlık akışı. Her adım izlenir (trace) ve sonuç kanıt kaydına yazılır.

Adımlar ve sorumlular:
  1. persona_yorumlama   deterministik  eksik/çelişkili/tanınmayan alan -> RM teyidi (akış durur)
  2. veri_toplama        deterministik  kaynak + zaman damgalı veri noktaları
  3. uygunluk_kurallari  deterministik  dışlamalar + gerekçeler
  4. puanlama_secim      deterministik  açıklanabilir skor, kısa liste
  5. metin_taslagi       llm            yalnızca anlatım; liste/sayılar 3-4'ten gelir
  6. metin_guard         deterministik  yasaklı ifade, dışlanan ürün, kaynaksız oran kontrolü
  7. rm_onayi            rm             onay / düzenleyerek onay / red (ayrı endpoint)
"""
from __future__ import annotations

import json
import time
import uuid
from collections import OrderedDict
from datetime import datetime

from . import evidence, guard, note, rl, rl_category, rules, scoring, universe
from .config import config_hash, decision_config, now_iso
from .llm import PlaceholderLLM, fmt_pct, get_client
from .note import CATEGORY_ROLES  # noqa: F401  (geri uyumluluk)
from .models import Flag, Persona, RunRequest, TraceStep
from .profile import apply_answers, interpret

def load_persona(persona_id: str) -> Persona:
    """Brief personası (data/personas) ya da RM'in eklediği müşteri (veri tabanı)."""
    from . import clients
    return clients.get(persona_id)


def list_personas() -> list[dict]:
    from . import clients
    return clients.list_all()


class Tracer:
    def __init__(self):
        self.steps: list[TraceStep] = []

    def run(self, step: str, actor: str, fn, summarize):
        started, t0 = now_iso(), time.perf_counter()
        result = fn()
        self.steps.append(TraceStep(step=step, actor=actor, started_at=started,
                                    duration_ms=round((time.perf_counter() - t0) * 1000, 1), summary=summarize(result)))
        return result


def _rl_lines(r: dict, allowed: set[str]) -> list[str]:
    """RL sonucunu taslak metin satırlarına çevirir; kullanılan oranlar guard'ın izinli sayı kümesine eklenir."""
    if r.get("durum") != "tamam":
        return [f"RL dağılım simülasyonu çalışmadı: {r.get('neden')}"] if r.get("durum") else []

    def pct(x):
        s = fmt_pct(x)
        allowed.add(s)
        return s

    names = r["urun_adlari"]
    alloc = "; ".join(f"{names[k]} {pct(v)}" for k, v in r["baslangic_dagilimi"].items())
    t, d = r["test"], r["donem"]["test"]
    rl_t, eq = t["RL politikası (dinamik)"], t["Eşit ağırlık (sabit)"]
    lines = [
        f"Başlangıç dağılımı ({r['baslangic_tarihi']} piyasa koşullarıyla; her 3 ayda bir yeniden değerlendirilir): {alloc}.",
        f"Ajan seçimi: doğrulama döneminde '{r['secilen_ajan']}' seçildi; başlangıç dağılımı '{r['baslangic_ajani']}' ajanından.",
        f"Test dönemi simülasyonu ({d[0]} – {d[1]}, eğitimde kullanılmadı): ortalama reel kazanç {pct(rl_t['ortalama_reel_kazanc'])} "
        f"(eşit ağırlık {pct(eq['ortalama_reel_kazanc'])}); dönem sonunda reel olarak anaparanın altında kalma olasılığı "
        f"{pct(rl_t['anapara_alti_bitis_olasiligi'])} (eşit ağırlık {pct(eq['anapara_alti_bitis_olasiligi'])}).",
    ]
    below = [p for p in r["politika"] if p["adim"] == 1 and p["servet_bandi"] in ("< %95", "%95-100") and p["guvenilir"]]
    above = [p for p in r["politika"] if p["adim"] == 1 and p["servet_bandi"] in ("%100-105", "%105-115") and p["guvenilir"]]
    fmt = lambda p: ", ".join(f"{names[k]} {pct(v)}" for k, v in p["agirliklar"].items())
    if below:
        lines.append(f"İlk 3 ayın sonunda reel servet anaparanın altındaysa politika: {fmt(below[0])}.")
    if above:
        lines.append(f"İlk 3 ayın sonunda reel servet anaparanın üstündeyse politika: {fmt(above[0])}.")
    if r["dislanan"]:
        lines.append("Simülasyona alınamayan ürünler: " + "; ".join(f"{names.get(d['product_id'], d['product_id'])} ({d['neden']})" for d in r["dislanan"]))
    for n in r["notlar"]:
        allowed.update(guard.PCT.findall(n))  # notlardaki oranlar RL değerlendirmesinden hesaplanır
        lines.append(f"Not: {n}")
    lines.append("Bu bölüm geçmiş verilerle yapılan bir simülasyondur; getiri tahmini değildir.")
    return lines


def _policy_uncertainty(original, req) -> dict | None:
    """NN-politika için seçilen dağılımın olasılığı ve olasılık ağırlıklı beklenen dağılım (aynı canlı model, aynı durum).
    Politika düzse tek dağılım (argmax) gürültüye duyarlıdır; RM'e yön olarak beklenen dağılım da gösterilir."""
    from . import rl_tools
    try:
        k = rl_tools.rl_karar({"persona": original.model_dump(), "rm_answers": req.rm_answers or {},
                               "mevcut_dagilim": req.mevcut_dagilim})
    except Exception:
        return None
    if k.get("secilen_olasilik") is None:
        return None
    return {"secilen_olasilik": k["secilen_olasilik"], "beklenen_dagilim": k["beklenen_dagilim"],
            "model_tercihi": k["model_tercihi"], "model": k["model"]}


def _feature_shap(original, req, cats: set[str]) -> dict | None:
    """Öneriler sekmesindeki ürün kartları için gösterge düzeyinde SHAP (rl_tools.ozellik_shap), yalnızca eşlenen
    kategoriler. Açıklama yardımcıdır: hesaplanamazsa öneri yine üretilir."""
    from . import rl_tools
    try:
        r = rl_tools.ozellik_shap({"persona": original.model_dump(), "rm_answers": req.rm_answers or {},
                                   "mevcut_dagilim": req.mevcut_dagilim})
    except Exception:
        return None
    if r is None:
        return None
    r["kategoriler"] = {c: v for c, v in r["kategoriler"].items() if c in cats}
    return r


def run(req: RunRequest) -> dict:
    tr = Tracer()
    original = req.persona or load_persona(req.persona_id or "")
    persona = apply_answers(original, req.rm_answers)
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{persona.persona_id or 'X'}-{uuid.uuid4().hex[:6]}"
    record = {
        "run_id": run_id, "created_at": now_iso(), "config_hash": config_hash(),
        "persona": persona.model_dump(), "persona_original": original.model_dump(),
        "rm_answers": req.rm_answers, "disabled_sources": req.disabled_sources,
    }

    view, questions = tr.run("persona_yorumlama", "deterministik", lambda: interpret(persona, req.rm_answers),
                             lambda r: f"{len(r[1])} teyit sorusu" if r[1] else "persona yorumlandı, kısıtlar kurallara bağlandı")
    if questions:
        record.update(status="rm_teyidi_bekliyor", questions=[q.model_dump() for q in questions],
                      trace=[s.model_dump() for s in tr.steps])
        evidence.save(record)
        return record

    products, market, data_flags = tr.run("veri_toplama", "deterministik", lambda: universe.build(req.disabled_sources),
                                          lambda r: f"{len(r[0])} ürün, {len(r[1])} piyasa verisi, {len(r[2])} veri uyarısı")
    eligible, excluded, rule_flags = tr.run("uygunluk_kurallari", "deterministik", lambda: rules.apply(products, view),
                                            lambda r: f"{len(r[0])} uygun, {len(r[1])} dışlandı")
    flags: list[Flag] = data_flags + rule_flags

    def score_and_select():
        scored, W = scoring.score(eligible, persona, view)
        return scored, W, scoring.select(scored, view)

    scored, W, sel_warnings = tr.run("puanlama_secim", "deterministik", score_and_select,
                                     lambda r: f"{sum(s.selected for s in r[0])} ürün kısa listede")
    flags += [Flag(level="uyari", message=w) for w in sel_warnings]

    def run_rl():
        try:
            return rl.run([s for s in scored if s.selected], persona, view)
        except Exception as exc:  # RL katmanı karar desteğinin ek parçası; hatası ana akışı durdurmaz
            return {"durum": "hata", "neden": f"{type(exc).__name__}: {exc}"}

    def rl_summary(r):
        if r.get("durum") != "tamam":
            return f"çalışmadı: {r.get('neden')}"
        t = r["test"]
        return (f"başlangıç {', '.join(f'{k.split(':', 1)[1]} %{v*100:.0f}' for k, v in r['baslangic_dagilimi'].items())}; "
                f"test ort. reel kazanç RL {t['RL politikası (dinamik)']['ortalama_reel_kazanc']:+.1%}, "
                f"eşit ağırlık {t['Eşit ağırlık (sabit)']['ortalama_reel_kazanc']:+.1%}")

    rl_result = tr.run("rl_dagilim", "rl", run_rl, rl_summary)

    def run_rlk():
        try:
            r = rl_category.infer(persona, view, scored, req.mevcut_dagilim)
        except Exception as exc:
            return {"durum": "hata", "neden": f"{type(exc).__name__}: {exc}"}
        if r.get("durum") == "tamam":
            r["politika_belirsizligi"] = _policy_uncertainty(original, req)
            # Öneriler sekmesi RL dağılımından kurulur: eşlenen ürünlerin tam verisi (kaynaklı alanlar, skor dökümü) ve
            # her kategori için aynı kategoriden bir sonraki uygun ürünler (RM alternatif olarak görür). Tümü kurallardan
            # geçmiş uygun ürünlerdir; RL izinsiz kategori seçemez (profil maskesi).
            by_id = {s.product.product_id: s for s in scored}
            dump = lambda s: {"product": s.product.model_dump(), "score": s.score, "confidence": s.confidence,  # noqa: E731
                              "rank": s.rank, "selected": s.selected, "breakdown": [c.model_dump() for c in s.breakdown]}
            chosen = {m["product_id"] for m in r["urun_eslemesi"] if m.get("product_id")}
            r["urun_ayrintilari"] = {pid: dump(by_id[pid]) for pid in chosen if pid in by_id}
            alts = {}
            for cat in {m["kategori"] for m in r["urun_eslemesi"]}:
                pool = sorted((s for s in scored if rl_category.PRODUCT_TO_CAT.get(s.product.category, s.product.category) == cat
                               and s.product.product_id not in chosen), key=lambda s: (not s.selected, -s.score))
                alts[cat] = [{"product_id": s.product.product_id, "ad": s.product.name, "skor": s.score, "kisa_listede": s.selected}
                             for s in pool[:3]]
            r["kategori_alternatifleri"] = alts
            r["ozellik_shap"] = _feature_shap(original, req, {m["kategori"] for m in r["urun_eslemesi"]})
        return r

    rlk_result = tr.run("rl_kategori_modeli", "rl", run_rlk,
                        lambda r: (", ".join(f"{k} %{v*100:.0f}" for k, v in r["kategori_dagilimi"].items())
                                   if r.get("durum") == "tamam" else f"çalışmadı: {r.get('neden')}"))

    allowed: set[str] = set()
    fallback = _rl_lines(rl_result, allowed) if rlk_result.get("durum") != "tamam" else []
    ctx = note.build(persona, view, scored, excluded, flags, {p.product_id: p for p in products}, market, rlk_result,
                     fallback, allowed)
    excluded_codes = [e.product_id.split(":", 1)[1] for e in excluded if e.product_id.startswith("FON:")]
    selected = [s for s in scored if s.selected]
    names = [s.product.name for s in selected]
    # RL eşlemesindeki ürün adları da metinde meşru özel addır (örn. '... Garanti Bankası' hissesi / 'GARANTİ PORTFÖY' fonu)
    names += [m["ad"] for m in rlk_result.get("urun_eslemesi", []) if m.get("ad") and m["ad"] not in names]
    shown = ctx.pop("shown_flags")
    note_ids = ctx.pop("note_ids")
    # Ajan (LLM + araçlar) çalıştırmanın kanıt kaydı biçimindeki görünümünü okur
    ctx["_run"] = {
        "persona": persona.model_dump(), "rm_answers": req.rm_answers, "profile_view": view.model_dump(), "weights": W,
        "market": [m.model_dump() for m in market],
        "flags": [f.model_dump() for f in shown], "hidden_flag_count": len(flags) - len(shown), "not_icerigi": ctx,
        "shortlist": [s.model_dump() for s in selected],
        "others": [{"product_id": s.product.product_id, "name": s.product.name, "category": s.product.category, "score": s.score,
                    "confidence": s.confidence, "rank": s.rank, "reason": s.not_selected_reason} for s in scored if not s.selected],
        "excluded": [e.model_dump() for e in excluded], "rl_kategori": rlk_result,
        "teyitler": ctx.get("confirmations", []), "musteri_sorulari": ctx.get("client_questions", []),
    }
    ctx["_excluded_codes"], ctx["_names"] = excluded_codes, names
    client = get_client()
    agent_error = None

    def draft():
        nonlocal client, agent_error
        try:
            return client.draft_meeting_text(ctx)
        except Exception as exc:  # LLM sunucusu yoksa / hata verirse: şablona dön, nedeni kayda geçir
            if isinstance(client, PlaceholderLLM):
                raise
            agent_error = f"{type(exc).__name__}: {exc}"
            client = PlaceholderLLM()
            return client.draft_meeting_text(ctx)

    text = tr.run("metin_taslagi", "llm", draft, lambda r: f"{client.name}: {len(r)} karakter"
                  + (f" (ajan hatası, şablon kullanıldı: {agent_error[:120]})" if agent_error else ""))
    agent_log = getattr(client, "last", None)
    if agent_log:
        allowed |= set(agent_log.get("izin_verilen_oranlar", []))   # ajanın araç çıktılarındaki sayılar
    full_check = lambda t: guard.check(t, excluded_codes, allowed, names) + guard.structure(t)  # noqa: E731
    violations = tr.run("metin_guard", "deterministik", lambda: full_check(text),
                        lambda r: "temiz" if not r else f"{len(r)} ihlal")
    guard_info = {"violations": violations, "fallback_used": False, "muaf_adlar": names}
    if violations and not isinstance(client, PlaceholderLLM):
        text = PlaceholderLLM().draft_meeting_text(ctx)
        guard_info.update(fallback_used=True, violations_after=full_check(text))

    # RM'e gösterilen uyarılar: notta geçen ürünlerinkiler ve notta bulunan kategorilere ait genel uyarılar (note.relevant_flags).
    # Diğerleri sayı olarak tutulur.
    hidden = len(flags) - len(shown)
    record.update(
        status="rm_onayi_bekliyor" if selected else "oneri_yok",
        profile_view=view.model_dump(),
        weights=W,
        market=[m.model_dump() for m in market],
        flags=[f.model_dump() for f in shown],
        hidden_flag_count=hidden,
        shortlist=[s.model_dump() for s in selected],
        others=[{"product_id": s.product.product_id, "name": s.product.name, "category": s.product.category,
                 "score": s.score, "confidence": s.confidence, "rank": s.rank, "reason": s.not_selected_reason,
                 "breakdown": [c.model_dump() for c in s.breakdown]} for s in scored if not s.selected],
        excluded=[e.model_dump() for e in excluded],
        universe_size=len(products),
        llm=client.name,
        draft_text=text,
        rl=rl_result,
        rl_kategori=rlk_result,
        guard=guard_info,
        ajan=({k: v for k, v in agent_log.items() if k != "izin_verilen_oranlar"} if agent_log else
              ({"hata": agent_error} if agent_error else None)),
        teyitler=ctx.get("confirmations", []), musteri_sorulari=ctx.get("client_questions", []),
        not_icerigi={k: v for k, v in ctx.items() if not k.startswith("_")},
        trace=[s.model_dump() for s in tr.steps],
    )
    evidence.save(record)
    return record
