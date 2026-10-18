"""RL açıklama araçları: gerçek modelle tutarlılık (Shapley toplamı, karşı-olgusal, eşik taraması) ve hata yolları.
Eğitilmiş v2 modeli ve veri yoksa atlanır."""
from __future__ import annotations

import numpy as np
import pytest

from app import rl_tools as T

try:
    T.load(T.DEFAULT_MODEL)
    HAVE = True
except Exception:
    HAVE = False
needs_model = pytest.mark.skipif(not HAVE, reason="varsayılan RL modeli yok")


@needs_model
@pytest.mark.parametrize("pid", ["P01", "P02", "P03", "P04", "P05"])
def test_shapley_efficiency_matches_real_model(pid):
    n = T.rl_neden({"persona_id": pid})
    total = {}
    for t in n["temalar"]:
        for c in t["kategori_katkilari"]:
            total[c["kategori"]] = total.get(c["kategori"], 0) + c["puan"]
    cats = set(n["bugunku_karar"]) | set(n["ortalama_piyasada_karar"])
    for c in cats:
        diff = n["bugunku_karar"].get(c, 0) - n["ortalama_piyasada_karar"].get(c, 0)
        assert abs(total.get(c, 0) - diff) < 0.5, (c, total.get(c), diff)  # yalnızca 0,1 puan yuvarlama payı
    # Açıklanan karar, rl_karar'daki modelin tercihiyle aynı olmalı (aynı ağ, aynı durum; kapı öncesi)
    assert n["bugunku_karar"] == T.rl_karar({"persona_id": pid})["model_tercihi"]


@needs_model
def test_what_if_without_changes_keeps_decision():
    r = T.rl_ne_olursa({"persona_id": "P01", "gostergeler": []})
    assert r["karar_degisti"] is False and r["onceki_dagilim"] == r["yeni_dagilim"]


@needs_model
def test_what_if_persona_change_respects_mask():
    r = T.rl_ne_olursa({"persona_id": "P02", "persona_degisiklikleri": {"risk_profili": "Muhafazakâr"}})
    ctx = T.context({"persona_id": "P02"}, persona_override={"risk_profili": "Muhafazakâr"})
    allowed = {ctx.cats[j] for j in range(len(ctx.cats)) if (ctx.A[ctx.mask][:, j] > 0).any()}
    assert set(r["yeni_dagilim"]) <= allowed


@needs_model
def test_threshold_segments_contain_today_decision():
    e = T.rl_esik({"persona_id": "P04", "gosterge": "TÜFE yıllık"})
    today = T.rl_karar({"persona_id": "P04"})["model_tercihi"]
    assert any(s["dagilim"] == today for s in e["bolgeler"])
    lows = [s["aralik"][0] for s in e["bolgeler"]]
    assert lows == sorted(lows)


@needs_model
def test_similar_periods_are_realized_and_spaced():
    b = T.rl_benzer_donemler({"persona_id": "P01", "adet": 4})
    dates = sorted(np.datetime64(x["tarih"]) for x in b["benzer_donemler"])
    assert all((d2 - d1).astype(int) >= 91 for d1, d2 in zip(dates, dates[1:]))
    assert all(x["donem"] in ("egitim", "dogrulama", "test", "egitim_oncesi", "kapsam_disi") for x in b["benzer_donemler"])


@needs_model
def test_reliability_reports_baselines_and_calibration():
    g = T.rl_guvenilirlik({"persona_id": "P01"})
    assert g["persona_test_kiyasi"]
    cov = g["inanc_kalibrasyonu_ort_kapsama_1sigma"]  # yalnızca inanç başlıklı modellerde (v2+)
    if cov is not None and cov < 0.5:
        assert any("kapsama" in u for u in g["uyarilar"])


def test_errors_and_schemas():
    names = {s["function"]["name"] for s in T.schemas()}
    assert names == set(T.TOOLS)
    with pytest.raises(T.ToolError):
        T.call("yok_boyle", {})
    if HAVE:
        with pytest.raises(T.ToolError):
            T.rl_esik({"persona_id": "P01", "gosterge": "olmayan gösterge"})
        with pytest.raises(T.ToolError):
            T.rl_karar({"persona_id": "P01", "model": "v9"})


@needs_model
@pytest.mark.parametrize("version", ["canli", "v1", "v2", "v4", "ppo_v9"])
def test_every_available_model_version_runs(version):
    try:
        T.load(version)
    except T.ToolError:
        pytest.skip(f"{version} modeli yok / eğitimi bitmedi")
    k = T.rl_karar({"persona_id": "P05", "model": version})
    assert k["kategori_dagilimi"] and abs(sum(k["kategori_dagilimi"].values()) - 100) <= 1
    if k["guvenli_iyilestirme"]:
        g = k["guvenli_iyilestirme"]
        assert k["kategori_dagilimi"] == (g["taban"] if g["kaynak"] == "taban_kural" else g["model_tercihi"])


@needs_model
def test_percentage_point_change_is_interpreted_in_ratio_units():
    a = T.rl_ne_olursa({"persona_id": "P05", "gostergeler": [{"ad": "Politika faizi", "degisim": -5}]})
    b = T.rl_ne_olursa({"persona_id": "P05", "gostergeler": [{"ad": "Politika faizi", "degisim": -0.05}]})
    assert any("yüzde puan olarak yorumlandı" in x for x in a["uygulanan_degisiklikler"])
    assert a["yeni_dagilim"] == b["yeni_dagilim"]
