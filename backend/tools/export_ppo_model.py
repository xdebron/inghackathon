"""WSL'de eğitilen NN-PPO modelini (Stable-Baselines3 MaskablePPO zip) canlı çıkarım klasörüne aktarır: numpy ağırlıkları +
meta.json (rl_category.infer'in beklediği alanlar). Backend Stable-Baselines3 gerektirmez.

Kullanım:  backend/.venv/Scripts/python backend/tools/export_ppo_model.py --zip models/nnppo_v8_tumveri/model_nnppo.zip \
               --ortam models/rl_ortam_v8_tum.npz --tahmin models/nnppo_v8 --out models/rl_ppo_v8

--tahmin: aynı ayarla eğitim/test ayrımlı koşuların klasörü (tohum*/sonuc.json); test sonuçları performans tahmini olarak
meta'ya yazılır (tüm veriyle eğitilen modelin kendi test dönemi sonuçları örneklem içidir, kullanılmaz).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import rl_category as rc  # noqa: E402
from app.config import config_hash, now_iso  # noqa: E402

MODEL_LABEL = "Model (PPO, profile koşullu)"
RULE_LABEL = "Para piyasası + mevduat/borçlanma"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--ortam", required=True)
    ap.add_argument("--tahmin", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baslangic", default="2025-01-01")
    ap.add_argument("--test-baslangic", default="2026-01-01")
    ap.add_argument("--hp", default=None, help="hiperparametre json (varsayılan models/hpo_nnppo/en_iyi.json)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    import torch

    z = zipfile.ZipFile(args.zip)
    sd = torch.load(io.BytesIO(z.read("policy.pth")), map_location="cpu", weights_only=True)
    sb3 = json.loads(z.read("data"))
    activation = "relu" if "ReLU" in str(sb3.get("policy_kwargs", "")) else "tanh"
    np_ = lambda k: sd[k].numpy().astype(np.float64)
    W = {}
    pi_idx = sorted({int(k.split(".")[2]) for k in sd if k.startswith("mlp_extractor.policy_net.") and k.endswith(".weight")})
    vf_idx = sorted({int(k.split(".")[2]) for k in sd if k.startswith("mlp_extractor.value_net.") and k.endswith(".weight")})
    for i, j in enumerate(pi_idx):
        W[f"L{i}W"], W[f"L{i}b"] = np_(f"mlp_extractor.policy_net.{j}.weight").T, np_(f"mlp_extractor.policy_net.{j}.bias")
    for i, j in enumerate(vf_idx):
        W[f"V{i}W"], W[f"V{i}b"] = np_(f"mlp_extractor.value_net.{j}.weight").T, np_(f"mlp_extractor.value_net.{j}.bias")
    W["VW"], W["Vb"] = np_("value_net.weight").T, np_("value_net.bias")
    if "action_net.proj.weight" in sd:      # aksiyon gömmeli çıkış: gömmeler bir kez hesaplanır (g(A), ReLU ara katman)
        Abuf = np_("action_net.A")
        e1 = np.maximum(Abuf @ np_("action_net.emb.0.weight").T + np_("action_net.emb.0.bias"), 0.0)
        W["G"] = e1 @ np_("action_net.emb.2.weight").T + np_("action_net.emb.2.bias")
        W["PW"], W["Pb"], W["Ab"] = np_("action_net.proj.weight").T, np_("action_net.proj.bias"), np_("action_net.bias")
        head = "aksiyon_gomme"
    else:
        W["AW"], W["Ab"] = np_("action_net.weight").T, np_("action_net.bias")
        head = "dogrusal"
    n_params = int(sum(v.numel() for k, v in sd.items() if k != "action_net.A"))

    env = np.load(args.ortam)
    env_meta = json.loads(Path(args.ortam).with_suffix(".json").read_text(encoding="utf-8"))
    gset = env_meta.get("gosterge_seti", "v2")
    ds = rc.build_dataset(gset, args.baslangic, args.test_baslangic, test_by_end=env_meta.get("test_bitis", False))
    assert ds["feature_names"] == env_meta["feature_names"], "gösterge listesi ortamla uyuşmuyor"
    targs = json.loads((Path(args.zip).parent / "argumanlar.json").read_text(encoding="utf-8"))         if (Path(args.zip).parent / "argumanlar.json").exists() else {}
    clip = targs.get("kirp") or rc.cfg()["gpu"]["ozellik_kirp"]
    squash = targs.get("kirpma_turu", "sert")
    in_meta = {"ozellik_kirp": clip, "kirpma_turu": squash}
    F = rc.model_input(ds["features"], in_meta)
    Fe = rc.model_input((env["F_raw"] - env["mu"]) / env["sd"], in_meta) if "F_raw" in env else env["F"]
    gap = float(np.abs(F - Fe).max())
    assert gap < 1e-9, f"normalize göstergeler ortamdan farklı: {gap}"          # aynı veri ve normalizasyon

    pol = rc.PPOPolicy(W, activation)
    n_state = 3 if bool(env["nominal"]) else 2          # süre + reel servet (+ nominal servet: nominal anapara modunda)
    A = env["A"]
    cats = env_meta["cats"]
    # numpy politikası, eğitim ortamındaki persona kararlarını aynen vermeli
    first = json.loads((Path(args.zip).parent / "sonuc.json").read_text(encoding="utf-8"))["persona_ilk_karar"]
    for i, p in enumerate(env["persona_ids"]):
        idx0 = int(env[f"persona{i}_idx"][0][0])
        x = np.concatenate([Fe[idx0], env["pvec"][p], np.zeros(n_state), np.zeros(len(cats))])[None, :]
        a = int(np.argmax(np.where(env["M"][p], pol.forward(x)[0], -np.inf)))
        got = {str(j): round(float(A[a][j]), 2) for j in range(len(cats)) if A[a][j] > 0}
        assert got == {str(k): v for k, v in first[i].items()}, (i, got, first[i])

    # performans tahmini: aynı ayarla eğitim/test ayrımlı koşular (tohum ortalaması)
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args.tahmin).glob("tohum*/sonuc.json"))]
    def avg(key, sub=None):
        vals = [r[key] if sub is None else r[key][sub] for r in runs]
        return float(np.mean(vals))
    t_keys = ("ortalama_reel_kazanc", "kotu_yuzde5", "anapara_alti_bitis_olasiligi", "nominal_anapara_alti_bitis_olasiligi", "ortalama_odul")
    rule_test = None
    log0 = sorted(Path(args.tahmin).glob("tohum*/egitim_log.jsonl"))[0]
    for line in log0.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("olay") == "taban_test":
            rule_test = {k: row.get(k) for k in t_keys}
    persona_names = ["P01", "P02", "P03", "P04", "P05"]
    test = {"gorulmemis_profiller": {MODEL_LABEL: {k: avg("test", k) for k in t_keys}, RULE_LABEL: rule_test},
            "personalar": {}, "not": (f"Performans tahmini: aynı ayarlarla eğitim/test ayrımlı {len(runs)} tohumun ortalaması "
                                      f"(test {args.test_baslangic} sonrası, bölüm ≤ 4 çeyrek). Canlı model aynı ayarla tüm veride "
                                      "eğitildi; onun test dönemi sonuçları örneklem içidir.")}
    for i, pid in enumerate(persona_names):
        test["personalar"][pid] = {
            MODEL_LABEL: {"ortalama_reel_kazanc": float(np.mean([r["persona"][i] for r in runs])),
                          "ortalama_odul": float(np.mean([r["persona_odul"][i] for r in runs])),
                          "en_kotu_tohum_reel_kazanc": float(np.min([r["persona"][i] for r in runs]))},
            RULE_LABEL: {"ortalama_reel_kazanc": runs[0]["taban_persona"][i], "ortalama_odul": runs[0]["taban_persona_odul"][i]}}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    np.savez(out / "model.npz", **W)
    hp = json.loads(Path(args.hp or (Path(ROOT) / "models/hpo_nnppo/en_iyi.json")).read_text())
    meta = {
        "olusturma": now_iso(), "model_turu": "ppo", "aktivasyon": activation, "cikis_turu": head, "parametre": n_params,
        "katmanlar": [int(W[f"L{i}W"].shape[1]) for i in range(len(pi_idx))], "model_adi": "NN-PPO (MaskablePPO), profile koşullu, profilin tamamı" + (" + kısıt kataloğu" if env_meta.get("profil_surumu", 2) >= 3 else ""),
        "kaynak_zip": str(Path(args.zip).as_posix()), "sb3_policy_kwargs": sb3.get("policy_kwargs"),
        "config_hash": config_hash(), "kategoriler": cats, "kategori_rd": ds["rd"], "aksiyonlar": A.tolist(),
        "gosterge_seti": gset, "gosterge_adlari": ds["feature_names"], "gosterge_ort": ds["mu"].tolist(),
        "gosterge_std": ds["sd"].tolist(), "ozellik_kirp": clip, "kirpma_turu": squash, "senaryo_artirimi": targs.get("senaryo_oran", 0.0),
        "durum_boyutu": n_state, "profil_surumu": env_meta.get("profil_surumu", 2), "katalog_kisitlar": [r["id"] for r in rc.catalog_rules()],
        "anapara_tabani": env_meta["anapara_tabani"], "veri_baslangic": args.baslangic, "test_baslangic": args.test_baslangic,
        "egitim_verisi": "tüm veri (eğitim ∪ doğrulama ∪ test pencereleri)", "egitim_havuzu": env_meta.get("egitim_havuzu"),
        "donem": {"baslangic": ds["period"]["baslangic"], "bitis": ds["period"]["bitis"], "egitim": ds["period"]["egitim"],
                  "dogrulama": ds["period"]["dogrulama"], "test": ds["period"]["test"]},
        "profil_istatistik": env_meta.get("profil_istatistik"), "profil_sayisi": {"egitim": int(len(env["train_ids"])),
                                                                                "gorulmemis": int(len(env["hold_ids"]))},
        "hiperparametreler": hp["params"], "hpo": {"deneme": 12, "tohum": 3, "secim": "yalnızca doğrulama, son ağırlıklar"},
        "rl_kategori": rc.cfg(), "rl": rc.decision_config()["rl"],
        "guncelleme": int(hp["params"]["adim"]), "en_iyi_guncelleme": None, "egri": [], "test": test,
        "mevcut_portfoy_egitimi": True, "veri_notlari": ds["notes"],
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    (out / "durum.json").write_text(json.dumps({"bitti": True, "guncelleme": meta["guncelleme"], "toplam": meta["guncelleme"],
                                                "model_turu": "ppo"}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"dosya": str(out), "aksiyon": len(A), "girdi": W["L0W"].shape[0], "parametre": n_params, "cikis": head,
                      "dogrulama": "kararlar eşleşti"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
