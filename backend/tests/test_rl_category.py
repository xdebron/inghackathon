"""Kategori düzeyi, profile koşullu RL testleri: maskeler sert kısıtları uygular, profiller çelişkisiz, veri seti
geleceğe bakmaz ve boşluksuz, stopaj dönemine göre uygulanır, model yokken güvenli davranır."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import rl_category as rc
from app.models import Persona
from app.profile import interpret

A = rc.action_grid()
RD = {"mevduat_tl": 1, "para_piyasasi": 1, "borclanma": 2, "katilim": 3, "kiymetli_maden": 6, "karma_degisken": 5,
      "fon_sepeti": 5, "hisse": 6, "yabanci_hisse": 6, "eurobond_doviz": 5}
CI = {c: i for i, c in enumerate(rc.CATS)}


def prof(**kw):
    p = {"id": "T", "risk_profili": "Dengeli", "max_rd": 5, "seviye": 0.6, "ufuk_yil": 1.0, "anapara": "Orta",
         "likidite": "Orta", "fx": 1.0, "yuksek_risk_yasagi": False, "kilit": False, "doviz_artirmama": False, "yogunlasma": False}
    p.update(kw)
    return p


def allowed_weight(mask, cat):
    return A[mask][:, CI[cat]].max()


def test_grid_respects_category_caps():
    fund = [i for c, i in CI.items() if c != "mevduat_tl"]
    assert np.allclose(A.sum(axis=1), 1) and (A <= rc.category_caps(rc.CATS) + 1e-9).all() and A[:, CI["mevduat_tl"]].max() == 1.0


def test_risk_limit_masks_high_rd_categories():
    m, why = rc.profile_mask(prof(max_rd=2), A, RD)
    assert allowed_weight(m, "hisse") == 0 and allowed_weight(m, "katilim") == 0 and allowed_weight(m, "borclanma") > 0
    assert any("hisse" in w for w in why)


def test_constraint_flags():
    m, _ = rc.profile_mask(prof(max_rd=7, yuksek_risk_yasagi=True), A, RD)
    assert allowed_weight(m, "hisse") == 0 and allowed_weight(m, "karma_degisken") == 0
    m, _ = rc.profile_mask(prof(kilit=True), A, RD)
    assert allowed_weight(m, "mevduat_tl") == 0 and allowed_weight(m, "yabanci_hisse") == 0
    m, _ = rc.profile_mask(prof(max_rd=7, doviz_artirmama=True), A, RD)
    assert all(allowed_weight(m, c) == 0 for c in rc.FX_CATS)
    m, _ = rc.profile_mask(prof(yogunlasma=True), A, RD)
    assert A[m].max() <= 0.5 + 1e-9


def test_fx_preference_caps_total_fx():
    fx = [CI[c] for c in rc.FX_CATS]
    m, _ = rc.profile_mask(prof(max_rd=7, fx=0.2), A, RD)
    assert A[m][:, fx].sum(axis=1).max() <= 0.25 + 1e-9
    m, _ = rc.profile_mask(prof(max_rd=7, fx=1.0), A, RD)
    assert A[m][:, fx].sum(axis=1).max() > 0.5


def test_every_sampled_profile_has_a_valid_action_and_is_contradiction_free():
    profiles = rc.sample_profiles(500, 3)
    for p in profiles:
        assert rc.profile_mask(p, A, RD)[0].any()
        persona = Persona(persona_id=p["id"], risk_profili=p["risk_profili"],
                          yatirim_ufku={0.5: "0-1 yıl", 1.0: "1-3 yıl", 3.0: "3-5 yıl", 5.0: "5+ yıl"}[p["ufuk_yil"]],
                          likidite_ihtiyaci=p["likidite"], doviz_maruz_kalma_tercihi="Açık", gelir_nakit_akisi_ihtiyaci="Orta",
                          anapara_koruma_tercihi=p["anapara"], oncelikler=["x"], yatirim_amaci="x",
                          acik_kisitlar=(["yüksek riskli ürünlerden kaçınma"] if p["yuksek_risk_yasagi"] else []))
        view, qs = interpret(persona, {})
        assert not [q for q in qs if q.issue == "celiski"], (p, qs)


def test_profile_vector_shape_stable():
    assert rc.profile_vector(prof()).shape == rc.profile_vector(rc.sample_profiles(1, 0)[0]).shape


@pytest.fixture(scope="module")
def ds():
    return rc.build_dataset()


def test_dataset_has_no_gaps_in_used_rows_and_disjoint_splits(ds):
    for k in ("train", "val", "test"):
        assert not np.isnan(ds["features"][ds[k]]).any()
        assert not np.isnan(ds["real"][ds[k]]).any()
    pools = [ds[k] for k in ("train", "val", "test") if len(ds[k])]   # veri eksikse bazı havuzlar boş olabilir
    assert all(a.max() < b.min() for a, b in zip(pools, pools[1:]))


def test_historical_withholding_is_period_dependent(ds):
    pp = ds["tax"][:, ds["cats"].index("para_piyasasi")]
    assert pp.min() < pp.max()  # 2023'te %0, 2025 sonrası %17,5


def test_short_training_runs_and_beats_nothing_is_not_required(ds):
    profiles = rc.sample_profiles(200, 5)
    Ad = rc.action_grid(ds["cats"])                  # veri setinin kategori listesine göre (v2: BIST 30 dahil)
    masks = np.array([rc.profile_mask(p, Ad, ds["rd"], ds["cats"])[0] for p in profiles])
    sim = rc.Sim(ds, Ad, profiles, masks)
    model = rc.ProfileDQN(ds, Ad, 0)
    curve = rc.train(model, sim, np.arange(150), ds["train"], 200, np.random.default_rng(0), eval_every=100, log=lambda r: None)
    assert len(curve) == 2
    for pol in ("dqn", "esit", "en_guvenli", "pp_agirlikli"):
        e = rc.evaluate(model, sim, np.arange(150, 160), ds["val"], 2, np.random.default_rng(1), pol)
        assert set(e) >= {"ortalama_reel_kazanc", "anapara_alti_bitis_olasiligi"}


def test_infer_without_model_is_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "MODEL_DIR", Path(tmp_path))
    rc._MODEL_CACHE.clear()
    r = rc.infer(None, None, [])
    assert r["durum"] == "model_yok"


def test_gpu_training_matches_numpy_export(ds):
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA yok")
    from app import rl_gpu
    from app.rl import MLP
    profiles = rc.sample_profiles(100, 9)
    Ad = rc.action_grid(ds["cats"])
    masks = np.array([rc.profile_mask(p, Ad, ds["rd"], ds["cats"])[0] for p in profiles])
    sim = rc.Sim(ds, Ad, profiles, masks)
    ts = rl_gpu.TorchSim(sim, "cuda")
    d_in = ds["features"].shape[1] + sim.pvec.shape[1] + rc.state_dims() + len(ds["cats"])
    net = rl_gpu.Net(d_in, 32, len(Ad)).cuda()
    hc = {**rc.cfg()["gpu"], "paralel_bolum": 64, "batch": 128, "hedef_aksiyon": 4, "tampon": 10000, "hedef_senkron": 50}
    rl_gpu.train(net, ts, np.arange(80), ds["train"], 100, hc, 0, eval_every=50, log=lambda r: None)
    m = MLP(d_in, 32, len(Ad), np.random.default_rng(0), 0.0)
    m.load_state_dict(net.numpy_state())
    x = np.random.default_rng(1).normal(size=(4, d_in))
    assert np.abs(m.forward(x) - net(torch.tensor(x, dtype=torch.float32, device="cuda")).detach().cpu().numpy()).max() < 1e-4
    e = rl_gpu.evaluate(net, ts, sim, np.arange(80, 90), ds["val"], 2, 1)
    assert set(e) >= {"ortalama_reel_kazanc", "anapara_alti_bitis_olasiligi"}


def test_enumerated_profiles_cover_brief_combinations():
    ps, st = rc.enumerate_profiles()
    assert (st["ham"], st["celiskisiz"], st["rl_farkli"]) == (27648, 11520, 2400)
    keys = {(p["risk_profili"], p["ufuk_yil"], p["anapara"], p["likidite"], p["fx"]) + tuple(p[f] for f in rc.FLAGS) for p in ps}
    assert len(keys) == len(ps)  # tekrar yok
    assert {p["ufuk_yil"] for p in ps} == {1.0, 5.0}  # yalnızca brief'te geçen ufuklar


def test_fine_grid_is_superset_of_coarse_and_respects_limits():
    from app.rl import action_grid as coarse_grid
    fund = np.array([c != "mevduat_tl" for c in rc.CATS])
    g = rc.cfg()["izgara"]
    assert np.allclose(A.sum(axis=1), 1) and (A <= rc.category_caps(rc.CATS) + 1e-9).all()
    fine = A[np.isclose((A * round(1 / g["adim"])) % 1, 0).all(axis=1)]
    assert ((fine > 0).sum(axis=1) <= max(g["max_kategori"], 4)).all()
    old = coarse_grid(len(rc.CATS), 0.25, None, fund, 0.5)
    old = old[(old <= rc.category_caps(rc.CATS) + 1e-9).all(axis=1)]   # yeni kategori sınırları içinde kalan eski seçenekler
    assert all(np.isclose(A, o).all(axis=1).any() for o in old)  # ... hiçbiri kaybolmadı
    assert np.isclose(A, np.array([0.4, 0, 0.4, 0, 0, 0, 0, 0.2, 0, 0])).all(axis=1).any()  # %10 adım örneği


class _P:
    def __init__(self, pid, cat, name=None):
        self.product_id, self.category, self.name = pid, cat, name or pid


class _S:
    def __init__(self, pid, cat, score, selected):
        self.product, self.score, self.selected = _P(pid, cat), score, selected


def test_mapping_splits_large_shares_and_prefers_low_tracking_error(monkeypatch):
    te = {"AAA": 0.01, "BBB": 0.02, "CCC": 0.20}
    monkeypatch.setattr(rc, "tracking_errors", lambda codes, cat, w: {c: te.get(c) for c in codes})
    scored = [_S("FON:CCC", "para_piyasasi", 0.95, True), _S("FON:AAA", "para_piyasasi", 0.90, True),
              _S("FON:BBB", "para_piyasasi", 0.80, False), _S("FON:HHH", "hisse", 0.7, True)]
    rows, notes = rc.map_to_funds({"para_piyasasi": 0.5, "hisse": 0.1}, scored)
    pp = [r for r in rows if r["kategori"] == "para_piyasasi"]
    assert [r["product_id"] for r in pp] == ["FON:AAA", "FON:BBB"]           # CCC yüksek takip farkı nedeniyle atlandı
    assert all(abs(r["agirlik"] - 0.25) < 1e-9 for r in pp)                  # %50 → 2 × %25
    hs = [r for r in rows if r["kategori"] == "hisse"]
    assert len(hs) == 1 and hs[0]["agirlik"] == 0.1 and hs[0]["takip_uyarisi"]  # tek aday, takip farkı ölçülemedi → uyarı
    assert any("hisse" in n for n in notes)


def test_mapping_uses_high_te_fund_only_with_warning(monkeypatch):
    monkeypatch.setattr(rc, "tracking_errors", lambda codes, cat, w: {c: 0.5 for c in codes})
    rows, notes = rc.map_to_funds({"borclanma": 0.2}, [_S("FON:XXX", "borclanma", 0.9, True)])
    assert rows[0]["product_id"] == "FON:XXX" and rows[0]["takip_uyarisi"] and notes


def test_current_vector_normalizes_and_rejects_unknown():
    v, notes = rc.current_vector({"mevduat_tl": 80, "para_piyasasi": 20})
    assert abs(v.sum() - 1) < 1e-12 and abs(v[rc.CATS.index("mevduat_tl")] - 0.8) < 1e-12 and notes
    v0, n0 = rc.current_vector(None)
    assert v0.sum() == 0 and "nakit" in n0[0]
    with pytest.raises(ValueError):
        rc.current_vector({"kripto": 1.0})


def test_window_ok_excludes_windows_touching_invalid_days():
    g = np.ones(30, bool)
    g[15] = False                                   # 15. gün geçersiz (boşluk ardı)
    ok = rc._window_ok(g, 5, 25)
    assert not ok[:4].any()                         # geri pencere için yeterli geçmiş yok
    assert ok[4] and ok[9]                          # 15'e dokunmayan pencereler geçerli
    assert not ok[10:16].any()                      # ileri pencere 15'i içeriyor (10..14) veya geri pencere içeriyor (15)
    assert not ok[16:20].any() and ok[20]           # geri pencere 15'i içeriyor (16..19); 20 temiz


@pytest.mark.skipif(not (rc.GECMIS_DIR / "meta.json").exists(), reason="tarihçe serileri yok")
def test_v2_feature_set_uses_history_series_and_is_gap_safe():
    ds2 = rc.build_dataset("v2")
    assert ds2["gosterge_seti"] == "v2"
    assert {"Politika faizi (karar)", "TLREF", "BIST 100 toplam getiri 3 ay"} <= set(ds2["feature_names"])
    for k in ("train", "val", "test"):
        assert not np.isnan(ds2["features"][ds2[k]]).any()
    s = rc.hist("tlref")
    assert s.index.is_monotonic_increasing and (s.between(0, 1)).all()


# --- BIST 30 (doğrudan hisse) kategorisi -----------------------------------------------------------

HAS_BIST = (rc.BIST_DIR / "meta.json").exists()


def test_category_lists_are_versioned():
    assert rc.cats_for("v1") == rc.CATS_V1 and len(rc.CATS_V1) == 10       # canlı eski model bozulmaz
    assert rc.cats_for("v2")[:10] == rc.CATS_V1 and rc.cats_for("v2")[-1] == "bist30_hisse"
    A2 = rc.action_grid(rc.CATS_V2)
    assert A2.shape[1] == 11 and np.allclose(A2.sum(axis=1), 1) and A2[:, -1].max() <= 0.5 + 1e-9


def test_high_risk_ban_and_risk_limit_close_bist30():
    cats = rc.CATS_V2
    A2 = rc.action_grid(cats)
    rd = {**RD, "bist30_hisse": 6}
    j = cats.index("bist30_hisse")
    m, why = rc.profile_mask(prof(risk_profili="Agresif", max_rd=7, yuksek_risk_yasagi=True), A2, rd, cats)
    assert A2[m][:, j].max() == 0 and any(w.startswith("bist30_hisse") for w in why)
    m, _ = rc.profile_mask(prof(max_rd=5), A2, rd, cats)
    assert A2[m][:, j].max() == 0
    m, _ = rc.profile_mask(prof(risk_profili="Agresif", max_rd=7), A2, rd, cats)
    assert A2[m][:, j].max() > 0


def test_current_vector_respects_model_categories():
    v, _ = rc.current_vector({"mevduat_tl": 1.0, "bist30_hisse": 0.0}, rc.CATS_V1)   # sıfır pay yok sayılır
    assert len(v) == 10
    with pytest.raises(ValueError):
        rc.current_vector({"bist30_hisse": 0.3, "mevduat_tl": 0.7}, rc.CATS_V1)
    v2, _ = rc.current_vector({"bist30_hisse": 0.3, "mevduat_tl": 0.7}, rc.CATS_V2)
    assert abs(v2[-1] - 0.3) < 1e-12


def test_stock_mapping_splits_across_basket(monkeypatch):
    monkeypatch.setattr(rc, "stock_basket_tracking_error", lambda codes, w: 0.05)
    scored = [_S(f"HISSE:H{i}", "bist30_hisse", 1 - i / 10, i < 3) for i in range(8)]
    rows, notes = rc.map_to_funds({"bist30_hisse": 0.3}, scored)
    assert len(rows) == 5 and all(abs(r["agirlik"] - 0.06) < 1e-9 for r in rows)   # %30 → 5 × %6
    assert rows[0]["product_id"] == "HISSE:H0" and not rows[0]["takip_uyarisi"] and not notes
    monkeypatch.setattr(rc, "stock_basket_tracking_error", lambda codes, w: None)
    rows, notes = rc.map_to_funds({"bist30_hisse": 0.2}, scored[:2])
    assert len(rows) == 2 and rows[0]["takip_uyarisi"] and len(notes) == 2           # az hisse + ölçülemeyen takip farkı


@pytest.mark.skipif(not HAS_BIST, reason="BIST verisi yok")
def test_bist30_basket_and_xu030_indicator_in_v2():
    ds2 = rc.build_dataset("v2")
    assert ds2["cats"] == rc.CATS_V2 and "BIST 30 endeksi 3 ay" in ds2["feature_names"]
    j = ds2["cats"].index("bist30_hisse")
    assert (ds2["tax"][:, j] == rc.cfg()["bist30"]["stopaj"]).all()
    assert ds2["members"]["bist30_hisse"] >= rc.cfg()["bist30"]["min_hisse"]
    for k in ("train", "val"):
        assert not np.isnan(ds2["real"][ds2[k]][:, j]).any()
    assert any("hayatta kalma" in n for n in ds2["notes"])


@pytest.mark.skipif(not HAS_BIST, reason="BIST verisi yok")
def test_universe_has_bist30_stock_products():
    from app import universe
    meta = rc.bist_meta()
    flags = []
    prods = universe._stock_products({}, flags)
    assert len(prods) == len(rc.bist_stock_codes()) > 0
    p = prods[0]
    assert p.product_id.startswith("HISSE:") and p.category == "bist30_hisse" and p.currency == "TRY"
    assert p.fields["rd"].status == "assumption" and p.fields["getiri_1y"].source.startswith(meta["kaynak"][:5])
    assert any("teyit" == f.level and "BIST 30" in f.message for f in flags)


def test_new_direct_products_map_to_proxy_category_with_note(monkeypatch):
    monkeypatch.setattr(rc, "tracking_errors", lambda codes, cat, w: {c: 0.01 for c in codes})
    scored = [_S("DIBS:TL_SABIT_2Y", "dibs_tl", 0.9, True), _S("FON:BBB", "borclanma", 0.8, True),
              _S("MADEN_HESABI:XAU", "maden_hesabi", 0.7, True)]
    rows, notes = rc.map_to_funds({"borclanma": 0.2, "kiymetli_maden": 0.1}, scored)
    b = [r for r in rows if r["kategori"] == "borclanma"][0]
    assert b["product_id"] == "DIBS:TL_SABIT_2Y" and b["vekil_kategori"] and "vekil" in b["takip_uyarisi"]
    m = [r for r in rows if r["kategori"] == "kiymetli_maden"][0]
    assert m["product_id"] == "MADEN_HESABI:XAU" and m["vekil_kategori"]
    assert sum("vekil" in n for n in notes) == 2


def test_nominal_principal_penalty_and_cap(monkeypatch):
    """Nominal modda: reel olarak eriyen ama nominal anaparayı koruyan dağılım cezalanmaz; ceza adım başına tavanlı."""
    ds = {"real": np.array([[-0.05, -0.30]]), "nominal_net": np.array([[0.10, -0.25]]), "step": 63, "cats": ["a", "b"]}
    A = np.array([[1.0, 0.0], [0.0, 1.0]])
    prof_ = prof(anapara="Yüksek")
    for mode, expect_pen_a in (("nominal", False), ("reel", True)):
        monkeypatch.setattr(rc, "cfg", lambda m=mode: {**rc.decision_config()["rl_kategori"], "anapara_tabani": m, "ceza_tavan": 0.25})
        sim = rc.Sim(ds, A, [prof_], np.ones((1, 2), bool))
        w, r, _ = sim.outcome(np.array([0, 0]), np.ones((2, 2)), np.zeros((2, 2)), np.array([0, 0]), np.array([0, 1]))
        cost = sim.cost                                       # nakitten giriş devri = 1
        pen_a = -0.05 - cost * 1 - r[0]                         # reel değişim − ödül = ceza
        assert (pen_a > 1e-9) == expect_pen_a
        assert abs(w[0, 1] - (1.10 - cost)) < 1e-12 and abs(w[0, 0] - (0.95 - cost)) < 1e-12
        pen_b = (-0.30 - cost) - r[1]
        assert abs(pen_b - 0.25) < 1e-9                         # λ=5 × açık >> tavan → 0,25


def test_gate_falls_back_to_baseline_unless_model_clearly_better(monkeypatch, tmp_path):
    A = rc.action_grid(rc.CATS_V1)
    mask = rc.profile_mask(prof(), A, RD, rc.CATS_V1)[0]
    base = rc.baseline_action(A, mask, rc.CATS_V1)
    assert abs(A[base][CI["para_piyasasi"]] - 0.5) < 1e-9 and abs(A[base][CI["mevduat_tl"]] - 0.5) < 1e-9
    other = int(np.where(mask)[0][0] if np.where(mask)[0][0] != base else np.where(mask)[0][1])
    meta = {"olusturma": "X"}
    monkeypatch.setattr(rc, "MODEL_DIR", Path(tmp_path))
    (Path(tmp_path) / "kapi.json").write_text('{"model_olusturma": "X", "delta": 0.5}', encoding="utf-8")
    q = np.zeros(len(A))
    q[other], q[base] = 1.0, 0.8                             # model yalnızca 0,2 iyi < eşik 0,5 → taban
    g = rc.apply_gate(meta, q, other, A, mask, rc.CATS_V1)
    assert g["kaynak"] == "taban_kural" and g["aksiyon"] == base and "taban kural" in g["gerekce"]
    q[other] = 2.0                                           # 1,2 iyi > eşik → model
    g = rc.apply_gate(meta, q, other, A, mask, rc.CATS_V1)
    assert g["kaynak"] == "model" and g["aksiyon"] == other
    assert rc.apply_gate({"olusturma": "Y"}, q, other, A, mask, rc.CATS_V1) is None     # başka modelin kapısı kullanılmaz



def test_category_caps_limit_risky_bets_but_every_profile_stays_feasible():
    """Oynak kategoriler en fazla %40, düşük oynaklıklılar config'teki sınır, mevduat sınırsız; tüm brief profillerinde en az bir izinli
    dağılım ve taban kural (%50 PP + %50 mevduat/borçlanma) her zaman izinli."""
    cats = rc.CATS_V2
    A2 = rc.action_grid(cats)
    caps = rc.category_caps(cats)
    assert (A2 <= caps + 1e-9).all() and caps[cats.index("kiymetli_maden")] == 0.4
    assert caps[cats.index("para_piyasasi")] == rc.cfg().get("dusuk_oynaklik_max_agirlik", 0.5)
    rd = {**RD, "bist30_hisse": 7}
    for p in rc.enumerate_profiles()[0]:
        m = rc.profile_mask(p, A2, rd, cats)[0]
        assert m.any()
        b = rc.baseline_action(A2, m, cats)
        assert m[b] and abs(A2[b].sum() - 1) < 1e-9


def test_eval_horizon_caps_episode_length_without_changing_draws():
    T = np.array([1, 3, 4, 20, 40])
    assert rc.eval_horizon(T).tolist() == [1, 3, 4, 4, 4]



def test_mapping_enforces_per_fund_cap_for_large_low_vol_share(monkeypatch):
    """Kategori payı %100 olsa da tek fona %50'den fazla verilmez: pay en az ceil(pay/%50) fona bölünür."""
    monkeypatch.setattr(rc, "tracking_errors", lambda codes, cat, w: {c: 0.01 for c in codes})
    scored = [_S("FON:A", "para_piyasasi", 0.9, True), _S("FON:B", "para_piyasasi", 0.8, True), _S("FON:C", "para_piyasasi", 0.7, False)]
    rows, notes = rc.map_to_funds({"para_piyasasi": 1.0}, scored)
    assert len(rows) == 2 and all(abs(r["agirlik"] - 0.5) < 1e-9 for r in rows)
    rows, notes = rc.map_to_funds({"para_piyasasi": 1.0}, scored[:1])
    assert len(rows) == 1 and any("fon başına" in n for n in notes)       # yeterli fon yoksa RM uyarılır
