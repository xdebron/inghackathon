"""Kategori RL modelinin canlı durumu ve etkileşimli tahmin uçları (salt okunur).

/api/rl/model      eğitim durumu (canlı), öğrenme eğrisi, test sonuçları
/api/rl/secenekler profil alanlarının brief değerleri ve seçilebilir piyasa tarihleri
/api/rl/tahmin     profil (veya persona) + piyasa tarihi → kategori dağılımı, alternatifler, maske, dağılım dışı uyarısı
"""
from __future__ import annotations

import glob
import json
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import rl_category as rc
from .config import PERSONA_DIR, config_hash, decision_config
from .models import Persona
from .profile import interpret
from .sources import tefas_import

router = APIRouter(prefix="/api/rl")
_DS_CACHE: dict = {}

META_KEYS = ("olusturma", "sure_dk", "cihaz", "guncelleme", "en_iyi_guncelleme", "en_iyi_dogrulama_odul", "donem",
             "profil_sayisi", "profil_istatistik", "kategoriler", "kategori_rd", "kategori_medyan_fon", "gosterge_adlari",
             "egri", "test", "hiperparametreler", "veri_notlari", "ozellik_kirp", "inanc_basligi", "inanc_kalibrasyon",
             "model_turu", "model_adi", "egitim_verisi", "hpo", "whatif", "kirpma_turu")


def _dataset(feature_set: str | None = None) -> dict:
    if feature_set is None:
        _, meta = rc._load_model()
        feature_set = (meta or {}).get("gosterge_seti", "v1")   # çıkarım modelin eğitildiği setle yapılır
    key = (tefas_import.data_version(), config_hash(), feature_set)
    if key not in _DS_CACHE:
        _DS_CACHE.clear()
        _DS_CACHE[key] = rc.build_dataset(feature_set)
    return _DS_CACHE[key]


def _live_log(n: int = 400) -> list[dict]:
    path = rc.MODEL_DIR / "egitim_log.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[-n:]


@router.get("/model")
def model_info():
    from . import rl_explain as rx
    _, meta = rc._load_model()
    expl = rc.load_explainer(meta) if meta else None
    return {"durum": rc.model_status(), "meta": {k: meta.get(k) for k in META_KEYS} if meta else None,
            "aciklama": rx.global_summary(expl) if expl else None, "canli_log": _live_log()}


def _persona_values() -> dict:
    P = [json.loads(open(f, encoding="utf-8").read()) for f in sorted(glob.glob(str(PERSONA_DIR / "*.json")))]
    fields = ("risk_profili", "yatirim_ufku", "likidite_ihtiyaci", "anapara_koruma_tercihi", "doviz_maruz_kalma_tercihi")
    from .config import constraint_rules
    brief = sorted({k for p in P for k in p["acik_kisitlar"]})
    catalog = [r for r in constraint_rules()["kurallar"] if r.get("ornek") and r["ornek"] not in brief]
    return {"alanlar": {k: sorted({p[k] for p in P}) for k in fields},
            "kisitlar": brief + [r["ornek"] for r in catalog],
            "kisit_bilgisi": {r["ornek"]: {"id": r["id"], "tip": r["tip"], "aciklama": r["aciklama"],
                                           "kaynak": "brief" if r["ornek"] in brief else "katalog"}
                              for r in constraint_rules()["kurallar"] if r.get("ornek")},
            "personalar": {p["persona_id"]: {k: p[k] for k in fields + ("acik_kisitlar", "profil")} for p in P}}


@router.get("/secenekler")
def options():
    ds = _dataset()
    ok = np.where(~np.isnan(ds["features_raw"]).any(axis=1))[0]
    dates = [str(ds["dates"][i].date()) for i in ok]
    split = {}
    for name in ("train", "val", "test"):
        idx = ds[name]
        split[name] = [str(ds["dates"][idx[0]].date()), str(ds["dates"][idx[-1]].date())] if len(idx) else None
    return {**_persona_values(), "tarihler": dates, "varsayilan_tarih": dates[-1] if dates else None, "donemler": split,
            "veri_son_tarih": str(ds["dates"][-1].date())}


class PredictRequest(BaseModel):
    persona_id: str | None = None
    profil: dict[str, Any] | None = None          # risk_profili, yatirim_ufku, likidite_ihtiyaci, anapara_koruma_tercihi, doviz_maruz_kalma_tercihi, acik_kisitlar
    tarih: str | None = None
    alternatif: int = Field(5, ge=1, le=20)
    mevcut_dagilim: dict[str, float] | None = None


def _profile_from_request(req: PredictRequest) -> tuple[dict, Persona]:
    vals = _persona_values()
    if req.persona_id:
        src = vals["personalar"].get(req.persona_id)
        if not src:
            raise HTTPException(404, f"Persona yok: {req.persona_id}")
        fields = dict(src)
    elif req.profil:
        fields = dict(req.profil)
    else:
        raise HTTPException(400, "persona_id veya profil gerekli")
    persona = Persona(persona_id=req.persona_id or "SECIM", risk_profili=fields.get("risk_profili"),
                      yatirim_ufku=fields.get("yatirim_ufku"), likidite_ihtiyaci=fields.get("likidite_ihtiyaci"),
                      gelir_nakit_akisi_ihtiyaci="Orta", anapara_koruma_tercihi=fields.get("anapara_koruma_tercihi"),
                      doviz_maruz_kalma_tercihi=fields.get("doviz_maruz_kalma_tercihi"), oncelikler=["-"], yatirim_amaci="-",
                      acik_kisitlar=list(fields.get("acik_kisitlar") or []))
    view, questions = interpret(persona, {})
    if view is None:
        raise HTTPException(422, {"message": "Profil çelişkili veya eksik; sistem bu profil için karar vermez.",
                                  "sorular": [q.model_dump() for q in questions]})
    return rc.profile_from_persona(persona, view), persona


