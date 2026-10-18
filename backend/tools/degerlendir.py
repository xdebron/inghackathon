"""Değerlendirme takımı: brief'teki kuralları ve davranışları uçtan uca akış üzerinde ölçer, sonucu arayüze yazar.

Çalıştırma (backend klasöründe):  .venv/Scripts/python tools/degerlendir.py
Çıktı: data/degerlendirme/son.json  (arayüzde "Değerlendirme" sayfası)

Senaryolar
  1. Beş persona uçtan uca: kural ihlali (RD, açık kısıt, dışlanan ürün listede), RL dağılımında kapalı kategori, guard ihlali,
     zorunlu uyarı metni, kaynağı / zaman damgası eksik veri noktası, işlem süresi.
  2. Tekrarlanabilirlik: aynı persona iki kez → kısa liste ve RL dağılımı aynı mı?
  3. Veri kesintisi: her kaynak tek tek kapatılır → akış tamamlanıyor mu, kapalı kaynaktan 'ok' görünen (uydurulmuş) değer var mı?
  4. Eksik / çelişkili persona: öneri üretilmeden RM teyit sorusu soruluyor mu?
  5. Jüri tipi değişiklikler: risk profili düşürme, kısıt ekleme → liste yeni sınıra uyuyor mu?

Metin üretimi deterministik şablonla yapılır (LLM_PROVIDER=placeholder): ölçülen, kural ve veri davranışıdır; LLM ajanının
metni de aynı guard'dan geçer (bkz. docs/ajan.md). Kanıt kayıtları evidence/_degerlendirme klasörüne yazılır, arşive karışmaz.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ["LLM_PROVIDER"] = "placeholder"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, evidence, pipeline  # noqa: E402
from app.models import Persona, RunRequest  # noqa: E402

OUT = ROOT / "data" / "degerlendirme" / "son.json"
evidence.EVIDENCE_DIR = config.EVIDENCE_DIR / "_degerlendirme"
evidence.EVIDENCE_DIR.mkdir(exist_ok=True)

DISCLAIMER = "RM onayı olmadan müşteriyle paylaşılamaz"
OUTAGE_MARKERS = {"tcmb": "TCMB gösterge kurlar", "evds": "TCMB EVDS (", "tefas": "TEFAS"}


def persona(pid: str) -> Persona:
    return Persona(**json.loads((config.PERSONA_DIR / f"{pid.lower()}.json").read_text(encoding="utf-8")))


def after(ts: str | None, t0: str) -> bool:
    return bool(ts) and datetime.fromisoformat(ts) >= datetime.fromisoformat(t0)


def run(req: RunRequest) -> tuple[dict, float]:
    t0 = time.perf_counter()
    r = pipeline.run(req)
    return r, time.perf_counter() - t0


def datapoints(r: dict) -> list[dict]:
    out = list(r.get("market") or [])
    for s in r.get("shortlist") or []:
        out += list(s["product"]["fields"].values())
    return out


def checks(r: dict) -> dict:
    """Bir çalıştırmanın kural ve kanıt kontrolleri (0 = ihlal yok)."""
    pv = r.get("profile_view") or {}
    max_rd = pv.get("max_rd")
    short = r.get("shortlist") or []
    excl = {e["product_id"] for e in r.get("excluded") or []}
    rd_viol = [s["product"]["name"] for s in short
               if max_rd is not None and (s["product"]["fields"].get("rd") or {}).get("value") not in (None,)
               and s["product"]["fields"]["rd"]["value"] > max_rd]
    excl_listed = [s["product"]["name"] for s in short if s["product"]["product_id"] in excl]
    rlk = r.get("rl_kategori") or {}
    closed = {d.split(":", 1)[0] for d in rlk.get("dislanan_kategoriler") or []}
    rl_viol = [c for c in (rlk.get("kategori_dagilimi") or {}) if c in closed]
    rl_excl = [m["ad"] for m in rlk.get("urun_eslemesi") or [] if m.get("product_id") in excl]
    dps = [d for d in datapoints(r) if d.get("value") is not None]
    unsourced = [d["key"] for d in dps if not d.get("source")]
    no_time = [d["key"] for d in dps if not d.get("fetched_at") and not d.get("as_of")]
    no_fetch = [d["key"] for d in dps if not d.get("fetched_at")]
    text = r.get("draft_text") or ""
    return {
        "rd_ihlali": rd_viol, "dislanan_urun_listede": excl_listed, "rl_kapali_kategori": rl_viol + rl_excl,
        "guard_ihlali": (r.get("guard") or {}).get("violations") or [],
        "uyari_metni_var": DISCLAIMER in text,
        "kaynaksiz_veri": unsourced + no_time, "alinma_zamani_yok": no_fetch, "veri_noktasi": len(dps),
    }


def ok(c: dict) -> bool:
    return not (c["rd_ihlali"] or c["dislanan_urun_listede"] or c["rl_kapali_kategori"] or c["guard_ihlali"]
                or c["kaynaksiz_veri"]) and c["uyari_metni_var"]


def main() -> None:
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    result: dict = {"calisma_zamani": started, "senaryolar": []}
    add = result["senaryolar"].append

    # 1-2. Beş persona uçtan uca + tekrar
    for pid in ["P01", "P02", "P03", "P04", "P05"]:
        r1, t1 = run(RunRequest(persona_id=pid))
        r2, t2 = run(RunRequest(persona_id=pid))
        c = checks(r1)
        same_list = [s["product"]["product_id"] for s in r1.get("shortlist") or []] == \
                    [s["product"]["product_id"] for s in r2.get("shortlist") or []]
        same_rl = (r1.get("rl_kategori") or {}).get("kategori_dagilimi") == (r2.get("rl_kategori") or {}).get("kategori_dagilimi")
        u = (r1.get("rl_kategori") or {}).get("politika_belirsizligi") or {}
        add({"grup": "Uçtan uca persona", "ad": f"{pid} hazırlık", "gecti": ok(c) and same_list and same_rl and r1["status"] == "rm_onayi_bekliyor",
             "durum": r1["status"], "sure_sn": round((t1 + t2) / 2, 1), "kisa_liste": len(r1.get("shortlist") or []),
             "dislanan": len(r1.get("excluded") or []), "kontroller": c, "tekrar_ayni_liste": same_list, "tekrar_ayni_rl": same_rl,
             "rl_dagilim": (r1.get("rl_kategori") or {}).get("kategori_dagilimi"),
             "rl_secilen_olasilik": u.get("secilen_olasilik"), "run_id": r1["run_id"]})
        print(pid, "tamam", flush=True)

    # 3. Veri kesintisi (P05)
    for src in ["tcmb", "evds", "tefas", "manuel"]:
        t0 = datetime.now().astimezone().isoformat(timespec="seconds")
        r, t = run(RunRequest(persona_id="P05", disabled_sources=[src]))
        marker = OUTAGE_MARKERS.get(src)
        # Uydurma: kaynak kapalıyken o kaynaktan bu çalıştırmada "alınmış" gibi görünen değer. Önceden çekilmiş (önbellek /
        # geçmiş seri) değerler kendi alınma zamanıyla gösterildiği için uydurma sayılmaz.
        fabricated = [d["key"] for d in datapoints(r) if marker and marker in (d.get("source") or "")
                      and d.get("value") is not None and after(d.get("fetched_at"), t0)]
        missing = [d["key"] for d in datapoints(r) if d.get("status") in ("missing", "stale", "error", "conflict")]
        c = checks(r)
        add({"grup": "Veri kesintisi", "ad": f"{src} kapalı (P05)", "gecti": not fabricated and r["status"] in ("rm_onayi_bekliyor", "rm_teyidi_bekliyor") and ok(c),
             "durum": r["status"], "sure_sn": round(t, 1), "kisa_liste": len(r.get("shortlist") or []),
             "uydurulmus_deger": fabricated, "eksik_veya_bayat_isaretli": len(missing), "uyari_sayisi": len(r.get("flags") or []),
             "kontroller": c, "run_id": r["run_id"]})
        print(src, "kesinti tamam", flush=True)

    # 4. Eksik / çelişkili persona
    p = persona("P01").model_copy(update={"risk_profili": None})
    r, t = run(RunRequest(persona=p))
    add({"grup": "Eksik / çelişkili bilgi", "ad": "P01 risk profili silindi", "gecti": r["status"] == "rm_teyidi_bekliyor" and not r.get("shortlist"),
         "durum": r["status"], "sure_sn": round(t, 1), "sorular": [q["question"] for q in r.get("questions") or []], "run_id": r["run_id"]})
    p = persona("P01").model_copy(update={"risk_profili": "Agresif"})
    r, t = run(RunRequest(persona=p))
    add({"grup": "Eksik / çelişkili bilgi", "ad": "P01 risk Agresif (anapara koruma Yüksek ile çelişki)",
         "gecti": r["status"] == "rm_teyidi_bekliyor" and not r.get("shortlist"),
         "durum": r["status"], "sure_sn": round(t, 1), "sorular": [q["question"] for q in r.get("questions") or []], "run_id": r["run_id"]})

    # 5. Jüri tipi değişiklikler
    base, _ = run(RunRequest(persona_id="P03"))
    p = persona("P03").model_copy(update={"risk_profili": "Dengeli"})
    r, t = run(RunRequest(persona=p))
    c = checks(r)
    add({"grup": "Jüri tipi değişiklik", "ad": "P03 risk Agresif → Dengeli",
         "gecti": ok(c) and r["profile_view"]["max_rd"] < base["profile_view"]["max_rd"] and r["status"] in ("rm_onayi_bekliyor", "rm_teyidi_bekliyor"),
         "durum": r["status"], "sure_sn": round(t, 1), "rd_siniri": [base["profile_view"]["max_rd"], r["profile_view"]["max_rd"]],
         "rl_dagilim": [base.get("rl_kategori", {}).get("kategori_dagilimi"), r.get("rl_kategori", {}).get("kategori_dagilimi")],
         "kontroller": c, "run_id": r["run_id"]})
    p = persona("P04").model_copy(update={"acik_kisitlar": (persona("P04").acik_kisitlar or []) + ["yüksek riskli ürünlerden kaçınma"]})
    r, t = run(RunRequest(persona=p))
    c = checks(r)
    asked = [q["question"] for q in r.get("questions") or []]
    add({"grup": "Jüri tipi değişiklik", "ad": "P04 + 'yüksek riskli ürünlerden kaçınma' kısıtı",
         "gecti": (r["status"] == "rm_onayi_bekliyor" and ok(c)) or (r["status"] == "rm_teyidi_bekliyor" and bool(asked) and not r.get("shortlist")),
         "durum": r["status"], "sure_sn": round(t, 1), "sorular": asked,
         "uygulanan_kurallar": [x.get("id") for x in (r.get("profile_view") or {}).get("rules", [])],
         "rl_dagilim": r.get("rl_kategori", {}).get("kategori_dagilimi"), "kontroller": c, "run_id": r["run_id"]})

    S = result["senaryolar"]
    result["ozet"] = {
        "senaryo": len(S), "gecen": sum(s["gecti"] for s in S),
        "kural_ihlali": sum(len(s.get("kontroller", {}).get(k, [])) for s in S for k in ("rd_ihlali", "dislanan_urun_listede", "rl_kapali_kategori")),
        "guard_ihlali": sum(len(s.get("kontroller", {}).get("guard_ihlali", [])) for s in S),
        "kaynaksiz_veri": sum(len(s.get("kontroller", {}).get("kaynaksiz_veri", [])) for s in S),
        "alinma_zamani_yok": sum(len(s.get("kontroller", {}).get("alinma_zamani_yok", [])) for s in S),
        "uydurulmus_deger": sum(len(s.get("uydurulmus_deger", [])) for s in S),
        "veri_noktasi": sum(s.get("kontroller", {}).get("veri_noktasi", 0) for s in S),
        "ortalama_sure_sn": round(sum(s["sure_sn"] for s in S) / len(S), 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result["ozet"], ensure_ascii=False))


if __name__ == "__main__":
    main()
