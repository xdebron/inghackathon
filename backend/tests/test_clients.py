"""Müşteri ekleme: form ve JSON içe aktarım, kimlik atama, doğrulama, silme; eklenen müşteri brief personalarıyla aynı
akıştan geçer (eksik alanda RM teyit sorusu)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import clients, evidence, pipeline
from app.main import app
from app.models import RunRequest

import pytest


@pytest.fixture(autouse=True)
def fresh_evidence(tmp_path, monkeypatch):
    """Her test kendi kanıt klasörüyle: kimlik ataması kanıt kayıtlarına baktığı için testler birbirini etkilemesin."""
    d = tmp_path / "kanit"
    d.mkdir()
    monkeypatch.setattr(evidence, "EVIDENCE_DIR", d)


NEW = {"profil": "Emekli öğretmen", "yas": 66, "risk_profili": "Temkinli", "yatirim_ufku": "1-3 yıl",
       "likidite_ihtiyaci": "Orta", "gelir_nakit_akisi_ihtiyaci": "Yüksek", "anapara_koruma_tercihi": "Yüksek",
       "doviz_maruz_kalma_tercihi": "Sınırlı", "yatirim_amaci": "Emeklilik gelirini desteklemek", "durum": "Toplu ikramiye aldı",
       "oncelikler": "düzenli gelir, sermayenin korunması", "acik_kisitlar": ["yüksek riskli ürünlerden kaçınma"]}


def test_add_assigns_next_id_and_lists_after_brief(tmp_db):
    r = clients.add(dict(NEW), "form")
    p = r["persona"]
    assert p["persona_id"] == "P06" and p["oncelikler"] == ["düzenli gelir", "sermayenin korunması"]   # metin listeye çevrildi
    ids = [x["persona_id"] for x in pipeline.list_personas()]
    assert ids[:5] == ["P01", "P02", "P03", "P04", "P05"] and ids[-1] == "P06"
    assert pipeline.load_persona("p06").profil == "Emekli öğretmen" and clients.is_added("P06")


def test_validation_and_id_collisions(tmp_db):
    for bad, msg in (({"yas": 40}, "profil"), ({"profil": "X", "yas": 7}, "18-110"), ({**NEW, "persona_id": "P01"}, "zaten"),
                     ({**NEW, "persona_id": "geçersiz kimlik!"}, "Geçersiz")):
        try:
            clients.add(bad, "form")
            raise AssertionError("hata bekleniyordu")
        except clients.ClientError as e:
            assert msg in str(e)


def test_json_import_reports_bad_rows_and_adds_good_ones(tmp_db):
    res = clients.import_json([{**NEW, "persona_id": "M01"}, {"yas": 50}, {**NEW, "profil": "Doktor", "ek_alan": 1}])
    assert [x["persona"]["persona_id"] for x in res["eklenen"]] == ["M01", "P06"]
    assert res["hatalar"][0]["sira"] == 2 and "profil" in res["hatalar"][0]["hata"]
    assert res["eklenen"][1]["tanınmayan_alanlar"] == ["ek_alan"]


def test_brief_personas_cannot_be_deleted_added_can(tmp_db):
    clients.add(dict(NEW), "form")
    try:
        clients.delete("P01")
        raise AssertionError
    except clients.ClientError:
        pass
    clients.delete("P06")
    assert not clients.is_added("P06")


def test_added_client_with_missing_field_gets_confirmation_question(tmp_db, synthetic, tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "EVIDENCE_DIR", tmp_path / "kanit")
    clients.add({**NEW, "risk_profili": None}, "form")
    r = pipeline.run(RunRequest(persona_id="P06"))
    assert r["status"] == "rm_teyidi_bekliyor" and any("risk profili" in q["question"].lower() for q in r["questions"])


def test_api_form_import_and_portal_listing(tmp_db, synthetic, tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "EVIDENCE_DIR", tmp_path)
    c = TestClient(app)
    assert c.get("/api/musteriler/secenekler").json()["sonraki_kimlik"] == "P06"
    assert c.post("/api/musteriler", json=NEW).json()["persona"]["persona_id"] == "P06"
    r = c.post("/api/musteriler/ice-aktar", json=[{**NEW, "profil": "Mimar"}])
    assert r.status_code == 200 and r.json()["eklenen"][0]["persona"]["persona_id"] == "P07"
    assert c.post("/api/musteriler/ice-aktar", json=[{"yas": 1}]).status_code == 400
    rows = {x["persona"]["persona_id"] for x in c.get("/api/portal/personalar").json()}
    assert {"P06", "P07"} <= rows
    assert c.get("/api/musteri/P07").json()["profil"]["profil"] == "Mimar"           # müşteri portalında da var
    assert [x["eklenen"] for x in c.get("/api/musteriler").json()][-2:] == [True, True]


def test_deleted_clients_id_with_evidence_is_not_reused(tmp_db, synthetic, tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "EVIDENCE_DIR", tmp_path / "kanit")
    clients.add(dict(NEW), "form")
    pipeline.run(RunRequest(persona_id="P06"))          # kanıt kaydı oluşur
    clients.delete("P06")
    assert clients.add(dict(NEW), "form")["persona"]["persona_id"] == "P07"
    try:
        clients.add({**NEW, "persona_id": "P06"}, "form")
        raise AssertionError
    except clients.ClientError as e:
        assert "kanıt" in str(e)
