"""WSL: karar kuralı karşılaştırması — argmax (en olası aksiyon) ile ortalama politika izdüşümü (izinli aksiyonlar içinde
beklenen dağılıma Σ_a π(a)·A_a en yakın aksiyon). Aynı eğitilmiş modeller, aynı dışa aktarılmış senaryo izleri.
Seçim yalnızca doğrulama (persona doğrulama farkı, HPO tohum skoru); test yalnızca raporlanır.

Kullanım: python karar_kurali.py --env .../rl_ortam_v13_v2.npz --zip a.zip b.zip
"""
import argparse
import json
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_gbrl import Core, replay  # noqa: E402


def seed_score(d):
    d = np.array(d)
    return float(d.mean() + 3 * np.minimum(d, 0).sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--zip", nargs="+", required=True)
    args = ap.parse_args()
    d = dict(np.load(args.env))
    base = d["base"]
    for z in args.zip:
        targs = json.load(open(os.path.join(os.path.dirname(z), "argumanlar.json")))
        core = Core(d)
        core.set_features(targs["kirpma_turu"], targs["kirp"])
        import io
        import zipfile
        sd = {k: v.cuda() for k, v in torch.load(io.BytesIO(zipfile.ZipFile(z).read("policy.pth")), map_location="cpu", weights_only=True).items()}
        A_t = torch.as_tensor(core.A, dtype=torch.float32, device="cuda")
        idx = sorted({int(k.split(".")[2]) for k in sd if k.startswith("mlp_extractor.policy_net.") and k.endswith(".weight")})
        G = torch.relu(sd["action_net.A"] @ sd["action_net.emb.0.weight"].T + sd["action_net.emb.0.bias"]) @ sd["action_net.emb.2.weight"].T + sd["action_net.emb.2.bias"]

        def probs(obs, pid):
            out = []
            with torch.no_grad():
                for s in range(0, len(obs), 4096):
                    h = torch.as_tensor(obs[s:s + 4096], device="cuda")
                    for j in idx:
                        h = torch.relu(h @ sd[f"mlp_extractor.policy_net.{j}.weight"].T + sd[f"mlp_extractor.policy_net.{j}.bias"])
                    lg = (h @ sd["action_net.proj.weight"].T + sd["action_net.proj.bias"]) @ G.T + sd["action_net.bias"]
                    m = torch.as_tensor(core.M[pid[s:s + 4096]], device="cuda")
                    out.append(torch.softmax(torch.where(m, lg, torch.full_like(lg, -1e9)), 1))
            return torch.cat(out)

        def act_argmax(obs, pid):
            return probs(obs, pid).argmax(1).cpu().numpy()

        def act_proj(obs, pid):
            p = probs(obs, pid)
            e = p @ A_t                                              # beklenen dağılım
            dist = (A_t ** 2).sum(1)[None] - 2 * e @ A_t.T          # ||A_a − e||² (sabit terim hariç)
            m = torch.as_tensor(core.M[pid], device=dist.device)
            return torch.where(m, dist, torch.full_like(dist, 1e9)).argmin(1).cpu().numpy()

        n = len(d["persona_ids"])
        rule_val = [replay(core, d, f"personaval{i}", lambda o, p: base[p])["ortalama_odul"] for i in range(n)]
        rule_test = [replay(core, d, f"persona{i}", lambda o, p: base[p])["ortalama_reel_kazanc"] for i in range(n)]
        rule_hold = replay(core, d, "test_hold", lambda o, p: base[p])["ortalama_reel_kazanc"]
        for name, fn in (("argmax", act_argmax), ("izdusum", act_proj)):
            val = [replay(core, d, f"personaval{i}", fn)["ortalama_odul"] - rule_val[i] for i in range(n)]
            vh = [replay(core, d, k, fn)["ortalama_odul"] - replay(core, d, k, lambda o, p: base[p])["ortalama_odul"] for k in ("val_a", "val_b")]
            te = [replay(core, d, f"persona{i}", fn)["ortalama_reel_kazanc"] - rule_test[i] for i in range(n)]
            th = replay(core, d, "test_hold", fn)["ortalama_reel_kazanc"]
            print(json.dumps({"model": z.split("/models/")[-1], "kural": name, "dogrulama_skor": round(seed_score(val) + 0.5 * min(vh), 4),
                              "dogrulama_persona": [round(x * 100, 2) for x in val], "dogrulama_yarilar": [round(x * 100, 2) for x in vh],
                              "TEST_persona_reel": [round(x * 100, 2) for x in te], "TEST_gorulmemis": round(th * 100, 2),
                              "TEST_kural_gorulmemis": round(rule_hold * 100, 2)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
