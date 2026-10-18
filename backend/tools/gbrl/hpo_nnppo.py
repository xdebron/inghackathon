"""WSL: NN-PPO için erken durdurmasız ("soğuk") hiperparametre araması (Optuna TPE).

Her deneme = bir hiperparametre ayarı × 3 tohum; her koşu sıfırdan, sabit bütçeyle sonuna kadar eğitilir ve yalnızca SON
ağırlıklarla değerlendirilir (train_gbrl.py --son-agirlik). Seçim yalnızca doğrulama dönemine dayanır:
    tohum skoru = ortalama(persona farkı) + 3·Σ min(persona farkı, 0) + 0,5·min(görülmemiş profiller, iki doğrulama yarısı)
    deneme skoru = tohumların ortalaması − 0,5·tohumlar arası std
(persona farkı: personanın doğrulama izlerinde kurala göre ödül farkı). Test alanları seçimde kullanılmaz; yalnızca
kazanan ayar için okunur (raporlama).

Kullanım (WSL):  ~/gbrl-env/bin/python hpo_nnppo.py --env .../rl_ortam_v7.npz --out .../hpo_nnppo --deneme 12
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


def run_one(env, out, params, seed, log):
    cmd = [sys.executable, os.path.join(HERE, "train_gbrl.py"), "--algo", "nnppo", "--son-agirlik", "--env", env, "--out", out,
           "--seed", str(seed), "--ortam", "512", "--adim", str(params["adim"]), "--n-steps", str(params["n_steps"]),
           "--epoch", str(params["epoch"]), "--lr", str(params["lr"]), "--ent", str(params["ent"]),
           "--gizli", str(params["gizli"]), "--indirim", str(params["indirim"]), "--minibatch", str(params["minibatch"]),
           "--persona-oran", str(params["persona_oran"])]
    with open(log, "w") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
    path = os.path.join(out, "sonuc.json")
    if rc != 0 or not os.path.exists(path):
        return None
    return json.load(open(path))


def seed_score(r):
    d = np.array(r["persona_dogrulama_farki"])
    general = min(r.get("dogrulama_yarilar") or [0.0])
    return float(d.mean() + 3 * np.minimum(d, 0).sum() + 0.5 * general)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--deneme", type=int, default=12)
    ap.add_argument("--tohum", type=int, default=3)
    ap.add_argument("--paralel", type=int, default=2, help="aynı anda koşan deneme (her biri --tohum süreç)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=0, n_startup_trials=6),
                                storage=f"sqlite:///{args.out}/optuna.db", study_name="nnppo_soguk", load_if_exists=True)
    logf = open(os.path.join(args.out, "hpo_log.jsonl"), "a")
    t0 = time.time()

    def objective(trial):
        params = {
            "lr": trial.suggest_float("lr", 1e-5, 1e-3, log=True),
            "ent": trial.suggest_float("ent", 0.0, 0.01),
            "epoch": trial.suggest_int("epoch", 2, 10),
            "gizli": trial.suggest_categorical("gizli", [64, 128, 256]),
            "indirim": trial.suggest_float("indirim", 0.9, 0.99),
            "n_steps": trial.suggest_categorical("n_steps", [8, 16, 32]),
            "minibatch": trial.suggest_categorical("minibatch", [1024, 2048, 4096]),
            "persona_oran": trial.suggest_float("persona_oran", 0.0, 0.5),
            "adim": trial.suggest_categorical("adim", [2_000_000, 6_000_000]),
        }
        tdir = os.path.join(args.out, f"deneme{trial.number:02d}")
        os.makedirs(tdir, exist_ok=True)
        with ThreadPoolExecutor(args.tohum) as ex:
            futs = [ex.submit(run_one, args.env, os.path.join(tdir, f"tohum{s}"), params, s, os.path.join(tdir, f"tohum{s}.log"))
                    for s in range(args.tohum)]
            res = [f.result() for f in futs]
        ok = [r for r in res if r]
        if len(ok) < args.tohum:
            row = {"deneme": trial.number, "params": params, "hata": f"{args.tohum - len(ok)} tohum başarısız"}
            logf.write(json.dumps(row) + "\n"); logf.flush(); print(json.dumps(row), flush=True)
            raise optuna.TrialPruned()
        scores = [seed_score(r) for r in ok]
        score = float(np.mean(scores) - 0.5 * np.std(scores))
        pers = np.array([r["persona_dogrulama_farki"] for r in ok])
        row = {"deneme": trial.number, "dk": round((time.time() - t0) / 60, 1), "skor": round(score, 4),
               "tohum_skorlari": [round(x, 4) for x in scores], "params": params,
               "persona_dogrulama_farki_ort": np.round(pers.mean(0), 4).tolist(),
               "persona_dogrulama_farki_min": np.round(pers.min(0), 4).tolist()}
        logf.write(json.dumps(row) + "\n"); logf.flush(); print(json.dumps(row), flush=True)
        trial.set_user_attr("persona_ort", row["persona_dogrulama_farki_ort"])
        return score

    study.optimize(objective, n_trials=args.deneme, n_jobs=args.paralel)
    best = study.best_trial
    summary = {"en_iyi_deneme": best.number, "skor": best.value, "params": best.params, "sure_dk": round((time.time() - t0) / 60, 1)}
    json.dump(summary, open(os.path.join(args.out, "en_iyi.json"), "w"), indent=1)
    print(json.dumps({"olay": "bitti", **summary}), flush=True)


if __name__ == "__main__":
    main()
