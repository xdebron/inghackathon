"""Senaryo testleri: açık kısıt uyumu, tutarlılık, eksik/çelişkili veri, veri kesintisi, metin güvenliği."""
from __future__ import annotations

import pytest

from app import guard, pipeline
from app.models import Persona, RunRequest

PERSONAS = ["P01", "P02", "P03", "P04", "P05"]


def run(**kw):
    return pipeline.run(RunRequest(**kw))


def shortlist(r):
    return [s["product"] for s in r["shortlist"]]


@pytest.mark.parametrize("pid", PERSONAS)
def test_every_persona_produces_shortlist_within_risk_limit(synthetic, pid):
    r = run(persona_id=pid)
    assert r["status"] == "rm_onayi_bekliyor"
    max_rd = r["profile_view"]["max_rd"]
    for p in shortlist(r):
        assert p["fields"]["rd"]["value"] <= max_rd


def test_p01_no_high_risk_products(synthetic):
    r = run(persona_id="P01")
    for p in shortlist(r):
        assert p["fields"]["rd"]["value"] < 5
        assert p["category"] not in ("hisse", "serbest")
    assert any(e["rule_id"] in ("K_YUKSEK_RISK", "R_RISK_PROFILI") and e["product_id"] == "FON:HSS" for e in r["excluded"])


def test_p02_no_additional_fx_exposure(synthetic):
    r = run(persona_id="P02")
    assert all(p["currency"] == "TRY" for p in shortlist(r))
    assert {e["product_id"] for e in r["excluded"] if e["rule_id"] == "K_DOVIZ_ARTIRMAMA"} >= {"FON:EUR"}


def test_p04_concentration_one_per_category(synthetic):
    r = run(persona_id="P04")
    cats = [p["category"] for p in shortlist(r)]
    assert len(cats) == len(set(cats)) and len(set(cats)) >= 3


def test_p05_no_locked_or_illiquid(synthetic):
    r = run(persona_id="P05")
    for p in shortlist(r):
        vade = p["fields"].get("vade_gun")
        assert vade is None or vade["value"] <= 32
        assert p["fields"]["valor_gun"]["value"] <= 2
    assert any(e["product_id"] == "MEVDUAT:BANKA:181" and e["rule_id"] == "K_LIKIDITE_KILIDI" for e in r["excluded"])


def test_p03_liquidity_weight_fixed_by_constraint(synthetic):
    r = run(persona_id="P03")
    assert "K_KISA_VADE_NAKIT_YOK" in r["weights"]["likidite"]["explanation"]
    assert r["weights"]["getiri"]["weight"] > r["weights"]["dusuk_oynaklik"]["weight"]


def test_qualified_and_small_funds_excluded(synthetic):
    r = run(persona_id="P03")
    ex = {e["product_id"]: e["rule_id"] for e in r["excluded"]}
    assert ex["FON:SRB"] == "R_NITELIKLI"
    assert ex["FON:KUC"] == "R_KUCUK_FON"


def test_lower_fee_ranks_higher_when_otherwise_similar(synthetic):
    r = run(persona_id="P05")
    allp = {s["product"]["product_id"]: s["score"] for s in r["shortlist"]}
    allp.update({o["product_id"]: o["score"] for o in r["others"]})
    assert allp["FON:PPA"] > allp["FON:PPB"]


@pytest.mark.parametrize("pid", PERSONAS)
def test_deterministic_repeat_runs(synthetic, pid):
    a, b = run(persona_id=pid), run(persona_id=pid)
    assert [(s["product"]["product_id"], s["score"]) for s in a["shortlist"]] == \
           [(s["product"]["product_id"], s["score"]) for s in b["shortlist"]]
    assert a["draft_text"] == b["draft_text"]


def test_missing_field_stops_and_asks(synthetic):
    p = pipeline.load_persona("P01").model_copy(update={"likidite_ihtiyaci": None})
    r = run(persona=p)
    assert r["status"] == "rm_teyidi_bekliyor"
    assert any(q["field"] == "likidite_ihtiyaci" and q["issue"] == "eksik" for q in r["questions"])
    assert "shortlist" not in r
    r2 = run(persona=p, rm_answers={"likidite_ihtiyaci": "Orta"})
    assert r2["status"] == "rm_onayi_bekliyor"


