"""Hazırlık notu: RL dağılımıyla kurulan yedi bölüm, biçim kontrolü (kesik/eksik not, ajan ara cümlesi), serbest fonlarda
nitelikli yatırımcı teyidi, yalnızca ilgili teyit maddeleri ve tekrar olmaması, tek tip tarih biçimi."""
from __future__ import annotations

from app import guard, note
from app.config import decision_config
from app.models import Flag, RunRequest
from app.pipeline import run as _run


def run(**kw):
    return _run(RunRequest(**kw))


def test_template_note_has_all_sections_and_passes_guard(synthetic):
    r = run(persona_id="P03")
    t = r["draft_text"]
    assert guard.structure(t) == []
    assert r["guard"]["violations"] == []
    assert [s for s in note.NOTE_SECTIONS if s.split(")")[0] + ")" in t] == note.NOTE_SECTIONS
    assert r["not_icerigi"]["confirmations"] == r["teyitler"]


def test_no_line_is_repeated_in_note(synthetic):
    for pid in ("P01", "P03", "P05"):
        lines = [l.strip() for l in run(persona_id=pid)["draft_text"].splitlines() if len(l.strip()) > 30]
        assert len(lines) == len(set(lines)), pid
        assert run(persona_id=pid)["draft_text"].count("gelecekteki sonuçların göstergesi değildir") <= 1


def test_structure_catches_truncated_and_preamble():
    ibare = decision_config()["metin_guard"]["zorunlu_ibare"]
    ok = "\n".join([note.NOTE_TITLE + " – P01 (X)", *[f"{s}\n- madde." for s in note.NOTE_SECTIONS], "", ibare])
    assert guard.structure(ok) == []
    cut = ok.split(note.NOTE_SECTIONS[4])[0] + note.NOTE_SECTIONS[4] + "\n- 2025-03-21 (karma+fon+eurobond"
    v = guard.structure(cut)
    assert any("Bölüm eksik" in x for x in v) and any("yarıda" in x for x in v)
    assert any("başlığıyla başlamıyor" in x for x in guard.structure("Tüm veriler toplandı. Notu yazıyorum.\n---\n" + ok))
    tail = ok.replace("\n\n" + ibare, "\n\nGüvenilirlik notu: karar marjı dar.\n\n" + ibare)
    assert any("sonra soru dışında" in x for x in guard.structure(tail))


def test_serbest_funds_need_rm_confirmed_qualified_investor(synthetic):
    r = run(persona_id="P03")
    ex = {e["product_id"]: e for e in r["excluded"]}
    # KAP metninde ibare olmaması (SRV) da yetmez: müşteri teyidi yoksa iki serbest fon da dışlanır
    assert ex["FON:SRV"]["rule_id"] == ex["FON:SRB"]["rule_id"] == "R_NITELIKLI"
    assert any("nitelikli yatırımcı" in c for c in r["teyitler"])
    assert any("nitelikli yatırımcı" in q for q in r["musteri_sorulari"])
    r2 = run(persona_id="P03", rm_answers={"nitelikli_yatirimci": True})
    assert "FON:SRV" not in {e["product_id"] for e in r2["excluded"]}
    assert not any("nitelikli yatırımcı" in c for c in r2["teyitler"])


def test_generic_flags_only_for_categories_in_note():
    f = [Flag(level="teyit", category="dibs_tl", message="DİBS stopajı"), Flag(level="teyit", message="genel"),
         Flag(level="teyit", product_id="FON:A", message="A"), Flag(level="teyit", product_id="FON:B", message="B")]
    got = {x.message for x in note.relevant_flags(f, {"FON:A"}, {"para_piyasasi"})}
    assert got == {"genel", "A"}
    assert "DİBS stopajı" in {x.message for x in note.relevant_flags(f, set(), {"dibs_tl"})}


def test_dates_have_one_format():
    assert note.fmt_date("2026-8") == "Ağustos 2026"
    assert note.fmt_date("09-10-2026") == note.fmt_date("2026-10-09") == note.fmt_date("09.10.2026") == "09.10.2026"
    assert note.fmt_date(None) == "tarih yok"


def test_fx_premium_is_labelled_annual():
    label = note.MARKET_LABELS["gecmis.hedge_prim_usd_1a"][0]
    assert "yıllık" in label and "1 aylık" not in label
