"""RM çalışma alanı ve müşteri portalı: müşteri yalnızca RM onaylı içeriği görür; RM kararları guard'dan geçer."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import evidence, pipeline
from app.main import app
from app.models import RunRequest


@pytest.fixture
def client(synthetic, tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "EVIDENCE_DIR", tmp_path)
    return TestClient(app)


@pytest.fixture
def approved_run(client):
    r = pipeline.run(RunRequest(persona_id="P05"))
    evidence.add_decision(r["run_id"], {"decision": "onay", "rm_name": "Test RM", "comment": None, "edited_text": None})
    return evidence.load(r["run_id"])


def test_overview_lists_latest_run_per_persona(client, approved_run):
    rows = {x["persona"]["persona_id"]: x for x in client.get("/api/portal/personalar").json()}
    assert rows["P05"]["son"]["run_id"] == approved_run["run_id"] and rows["P05"]["kayit_sayisi"] == 1
    assert rows["P01"]["son"] is None


def test_customer_sees_nothing_until_rm_approves_summary(client, approved_run):
    v = client.get("/api/musteri/P05").json()
    assert v["durum"] == "hazirlaniyor" and v["ozet"] is None
    assert "shortlist" not in v and "excluded" not in v          # iç veri müşteriye gitmez
    rec = evidence.load(approved_run["run_id"])
    rec["musteri_ozeti"] = {"metin": "Sayın müşterimiz, seçenekleriniz ...", "durum": "rm_onayi_bekliyor", "ihlaller": []}
    evidence.save(rec)
    assert client.get("/api/musteri/P05").json()["ozet"] is None
    r = client.post(f"/api/portal/runs/{rec['run_id']}/musteri-ozeti/karar", json={"karar": "onay", "rm_name": "Test RM"})
    assert r.status_code == 200
    v = client.get("/api/musteri/P05").json()
    assert v["durum"] == "hazir" and v["ozet"]["danisman"] == "Test RM"


def test_rm_cannot_approve_promise_text(client, approved_run):
    rec = evidence.load(approved_run["run_id"])
    rec["musteri_ozeti"] = {"metin": "x", "durum": "rm_onayi_bekliyor", "ihlaller": []}
    evidence.save(rec)
    r = client.post(f"/api/portal/runs/{rec['run_id']}/musteri-ozeti/karar",
                    json={"karar": "onay", "rm_name": "RM", "metin": "Bu fon garanti getiri sağlar."})
    assert r.status_code == 422


def test_customer_question_flow(client, approved_run):
    q = client.post("/api/musteri/P05/soru", json={"soru": "Param ne zaman hesaba geçer?"}).json()
    v = client.get("/api/musteri/P05").json()
    assert v["sorular"][0]["durum"] == "danismaninizda" and v["sorular"][0]["cevap"] is None
    rid = approved_run["run_id"]
    r = client.post(f"/api/portal/runs/{rid}/musteri-soru/{q['id']}/karar",
                    json={"karar": "onay", "rm_name": "RM", "metin": "Satış emriniz genellikle ertesi iş günü hesaba geçer."})
    assert r.status_code == 200
    s = client.get("/api/musteri/P05").json()["sorular"][0]
    assert s["durum"] == "yanitlandi" and "ertesi iş günü" in s["cevap"]


def test_customer_summary_job_requires_rm_approval(client):
    r = pipeline.run(RunRequest(persona_id="P05"))
    assert client.post("/api/ajan/musteri-ozeti", json={"run_id": r["run_id"]}).status_code == 409


def test_question_goes_to_record_rm_sees(client, approved_run):
    """Özet eski kayıtta onaylıyken yeni hazırlık yapılırsa soru yeni kayda (RM'in açtığı) düşer; müşteri her ikisini de görür."""
    old = evidence.load(approved_run["run_id"])
    old["musteri_ozeti"] = {"metin": "Sayın müşterimiz ...", "durum": "onaylandi", "onaylayan": "RM"}
    old["musteri_gelen"] = [{"id": "eski1", "soru": "Eski soru", "zaman": "2026-10-16T10:00:00+03:00", "durum": "bekliyor"}]
    evidence.save(old)
    import time
    time.sleep(1.1)                                  # kayıt kimliği saniye çözünürlüklü
    new = pipeline.run(RunRequest(persona_id="P05"))
    q = client.post("/api/musteri/P05/soru", json={"soru": "Yeni soru"}).json()
    assert any(x["id"] == q["id"] for x in evidence.load(new["run_id"]).get("musteri_gelen", []))
    assert not any(x["id"] == q["id"] for x in evidence.load(old["run_id"]).get("musteri_gelen", []))
    v = client.get("/api/musteri/P05").json()
    assert v["ozet"] is not None and [s["soru"] for s in v["sorular"]] == ["Yeni soru", "Eski soru"]