@router.post("/tahmin")
def predict(req: PredictRequest):
    net, meta = rc._load_model()
    if net is None:
        raise HTTPException(409, "Eğitilmiş model yok.")
    ds = _dataset()
    if ds["feature_names"] != meta["gosterge_adlari"]:
        raise HTTPException(409, "Modelin gösterge listesi güncel veriyle uyuşmuyor; model yeniden eğitilmeli.")
    prof, persona = _profile_from_request(req)
    dates = pd.DatetimeIndex(ds["dates"])
    valid = np.where(~np.isnan(ds["features_raw"]).any(axis=1))[0]
    if not len(valid):
        raise HTTPException(409, "Göstergeleri hesaplanabilen tarih yok.")
    target = int(dates.searchsorted(pd.Timestamp(req.tarih), side="right")) - 1 if req.tarih else len(dates) - 1
    cand = valid[valid <= target]
    if not len(cand):
        raise HTTPException(400, "Seçilen tarihten önce göstergeleri hesaplanabilen bir tarih yok.")
    pos = int(cand[-1])                                   # istenen tarihe en yakın (önceki) geçerli tarih
    raw = ds["features_raw"][pos]
    mu, sd, clip = np.array(meta["gosterge_ort"]), np.array(meta["gosterge_std"]), meta.get("ozellik_kirp")
    z = (raw - mu) / sd
    zin = rc.model_input(z, meta)
    A = np.array(meta["aksiyonlar"])
    cats = meta.get("kategoriler") or rc.CATS_V1               # modelin kendi kategori listesi
    mask, why = rc.profile_mask(prof, A, meta["kategori_rd"], cats)
    if not mask.any():
        raise HTTPException(422, {"message": "Açık kısıtlar birlikte sağlanamıyor: modelin aksiyon ızgarasında izin verilen dağılım yok. "
                                             "Kısıtlardan birini gevşetin (örn. oranı değiştirin).", "kapali_kategoriler": why,
                                  "sorular": []})
    try:
        prev, cur_notes = rc.current_vector(req.mevcut_dagilim, cats)
    except ValueError as e:
        raise HTTPException(400, str(e))
    x = np.concatenate([zin, rc.profile_vector(prof, meta.get("profil_surumu", 1)), np.zeros(rc.state_dims(meta)), prev])[None, :]
    # PPO: değer = maskeli politika olasılığı; DQN: Q tahmini (ödül ölçeğinde)
    q = net.probs(x, mask[None, :])[0] if getattr(net, "kind", "dqn") == "ppo" else net.forward(x)[0] / rc.ProfileDQN.SCALE
    beliefs = rc.belief_table(net, x, cats, mask, A)
    qm = np.where(mask, q, -np.inf)
    order = np.argsort(-qm)[: min(req.alternatif, int(mask.sum()))]
    gate = rc.apply_gate(meta, q * rc.ProfileDQN.SCALE, int(order[0]), A, mask, cats)         if len(order) and getattr(net, "kind", "dqn") != "ppo" else None
    devir = lambda a: round(float(0.5 * np.abs(A[a] - prev).sum() + 0.5 * (1 - prev.sum())), 3)
    alts = [{"sira": i + 1, "deger": round(float(q[a]), 4), "devir": devir(a),
             "dagilim": {cats[j]: float(A[a][j]) for j in range(len(cats)) if A[a][j] > 0}} for i, a in enumerate(order)]
    sigma = decision_config()["rl"]["ood_esik_sigma"]
    ood = [{"ad": meta["gosterge_adlari"][k], "z": round(float(z[k]), 1)} for k in range(len(z)) if abs(z[k]) > sigma]
    period = "test" if pos in set(ds["test"]) else "dogrulama" if pos in set(ds["val"]) else "egitim" if pos in set(ds["train"]) else "diger"
    return {
        "tarih": str(dates[pos].date()), "donem": period, "profil": prof,
        "kategori_dagilimi": (gate and {cats[j]: float(A[gate["aksiyon"]][j]) for j in range(len(cats)) if A[gate["aksiyon"]][j] > 0})
                             or (alts[0]["dagilim"] if alts else {}),
        "guvenli_iyilestirme": gate and {k: v for k, v in gate.items() if k != "aksiyon"},
        "alternatifler": alts,
        "beklenen_getiri": beliefs,
        "aciklama": rc.explain_decision(meta, raw, prof, alts[0]["dagilim"], prev) if alts else None,
        "kategoriler": cats, "izinli_aksiyon": int(mask.sum()), "toplam_aksiyon": int(len(A)), "dislanan_kategoriler": why,
        "mevcut_dagilim": {cats[j]: float(prev[j]) for j in range(len(cats)) if prev[j] > 0},
        "notlar": cur_notes + (["Bu model eğitimde başlangıçta hep nakit gördü; mevcut dağılıma tepkisi yeniden eğitimden sonra anlamlı olur."]
                               if req.mevcut_dagilim and not meta.get("mevcut_portfoy_egitimi") else []),
        "dagilim_disi": ood,
        "gostergeler": [{"ad": n, "deger": float(raw[k]), "z": round(float(z[k]), 2), "kirpildi": bool(clip and abs(z[k]) > clip)}
                        for k, n in enumerate(meta["gosterge_adlari"])],
        "uyari": (f"Fiyat verisinde {len(ds['bosluklar'])} boşluk var; boşluğa yakın tarihlerin göstergeleri eksik veriyle hesaplanmış olabilir."
                  if ds.get("bosluklar") else None),
    }
