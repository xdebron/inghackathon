"""RL ortamını ve değerlendirme senaryolarını, başka bir çalışma ortamında (örn. WSL'de GBRL) aynen yeniden kurulabilecek
biçimde tek bir .npz dosyasına aktarır.

İçerik: adım getirileri (reel, nominal net), normalize ve kırpılmış göstergeler, havuzlar, aksiyon ızgarası, profil vektörleri ve
maskeleri, ufuk ve ceza parametreleri, taban kural aksiyonları, eğitim/görülmemiş/persona profil kimlikleri. Ayrıca
rl_category.evaluate'in rastgele senaryo izleri (bölüm uzunluğu ve adım adım başlangıç indeksleri) politikadan bağımsız
olduğu için birebir kaydedilir: dış ortamda değerlendirme aynı senaryolar üzerinde yapılır.

Kullanım:  backend/.venv/Scripts/python backend/tools/export_rl_env.py --out backend/models/rl_ortam_v5.npz
           [--baslangic 2025-01-01] [--test-baslangic 2026-01-01] [--anapara-tabani reel]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import rl_category as rc  # noqa: E402
from app.config import decision_config  # noqa: E402
from app.pipeline import load_persona  # noqa: E402
from app.profile import interpret  # noqa: E402


def scenario_paths(sim: rc.Sim, ids: np.ndarray, pool: np.ndarray, n_per: int, seed: int) -> dict:
    """rl_category.evaluate ile aynı rastgele sırayla: T, ilk indeks, her adımda next_idx."""
    rng = np.random.default_rng(seed)
    member = np.zeros(len(sim.ds["real"]) + sim.step + 1, bool)
    member[pool] = True
    pid = np.repeat(ids, n_per)
    T = rc.eval_horizon(rng.integers(sim.hmin[pid], sim.hmax[pid] + 1))
    idx = rng.choice(pool, size=len(pid))
    path = [idx]
    for _ in range(int(T.max()) - 1):
        idx = sim.next_idx(idx, member, pool, rng)
        path.append(idx)
    return {"pid": pid, "T": T, "idx": np.array(path)}


def constraint_variants(base: list[dict], n: int, rd: dict, cats_all: list[str], A: np.ndarray, seed: int = 7) -> tuple[list[dict], dict]:
    """Eğitim profilleri: brief kombinasyonları (katalog kısıtı yok) + n varyant. Varyant = rastgele bir brief kombinasyonu +
    katalogdan rastgele 1–3 kısıt; parametreler kuralın varsayılanı çevresinde küçük bir ızgaradan (oran ±%10, en az 3/4
    kategori, vade 31/62 gün). İzinli dağılımı kalmayan varyantlar atılır. Müşteri ya da persona değildir."""
    import copy
    rng = np.random.default_rng(seed)
    cat_rules = rc.catalog_rules()
    out = [dict(p, ek_kisit=[]) for p in base]
    tried = dropped = 0
    while len(out) < len(base) + n:
        tried += 1
        p = copy.deepcopy(base[rng.integers(len(base))])
        k = int(rng.integers(1, 4))
        picks = rng.choice(len(cat_rules), size=k, replace=False)
        ek = []
        for i in picks:
            r = cat_rules[i]
            prm = dict(r.get("parametre", {}))
            if "oran" in prm:
                prm["oran"] = float(np.clip(prm["oran"] + rng.choice([-0.1, 0.0, 0.1]), 0.1, 0.9))
            if "min_kategori" in prm:
                prm["min_kategori"] = int(rng.choice([3, 4]))
            if r["id"] == "K_VADE_SINIRI":
                prm["max_mevduat_vade_gun"] = int(rng.choice([31, 62]))
            ek.append({"id": r["id"], "rl": r["rl"], "parametre": prm})
        p["ek_kisit"] = ek
        if not rc.profile_mask(p, A, rd, cats_all)[0].any():
            dropped += 1
            continue
        p["id"] = f"V{len(out) - len(base):06d}"
        out.append(p)
    return out, {"varyant": n, "denenen": tried, "atilan_bos_maske": dropped, "katalog_kural": [r["id"] for r in cat_rules]}


def profile_reward_arrays(ds, A, cats, allp, test_start) -> dict:
    """Profil ödülü: r = reel getiri − Σ_k c_k · w_k(profil) · ceza_k(aksiyon) (+ döviz ve maliyet). Aksiyon cezaları:
    düşük oynaklık = wᵀΣw (Σ: test başlangıcından önce biten pencerelerin nominal getiri kovaryansı), likidite = Σ w·likit
    olmama (skorlamadaki valör/vade ölçeği: mevduat 3 ay 0,65; T+1 0,1; T+2 0,25; T+3 0,5), gelir = 1 − Σ w·gelir_üretimi,
    sadelik = 1 − Σ w·yapı_sadeliği, çeşitlendirme = Σ w² (yoğunlaşma endeksi), döviz = max(0, döviz payı − ½·döviz tavanı).
    Kategori öznitelikleri config `kategoriler` (deterministik skorlamayla aynı)."""
    import pandas as pd
    kat = decision_config()["kategoriler"]
    pc = rc.cfg()["profil_odulu"]
    ends = ds["dates"][ds["step"]:ds["step"] + len(ds["real"])]
    pre = np.where(np.asarray(ends) < np.datetime64(pd.Timestamp(test_start)))[0]
    pre = pre[np.isfinite(ds["nominal_net"][pre]).all(1)]
    S = np.cov(ds["nominal_net"][pre].T)
    illiq = np.array([0.65 if c == "mevduat_tl" else {0: 0.0, 1: 0.1, 2: 0.25, 3: 0.5}.get(kat[c]["valor_gun"], 0.7) for c in cats])
    gelir = np.array([kat[c]["gelir_uretimi"] for c in cats])
    sade = np.array([kat[c]["yapi_sadeligi"] for c in cats])
    fx = np.array([c in rc.FX_CATS for c in cats], float)
    PA = np.column_stack([np.einsum("ai,ij,aj->a", A, S, A), A @ illiq, 1 - A @ gelir, 1 - A @ sade, (A ** 2).sum(1)])
    keys = ["dusuk_oynaklik", "likidite", "gelir", "sadelik", "cesitlendirme"]
    K = np.array([[p["agirlik"][k] for k in keys] for p in allp])
    Kfx = np.array([p["agirlik"]["doviz_uyumu"] for p in allp])
    Kmal = np.array([p["agirlik"]["maliyet"] for p in allp])
    tav = rc.cfg()["doviz_tavan"]
    fx_tgt = np.array([0.5 * tav[str(float(p["fx"]))] for p in allp])
    return {"profil_PA": PA, "profil_K": K, "profil_c": np.array([pc[k] for k in keys]), "profil_fx_pay": A @ fx,
            "profil_fx_hedef": fx_tgt, "profil_Kfx": Kfx, "profil_cfx": pc["doviz_uyumu"],
            "profil_maliyet_carpani": 1 + pc["maliyet_carpani"] * Kmal, "profil_kov": S}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--baslangic", default="2025-01-01")
    ap.add_argument("--test-baslangic", default="2026-01-01")
    ap.add_argument("--anapara-tabani", choices=["reel", "nominal"], default="reel")
    ap.add_argument("--profil-tam", action="store_true",
                    help="profilin tamamı: state'te sürüm 2 profil vektörü (21 boyut), ödülde kriter ağırlıklı profil terimleri")
    ap.add_argument("--kisit-varyant", type=int, default=0,
                    help="katalog kısıtlı profil varyantı sayısı (brief kombinasyonu + rastgele 1–3 katalog kısıtı); profil sürüm 3")
    ap.add_argument("--gosterge-seti", default="v2", choices=["v2", "v3"], help="v3: çok ufuklu zamansal göstergeler")
    ap.add_argument("--test-bitis", action="store_true",
                    help="test havuzu = sonucu test başlangıcından sonraya uzanan pencereler (son 3 ay gibi kısa bir dönemi dışarıda tutmak için)")
    ap.add_argument("--tum-veri", action="store_true",
                    help="son model için: eğitim havuzu = eğitim ∪ doğrulama ∪ test (bugüne kadarki tüm geçerli pencereler). "
                         "Doğrulama/test izleri yine yazılır ama artık örneklem içidir; performans tahmini için kullanılmaz.")
    ap.add_argument("--haric", default=None, help="bu kategoriye ağırlık veren aksiyonları çıkar (GBRL çıkış sınırı için), örn. bist30_hisse")
    ap.add_argument("--izgara", default=None, help="'adim_birim:max_kategori[+...]', örn. 10:2+4:3 "
                    "(GBRL'in çıkış boyutu sınırı ~512 için). Verilmezse modelin tam ızgarası.")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    c, rl_cfg = rc.cfg(), decision_config()["rl"]
    ds = rc.build_dataset(args.gosterge_seti, args.baslangic, args.test_baslangic, test_by_end=args.test_bitis)
    clip = c["gpu"].get("ozellik_kirp")
    F = np.nan_to_num(ds["features"])
    F = np.clip(F, -clip, clip) if clip else F
    cats, A = ds["cats"], rc.action_grid(ds["cats"])
    if args.izgara:   # "10:2+4:3" = %10 adım ≤2 kategori ∪ %25 adım ≤3 kategori; sınırlar category_caps (aşağı yuvarlanır)
        parts = []
        for spec in args.izgara.split("+"):
            units, mx = map(int, spec.split(":"))
            caps = [int(np.floor(w * units + 1e-9)) for w in rc.category_caps(cats)]
            parts.append(np.array(rc._compositions(units, caps, mx), dtype=float) / units)
        allA = np.vstack(parts)
        _, ix = np.unique(np.round(allA, 6), axis=0, return_index=True)
        A = allA[np.sort(ix)]
    if args.haric:
        A = A[A[:, cats.index(args.haric)] == 0]
    profiles, pstats = rc.enumerate_profiles(full=args.profil_tam)
    pver = 2 if args.profil_tam else 1
    if args.kisit_varyant:
        pver = 3
        profiles, vstats = constraint_variants(profiles, args.kisit_varyant, ds["rd"], cats_all=cats, A=A)
        pstats = {**pstats, "katalog": vstats}
    personas = []
    for pid in ["P01", "P02", "P03", "P04", "P05"]:
        p = load_persona(pid)
        view, _ = interpret(p, {})
        personas.append(rc.profile_from_persona(p, view))
    allp = profiles + personas
    M = np.array([rc.profile_mask(p, A, ds["rd"], cats)[0] for p in allp])
    sim = rc.Sim(ds, A, allp, M, nominal=args.anapara_tabani == "nominal")
    if pver > 1:
        sim.pvec = np.array([rc.profile_vector(p, pver) for p in allp])
    order = np.random.default_rng(c["profil_tohum"] + 1).permutation(len(profiles))
    n_hold = int(round(len(profiles) * c["profil_holdout_orani"]))
    hold, train_ids = order[:n_hold], order[n_hold:]
    persona_ids = np.arange(len(profiles), len(allp))
    base = np.array([rc.baseline_action(A, M[i], cats) for i in range(len(allp))])
    train_pool = np.unique(np.concatenate([ds["train"], ds["val"], ds["test"]])) if args.tum_veri else ds["train"]
    out = {
        "real": ds["real"], "nominal_net": ds["nominal_net"], "F": F, "step": ds["step"],
        # ham göstergeler (eksikler ortalamayla; z = 0) + normalizasyon: eğitimde kırpma türü seçimi ve what-if şokları için
        "F_raw": np.where(np.isfinite(ds["features_raw"]), ds["features_raw"], ds["mu"][None]), "mu": ds["mu"], "sd": ds["sd"],
        "feature_names": np.array(ds["feature_names"]), "cats": np.array(cats), "inflation": ds["inflation"],
        "train": train_pool, "val": ds["val"], "test": ds["test"], "A": A, "M": M, "pvec": sim.pvec,
        "hmin": sim.hmin, "hmax": sim.hmax, "lam": sim.lam, "fixed": sim.fixed, "cost": sim.cost,
        "pen_cap": float(sim.pen_cap), "L": sim.L, "nominal": sim.nominal, "t_cap": rl_cfg["durum_max_adim"],
        "base": base, "train_ids": train_ids, "hold_ids": hold, "persona_ids": persona_ids,
        "p_cur": c.get("mevcut_portfoy_olasiligi", 0.0),
    }
    h = len(ds["val"]) // 2
    sets = {"val_hold": (hold, ds["val"], 8, 21), "test_hold": (hold, ds["test"], 8, 11),
            "val_a": (train_ids[:1000], ds["val"][:h], 4, 31), "val_b": (train_ids[:1000], ds["val"][h:], 4, 32)}
    for name, (ids, pool, n_per, seed) in sets.items():
        for k, v in scenario_paths(sim, ids, pool, n_per, seed).items():
            out[f"{name}_{k}"] = v
    for i, pid in enumerate(persona_ids):
        for k, v in scenario_paths(sim, np.array([pid]), ds["test"], 1000, 12).items():
            out[f"persona{i}_{k}"] = v
        for k, v in scenario_paths(sim, np.array([pid]), ds["val"], 400, 40 + i).items():   # seçim için doğrulama izleri
            out[f"personaval{i}_{k}"] = v
    if args.profil_tam:
        out.update(profile_reward_arrays(ds, A, cats, allp, args.test_baslangic))
    np.savez_compressed(args.out, **out)
    meta = {"cats": cats, "feature_names": ds["feature_names"], "period": ds["period"], "personas": [p["id"] for p in personas],
            "baslangic": args.baslangic, "test_baslangic": args.test_baslangic, "anapara_tabani": args.anapara_tabani,
            "havuz": {k: int(len(ds[k])) for k in ("train", "val", "test")}, "aksiyon": int(len(A)), "izgara": args.izgara, "haric": args.haric,
            "tum_veri": bool(args.tum_veri), "test_bitis": bool(args.test_bitis), "gosterge_seti": args.gosterge_seti, "egitim_havuzu": int(len(train_pool)),
            "profil_tam": bool(args.profil_tam), "profil_istatistik": pstats, "profil_boyutu": int(sim.pvec.shape[1]),
            "profil_surumu": pver}
    Path(args.out).with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta["havuz"] | {"aksiyon": meta["aksiyon"], "dosya": args.out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
