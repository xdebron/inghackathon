"""WSL: NN-PPO için soğuk (erken durdurmasız) HPO + özellik mühendisliği, what-if makullüğü amaçlı (Optuna TPE).

Arama uzayı: gösterge seti (v2 / v3 ortamı), girdi dönüşümü (sert kesme / tanh, sınır c), what-if senaryo artırımı (oran,
momentum κ), what-if tutarlılık düzenleyicisinin ağırlığı, öğrenme oranı, entropi, persona oranı. Mimari v11'de sabit (≈5M parametre, aksiyon gömmeli çıkış).

Her deneme = ayar × N tohum, sabit bütçe, yalnızca SON ağırlıklar. Seçim yalnızca DOĞRULAMA dönemine dayanır:
    getiri    = tohum skoru (hpo_nnppo.seed_score: persona farkları, kayıplar 3× + 0,5·görülmemiş yarıların en kötüsü)
    what-if   = doğru tepki − 3·yanlış yön − 0,5·max(0, en kötü senaryo yanlış − 0,2)   (doğrulama tarihlerinde)
    skor      = ort(getiri + λ·what-if) − 0,5·std   (λ = --lambda)
Test (son 3 ay) seçimde kullanılmaz; yalnızca kazanan için okunur.

Kullanım (WSL):  python hpo_whatif.py --envdir .../models --out .../models/hpo_v13 --deneme 16 --tohum 2 --paralel 3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from hpo_nnppo import seed_score  # noqa: E402

FIXED = ["--algo", "nnppo", "--son-agirlik", "--ortam", "512", "--n-steps", "8", "--epoch", "2", "--gizli", "1024",
         "--katman", "3", "--aktivasyon", "relu", "--aksiyon-gomme", "512", "--indirim", "0.9668248404375974",
         "--minibatch", "4096", "--ilerleme", "200"]


def whatif_score(w):
    return float(w["dogru_tepki"] - 3 * w["yanlis_yon"] - 0.5 * max(0.0, w["en_kotu_senaryo_yanlis"] - 0.2))


def run_one(envdir, out, p, seed, log, adim):
    cmd = [sys.executable, os.path.join(HERE, "train_gbrl.py"), *FIXED, "--env", os.path.join(envdir, f"rl_ortam_v13_{p['gosterge']}.npz"),
           "--out", out, "--seed", str(seed), "--adim", str(adim), "--lr", str(p["lr"]), "--ent", str(p["ent"]),
           "--persona-oran", str(p["persona_oran"]), "--kirpma-turu", p["kirpma"], "--kirp", str(p["kirp"]),
           "--senaryo-oran", str(p["senaryo_oran"]), "--momentum", str(p["momentum"]), "--tutarlilik", str(p["tutarlilik"])]
    with open(log, "w") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
    path = os.path.join(out, "sonuc.json")
    return json.load(open(path)) if rc == 0 and os.path.exists(path) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--envdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--deneme", type=int, default=16)
    ap.add_argument("--tohum", type=int, default=2)
    ap.add_argument("--paralel", type=int, default=3)
    ap.add_argument("--adim", type=int, default=6_000_000)
    ap.add_argument("--lambda", dest="lam", type=float, default=0.1)
    ap.add_argument("--baslangic", default=None, help="ilk denemeler için json listesi (enqueue)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=0, n_startup_trials=6),
                                storage=f"sqlite:///{args.out}/optuna.db", study_name="whatif_soguk", load_if_exists=True)
    if args.baslangic:
        for p in json.load(open(args.baslangic)):
            study.enqueue_trial(p)
    logf = open(os.path.join(args.out, "hpo_log.jsonl"), "a")
    t0 = time.time()

    def objective(trial):
        p = {
            "gosterge": trial.suggest_categorical("gosterge", ["v2", "v3"]),
            "kirpma": trial.suggest_categorical("kirpma", ["sert", "tanh"]),
            "kirp": trial.suggest_categorical("kirp", [2.0, 3.0, 4.0]),
            "senaryo_oran": trial.suggest_float("senaryo_oran", 0.0, 0.8),
            "momentum": trial.suggest_float("momentum", 0.0, 0.3),
            "tutarlilik": trial.suggest_float("tutarlilik", 0.5, 50.0, log=True),
            "lr": trial.suggest_float("lr", 1e-5, 2e-4, log=True),
            "ent": trial.suggest_float("ent", 0.0, 0.01),
            "persona_oran": trial.suggest_float("persona_oran", 0.0, 0.5),
        }
        tdir = os.path.join(args.out, f"deneme{trial.number:02d}")
        os.makedirs(tdir, exist_ok=True)
        with ThreadPoolExecutor(args.tohum) as ex:
            res = list(ex.map(lambda s: run_one(args.envdir, os.path.join(tdir, f"tohum{s}"), p, s,
                                                os.path.join(tdir, f"tohum{s}.log"), args.adim), range(args.tohum)))
        ok = [r for r in res if r and "whatif_dogrulama" in r]
        if len(ok) < args.tohum:
            row = {"deneme": trial.number, "params": p, "hata": f"{args.tohum - len(ok)} tohum başarısız"}
            logf.write(json.dumps(row) + "\n"); logf.flush(); print(json.dumps(row), flush=True)
            raise optuna.TrialPruned()
        perf = [seed_score(r) for r in ok]
        wi = [whatif_score(r["whatif_dogrulama"]) for r in ok]
        tot = [a + args.lam * b for a, b in zip(perf, wi)]
        score = float(np.mean(tot) - 0.5 * np.std(tot))
        W = [r["whatif_dogrulama"] for r in ok]
        row = {"deneme": trial.number, "dk": round((time.time() - t0) / 60, 1), "skor": round(score, 4),
               "getiri": round(float(np.mean(perf)), 4), "whatif": round(float(np.mean(wi)), 4), "params": p,
               "dogru_tepki": round(float(np.mean([w["dogru_tepki"] for w in W])), 3),
               "yanlis_yon": round(float(np.mean([w["yanlis_yon"] for w in W])), 3),
               "en_kotu_senaryo": round(float(np.max([w["en_kotu_senaryo_yanlis"] for w in W])), 3),
               "persona_dogrulama_farki_ort": np.round(np.mean([r["persona_dogrulama_farki"] for r in ok], 0), 4).tolist()}
        logf.write(json.dumps(row, ensure_ascii=False) + "\n"); logf.flush(); print(json.dumps(row, ensure_ascii=False), flush=True)
        return score

    study.optimize(objective, n_trials=args.deneme, n_jobs=args.paralel)
    best = study.best_trial
    summary = {"en_iyi_deneme": best.number, "skor": best.value, "params": best.params, "sure_dk": round((time.time() - t0) / 60, 1)}
    json.dump(summary, open(os.path.join(args.out, "en_iyi.json"), "w"), indent=1)
    print(json.dumps({"olay": "bitti", **summary}), flush=True)


if __name__ == "__main__":
    main()
