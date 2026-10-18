"""Profile koşullu kategori DQN'inin uzun (çevrimdışı) eğitimi.

Kullanım:  backend/.venv/Scripts/python backend/tools/train_rl.py [--updates N] [--tag ad]

Çıktılar (backend/models/rl_kategori/):
  model.npz          doğrulamada en iyi ağırlıklar
  meta.json          veri dönemi, göstergeler ve normalizasyon, aksiyonlar, kategori RD'leri, eğitim eğrisi,
                     test sonuçları (eğitim profilleri, görülmemiş profiller, 5 persona; DQN ve sabit kıyaslar)
  egitim_log.jsonl   değerlendirme anlarındaki kayıtlar
  durum.json         canlı ilerleme (güncelleme, geçen süre, tahmini kalan süre, en iyi doğrulama)
Sentetik profil parametreleri: backend/data/synthetic/profil_parametreleri.json (müşteri/persona DEĞİLDİR).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import rl_category as rc  # noqa: E402
from app.config import config_hash, decision_config, now_iso  # noqa: E402
from app.pipeline import load_persona  # noqa: E402
from app.profile import interpret  # noqa: E402

OUT = ROOT / "models" / "rl_kategori"
SYN = ROOT / "data" / "synthetic"
POLICIES = {"dqn": "DQN (profile koşullu)", "esit": "Eşit ağırlık (izinli kategoriler)",
            "en_guvenli": "En düşük oynaklıklı izinli dağılım", "pp_agirlikli": "Para piyasası + mevduat/borçlanma"}


def main():
    ap = argparse.ArgumentParser()
    c = rc.cfg()
    ap.add_argument("--updates", type=int, default=None)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    ap.add_argument("--baslangic", default=None, help="eğitim penceresi en erken başlangıcı (config veri_baslangic yerine)")
    ap.add_argument("--test-baslangic", default=None, help="test dönemi başlangıcı (config rl.test_baslangic yerine)")
    ap.add_argument("--anapara-tabani", choices=["reel", "nominal"], default=None, help="anapara cezası tabanı (config yerine)")
    ap.add_argument("--lr", type=float, default=None, help="öğrenme oranı (config yerine)")
    args = ap.parse_args()
    gpu = args.device == "cuda"
    hc = dict(c["gpu"] if gpu else c)                          # eğitim hiperparametreleri
    args.updates = args.updates or (hc["guncelleme"] if gpu else c["uzun_guncelleme"])
    if args.lr:
        hc["ogrenme_orani"] = args.lr
    tabani = args.anapara_tabani or c.get("anapara_tabani", "reel")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    SYN.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    logf = (out / "egitim_log.jsonl").open("w", encoding="utf-8")

    def log(row):
        row = {"zaman": now_iso(), "gecen_dk": round((time.time() - t0) / 60, 1), **row}
        logf.write(json.dumps(row, ensure_ascii=False) + "\n")
        logf.flush()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    ds = rc.build_dataset(None, args.baslangic, args.test_baslangic)
    A = rc.action_grid(ds["cats"])
    profiles, pstats = rc.enumerate_profiles()
    prof_blob = json.dumps(profiles, ensure_ascii=False)
    (SYN / "profil_parametreleri.json").unlink(missing_ok=True)
    (SYN / "profil_kombinasyonlari.json").write_text(json.dumps({
        "_aciklama": ("Brief'te paylaşılan 5 personanın alan değerleri ve açık kısıtlarının çelişkisiz kombinasyonları. Yeni değer "
                      "uydurulmaz; müşteri ya da persona değildir, ek müşteri verisi içermez. Yalnızca RL politikasının profil "
                      "parametrelerine göre genelleşmesi için eğitimde kullanılır. Öneri yalnızca verilen 5 persona için üretilir."),
        "istatistik": pstats, "profiller": profiles}, ensure_ascii=False), encoding="utf-8")
    personas = []
    for pid in ["P01", "P02", "P03", "P04", "P05"]:
        p = load_persona(pid)
        view, qs = interpret(p, {})
        if view is not None:
            personas.append(rc.profile_from_persona(p, view))
    all_p = profiles + personas
    masks = np.array([rc.profile_mask(p, A, ds["rd"], ds["cats"])[0] for p in all_p])
    sim = rc.Sim(ds, A, all_p, masks, nominal=tabani == "nominal")
    n_state = 3 if sim.nominal else 2
    rng = np.random.default_rng(c["profil_tohum"] + 1)
    order = rng.permutation(len(profiles))
    n_hold = int(round(len(profiles) * c["profil_holdout_orani"]))
    hold_ids = order[:n_hold]
    train_ids = order[n_hold:]
    persona_ids = np.arange(len(profiles), len(all_p))
    val_ids = train_ids[:min(len(train_ids), hc["degerlendirme_profil"])]
    hold_val_ids = hold_ids[:hc["degerlendirme_profil"]]
    log({"olay": "veri", "gosterge_seti": ds["gosterge_seti"], "bosluk": len(ds["bosluklar"]), "dislanan_pencere": ds["dislanan_pencere"],
         "havuz": {k: int(len(ds[k])) for k in ("train", "val", "test")}, "notlar": ds["notes"]})
    log({"olay": "basladi", "cihaz": args.device, "guncelleme": args.updates, "aksiyon": int(len(A)), "gosterge": len(ds["feature_names"]),
         "profil_egitim": int(len(train_ids)), "profil_holdout": int(len(hold_ids)), "donem": ds["period"]})

    best = {"skor": -np.inf, "guncelleme": 0}
    if gpu:
        import torch
        from app import rl_gpu
        torch.backends.cuda.matmul.allow_tf32 = True
        ts = rl_gpu.TorchSim(sim, "cuda")
        ts.set_feature_policy(clip=hc.get("ozellik_kirp"))
        d_in = ds["features"].shape[1] + sim.pvec.shape[1] + n_state + len(ds["cats"])
        torch.manual_seed(c["profil_tohum"] + 2)
        net = rl_gpu.Net(d_in, hc["gizli"], len(A), len(ds["cats"]) if hc.get("inanc_basligi") else 0).cuda()

        def evaluate(ids, pool, n, seed, policy="dqn"):
            return rl_gpu.evaluate(net, ts, sim, ids, pool, n, seed, policy)

        def save():
            np.savez(out / "model.npz", **net.numpy_state())

        def load_best():
            net.load_numpy_state(np.load(out / "model.npz"))

        log({"olay": "gpu", "cihaz_adi": torch.cuda.get_device_name(0), "torch": torch.__version__})
    else:
        model = rc.ProfileDQN(ds, A, c["profil_tohum"] + 2)

        def evaluate(ids, pool, n, seed, policy="dqn"):
            return rc.evaluate(model, sim, ids, pool, n, np.random.default_rng(seed), policy)

        def save():
            np.savez(out / "model.npz", **model.net.state_dict())

        def load_best():
            model.net.load_state_dict(np.load(out / "model.npz"))

    half = len(ds["val"]) // 2
    robust = c.get("secim_olcutu", "ortalama") == "en_kotu_donem" and half >= 5

    def on_eval(u):
        e = evaluate(val_ids, ds["val"], hc["degerlendirme_senaryo"], 7)
        h = evaluate(hold_val_ids, ds["val"], hc["degerlendirme_senaryo"], 8)
        score, parts = e["ortalama_odul"], None
        if robust:   # doğrulama döneminin iki yarısının en kötüsü: tek bir alt dönemi kovalayan noktayı seçmez
            parts = [evaluate(val_ids, ds["val"][:half], hc["degerlendirme_senaryo"], 9)["ortalama_odul"],
                     evaluate(val_ids, ds["val"][half:], hc["degerlendirme_senaryo"], 10)["ortalama_odul"]]
            score = min(parts)
        improved = score > best["skor"]
        if improved:
            best.update(skor=score, guncelleme=u)
            save()
        elapsed = time.time() - t0
        (out / "durum.json").write_text(json.dumps({
            "guncelleme": u, "toplam": args.updates, "gecen_dk": round(elapsed / 60, 1),
            "tahmini_kalan_dk": round(elapsed / u * (args.updates - u) / 60, 1), "en_iyi": best,
            "son_dogrulama_odul": round(e["ortalama_odul"], 4)}, ensure_ascii=False), encoding="utf-8")
        return {"dogrulama_odul": round(e["ortalama_odul"], 4), "dogrulama_reel_kazanc": round(e["ortalama_reel_kazanc"], 4),
                "dogrulama_anapara_alti": round(e["anapara_alti_bitis_olasiligi"], 4),
                "holdout_odul": round(h["ortalama_odul"], 4), "secim_skoru": round(score, 4),
                **({"dogrulama_yarilar": [round(x, 4) for x in parts]} if parts else {}), "en_iyi": improved}

    every = min(hc["degerlendirme_aralik"], max(1, args.updates // 2))
    if gpu:
        curve = rl_gpu.train(net, ts, train_ids, ds["train"], args.updates, hc, c["profil_tohum"] + 3,
                             on_eval=on_eval, eval_every=every, log=log)
    else:
        curve = rc.train(model, sim, train_ids, ds["train"], args.updates, np.random.default_rng(c["profil_tohum"] + 3),
                         on_eval=on_eval, eval_every=every, log=log)
    if (out / "model.npz").exists():
        load_best()

    belief = {}
    if gpu and net.n_cat:
        belief = {"dogrulama": rl_gpu.belief_report(net, ts, ds["val"], ds["train"], ds["cats"]),
                  "test": rl_gpu.belief_report(net, ts, ds["test"], ds["train"], ds["cats"])}
        log({"olay": "inanc", "dogrulama": {k: v["korelasyon"] for k, v in belief["dogrulama"].get("kategoriler", {}).items()}})

    # Son değerlendirme: test dönemi (eğitim ve seçimde hiç kullanılmadı)
    results = {"personalar": {}}
    if len(ds["test"]) == 0:
        results["not"] = "Test havuzu boş (test döneminde geçerli pencere yok); test değerlendirmesi yapılmadı."
        log({"olay": "uyari", "mesaj": results["not"]})
    else:
        for label, ids, n_per in (("egitim_profilleri", train_ids, 4), ("gorulmemis_profiller", hold_ids, 8)):
            results[label] = {POLICIES[k]: evaluate(ids, ds["test"], n_per, 11, k) for k in POLICIES}
        for i, p in zip(persona_ids, personas):
            results["personalar"][p["id"]] = {POLICIES[k]: evaluate(np.array([i]), ds["test"], 1000, 12, k) for k in POLICIES}
    meta = {
        "olusturma": now_iso(), "sure_dk": round((time.time() - t0) / 60, 1), "guncelleme": args.updates,
        "en_iyi_guncelleme": best["guncelleme"], "en_iyi_dogrulama_odul": best["skor"],
        "config_hash": config_hash(), "cihaz": args.device, "hiperparametreler": hc, "rl_kategori": c, "rl": decision_config()["rl"],
        "kategoriler": ds["cats"], "kategori_rd": ds["rd"], "kategori_uye_fon": ds["members"], "kategori_medyan_fon": ds["fund_counts"],
        "aksiyonlar": A.tolist(), "gosterge_adlari": ds["feature_names"], "gosterge_ort": ds["mu"].tolist(),
        "gosterge_std": ds["sd"].tolist(), "donem": ds["period"], "veri_notlari": ds["notes"],
        "profil_dosyasi_sha256": hashlib.sha256(prof_blob.encode("utf-8")).hexdigest()[:16],
        "profil_sayisi": {"egitim": int(len(train_ids)), "gorulmemis": int(len(hold_ids))}, "profil_istatistik": pstats,
        "mevcut_portfoy_egitimi": c.get("mevcut_portfoy_olasiligi", 0) > 0,
        "gosterge_seti": ds["gosterge_seti"], "veri_bosluklari": ds["bosluklar"], "dislanan_pencere": ds["dislanan_pencere"],
        "ozellik_kirp": hc.get("ozellik_kirp"),
        "inanc_basligi": bool(gpu and net.n_cat), "inanc_kalibrasyon": belief,
        "secim_olcutu": "en_kotu_donem" if robust else "ortalama", "degerlendirme_max_ceyrek": c.get("degerlendirme_max_ceyrek"),
        "kategori_max_agirlik": c.get("kategori_max_agirlik"),
        "veri_baslangic": args.baslangic or c.get("veri_baslangic"),
        "test_baslangic": args.test_baslangic or decision_config()["rl"]["test_baslangic"],
        "anapara_tabani": tabani, "ceza_tavan": c.get("ceza_tavan"), "durum_boyutu": n_state,
        "egri": curve, "test": results,
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    log({"olay": "bitti", "sure_dk": meta["sure_dk"], "en_iyi_guncelleme": best["guncelleme"],
         "test_personalar": {k: {pk: round(pv["ortalama_reel_kazanc"], 3) for pk, pv in v.items()} for k, v in results["personalar"].items()}})
    (out / "durum.json").write_text(json.dumps({"guncelleme": args.updates, "toplam": args.updates, "bitti": True,
                                                "sure_dk": meta["sure_dk"], "en_iyi": best}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
