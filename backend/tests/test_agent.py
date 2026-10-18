"""Hazırlık ajanı: sahte LLM ile araç döngüsü, guard düzeltmesi, hata döngüsü kırma, izin listesi ve şablona dönüş.
Gerçek LLM sunucusu gerekmez."""
from __future__ import annotations

import json

import pytest

from app import agent, pipeline
from app.models import RunRequest


def _call(name, args=None, i=0):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


class FakeLLM:
    """Sırayla verilen yanıtları döndürür; her isteği kaydeder."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, payload):
        self.requests.append(payload)
        msg = self.replies.pop(0)
        return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


@pytest.fixture
def record(synthetic):
    r = pipeline.run(RunRequest(persona_id="P05"))
    assert r["status"] == "rm_onayi_bekliyor"
    return r


def _agent(replies, **kw):
    fake = FakeLLM(replies)
    return agent.Agent(model="test", post=fake, **kw), fake


def test_tool_loop_uses_tool_numbers_and_appends_disclaimer(record):
    rv = agent.RunView(record)
    brief = agent.data_tools(rv)["kisa_liste"][1]({})["urunler"][0]
    ret = brief["alanlar"]["getiri_1y"]["deger"]           # örn. '%46,00'
    a, fake = _agent([
        {"content": "", "tool_calls": [_call("profil_ozeti", i=1), _call("kisa_liste", i=2)]},
        {"content": f"NOT\n{brief['ad']} 1 yıllık getiri {ret}. Geçmiş getiri geleceği göstermez."},
    ])
    res = a.run(rv, "yaz", agent.SYSTEM_PREP, [], [brief["ad"]])
    assert res["ihlaller"] == [] and res["duzeltme_turu"] == 0
    assert res["metin"].endswith(agent.decision_config()["metin_guard"]["zorunlu_ibare"])
    assert [s["ad"] for s in res["iz"] if s["tip"] == "arac"] == ["profil_ozeti", "kisa_liste"]
    # Araç sonuçları modele 'tool' mesajı olarak gitti
    assert any(m["role"] == "tool" for m in fake.requests[1]["messages"])


def test_unsourced_number_triggers_revision(record):
    rv = agent.RunView(record)
    a, fake = _agent([
        {"content": "", "tool_calls": [_call("kisa_liste")]},
        {"content": "Bu fon yılda %99,9 getirir."},                  # araçlarda olmayan oran
        {"content": "Bu fonun geçmiş getirisi araçta belirtildiği gibidir."},
    ])
    res = a.run(rv, "yaz", agent.SYSTEM_PREP, [], [])
    assert res["duzeltme_turu"] == 1 and res["ihlaller"] == []
    feedback = [m for m in fake.requests[-1]["messages"] if m["role"] == "user" and "kural ihlalleri" in m["content"]]
    assert feedback and "Kaynağı olmayan oran" in feedback[0]["content"]


def test_banned_phrase_kept_if_not_fixed(record):
    rv = agent.RunView(record)
    a, _ = _agent([{"content": "Getiri garanti."}, {"content": "Getiri garanti."}], revisions=1)
    res = a.run(rv, "yaz", agent.SYSTEM_PREP, [], [])
    assert any("garanti" in v for v in res["ihlaller"])


def test_repeated_tool_errors_break_loop(record):
    rv = agent.RunView(record)
    bad = {"content": "", "tool_calls": [_call("urun_detay", {"urun_id": "UYDURMA:1"}, 1),
                                         _call("urun_detay", {"urun_id": "UYDURMA:2"}, 2),
                                         _call("urun_detay", {"urun_id": "UYDURMA:3"}, 3)]}
    a, fake = _agent([bad, {"content": "Elimdeki bilgilerle not."}])
    res = a.run(rv, "yaz", agent.SYSTEM_PREP, [], [])
    assert any(s["tip"] == "yonlendirme" for s in res["iz"])
    assert "tools" not in fake.requests[-1]                          # son istekte araçlar kapalı
    err = next(s for s in res["iz"] if s["tip"] == "arac")
    assert not err["basarili"] and "kısa liste kimliklerini" in err["sonuc_ozet"]


def test_allowed_percents_cover_fractions_and_points():
    allowed = agent.allowed_percents([{"a": 0.4652, "b": 40, "c": "TÜFE %29,73"}])
    assert {"%46,52", "%46,5", "%40", "%40,0", "%29,73"} <= allowed


def test_pipeline_falls_back_to_template_when_llm_unreachable(synthetic, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ajan")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")      # erişilemez
    monkeypatch.setenv("LLM_MODEL", "yok")
    r = pipeline.run(RunRequest(persona_id="P05"))
    assert r["llm"] == "placeholder-sablon"
    assert r["ajan"] and "hata" in r["ajan"]
    assert any("ajan hatası" in s["summary"] for s in r["trace"] if s["step"] == "metin_taslagi")
