"""Model ekranı uçları: salt okunur durum, seçenekler ve etkileşimli tahmin (çelişkili profil reddedilir)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import rl_category as rc
from app.main import app

c = TestClient(app)
needs_model = pytest.mark.skipif(not (rc.MODEL_DIR / "model.npz").exists() or not (rc.MODEL_DIR / "meta.json").exists(),
                                 reason="eğitilmiş model yok")


def test_model_and_options_endpoints():
    m = c.get("/api/rl/model")
    assert m.status_code == 200 and "durum" in m.json()
    o = c.get("/api/rl/secenekler").json()
    assert o["alanlar"]["yatirim_ufku"] == ["1-3 yıl", "5+ yıl"] and o["tarihler"]
    brief = [k for k in o["kisitlar"] if o["kisit_bilgisi"].get(k, {}).get("kaynak", "brief") == "brief"]
    assert len(brief) == 5 and len(o["kisitlar"]) > 5            # brief'teki 5 kısıt + katalog


@needs_model
def test_predict_respects_mask_and_returns_alternatives():
    r = c.post("/api/rl/tahmin", json={"persona_id": "P05"}).json()
    assert abs(sum(r["kategori_dagilimi"].values()) - 1) < 1e-9
    assert set(r["kategori_dagilimi"]) <= {"para_piyasasi", "borclanma", "katilim"}  # P05 maskesi
    assert r["alternatifler"][0]["sira"] == 1 and len(r["alternatifler"]) <= 5


@needs_model
def test_contradictory_profile_is_rejected_with_questions():
    body = {"profil": {"risk_profili": "Agresif", "yatirim_ufku": "5+ yıl", "likidite_ihtiyaci": "Düşük",
                       "anapara_koruma_tercihi": "Yüksek", "doviz_maruz_kalma_tercihi": "Açık", "acik_kisitlar": []}}
    r = c.post("/api/rl/tahmin", json=body)
    assert r.status_code == 422 and r.json()["detail"]["sorular"]


@needs_model
def test_predict_with_current_portfolio_reports_turnover():
    r = c.post("/api/rl/tahmin", json={"persona_id": "P01", "mevcut_dagilim": {"mevduat_tl": 1.0}}).json()
    assert r["mevcut_dagilim"] == {"mevduat_tl": 1.0}
    assert all(0 <= a["devir"] <= 1 for a in r["alternatifler"])