def test_unknown_constraint_asks_then_maps(synthetic):
    p = pipeline.load_persona("P05").model_copy(update={"acik_kisitlar": ["kripto varlıklardan uzak durma"]})
    r = run(persona=p)
    assert r["status"] == "rm_teyidi_bekliyor"
    assert r["questions"][0]["issue"] == "eslesmeyen_kisit"
    r2 = run(persona=p, rm_answers={"kisit.kripto varlıklardan uzak durma": "K_YUKSEK_RISK"})
    assert r2["status"] == "rm_onayi_bekliyor"
    assert r2["profile_view"]["rules"][0]["id"] == "K_YUKSEK_RISK"


def test_contradiction_requires_ack(synthetic):
    p = pipeline.load_persona("P03").model_copy(update={"anapara_koruma_tercihi": "Yüksek"})
    r = run(persona=p)
    assert r["status"] == "rm_teyidi_bekliyor"
    assert any(q["issue"] == "celiski" for q in r["questions"])
    r2 = run(persona=p, rm_answers={"onay.C_RISK_ANAPARA": True})
    assert r2["status"] == "rm_onayi_bekliyor"
    assert any("C_RISK_ANAPARA" in c for c in r2["profile_view"]["acknowledged"])


def test_unrecognized_level_asks(synthetic):
    p = pipeline.load_persona("P01").model_copy(update={"likidite_ihtiyaci": "Çok yüksek"})
    r = run(persona=p)
    assert any(q["issue"] == "taninmayan_deger" for q in r["questions"])


def test_jury_change_risk_profile_changes_universe(synthetic):
    base = run(persona_id="P02")
    p = pipeline.load_persona("P02").model_copy(update={"risk_profili": "Muhafazakâr"})
    r = run(persona=p)
    assert all(s["product"]["fields"]["rd"]["value"] <= 2 for s in r["shortlist"])
    assert [s["product"]["product_id"] for s in r["shortlist"]] != [s["product"]["product_id"] for s in base["shortlist"]]


def test_data_outage_no_invented_funds(synthetic):
    r = run(persona_id="P03", disabled_sources=["tefas"])
    assert all(not s["product"]["product_id"].startswith("FON:") for s in r["shortlist"])


def test_draft_text_passes_guard_and_mentions_no_excluded(synthetic):
    for pid in PERSONAS:
        r = run(persona_id=pid)
        assert r["guard"]["violations"] == [], (pid, r["guard"])
        for e in r["excluded"]:
            if e["product_id"].startswith("FON:"):
                assert f" {e['product_id'][4:]} " not in r["draft_text"]


def test_guard_catches_violations():
    allowed = {"%46,0"}
    text = "Bu fon garanti getiri sağlar, HSS de alınabilir, getirisi %55,0 olur."
    v = guard.check(text, ["HSS"], allowed)
    assert any("garanti" in x for x in v)
    assert any("HSS" in x for x in v)
    assert any("%55,0" in x for x in v)
    assert any("Zorunlu" in x for x in v)


def test_decision_records_rm_approval(synthetic):
    from app import evidence
    r = run(persona_id="P05")
    rec = evidence.add_decision(r["run_id"], {"decision": "onay", "rm_name": "Test RM", "comment": None, "edited_text": None})
    assert rec["status"] == "rm_onayladi" and rec["rm_decision"]["decided_at"]


def test_guard_allows_shortlisted_fund_names_but_not_promises():
    # Hata analizi: 'GARANTİ PORTFÖY ...' fon adı yasaklı 'garanti' ifadesine takılıyordu (yanlış alarm).
    name = "GARANTİ PORTFÖY YARI İLETKEN TEKNOLOJİLERİ DEĞİŞKEN FON"
    req = "Bu metin RM görüşme hazırlığı içindir; yatırım tavsiyesi değildir ve RM onayı olmadan müşteriyle paylaşılamaz."
    assert guard.check(f"1. {name}\n{req}", [], set(), [name]) == []
    v = guard.check(f"1. {name} getiri garanti eder.\n{req}", [], set(), [name])
    assert any("garanti" in x for x in v)


def test_guard_ignores_acronyms_that_collide_with_fund_codes():
    # Hata analizi: TEFAS'ta 'PDF' kodlu bir fon var; kaynak adındaki '(PDF)' dışlanan ürün sayılıyordu.
    req = "Bu metin RM görüşme hazırlığı içindir; yatırım tavsiyesi değildir ve RM onayı olmadan müşteriyle paylaşılamaz."
    assert guard.check(f"Kaynak: Hazine ihale sonucu (PDF).\n{req}", ["PDF"], set()) == []
    assert any("ABC" in v for v in guard.check(f"ABC fonu da düşünülebilir.\n{req}", ["ABC"], set()))
