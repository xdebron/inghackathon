"""Uçtan uca hazırlık akışı. Her adım izlenir (trace) ve sonuç kanıt kaydına yazılır.

Adımlar ve sorumlular:
  1. persona_yorumlama   deterministik  eksik/çelişkili/tanınmayan alan -> RM teyidi (akış durur)
  2. veri_toplama        deterministik  kaynak + zaman damgalı veri noktaları
  3. uygunluk_kurallari  deterministik  dışlamalar + gerekçeler
  4. puanlama_secim      deterministik  açıklanabilir skor, kısa liste
  5. metin_taslagi       llm            yalnızca anlatım; liste/sayılar 3-4'ten gelir
  6. metin_guard         deterministik  yasaklı ifade, dışlanan ürün, kaynaksız oran kontrolü
  7. rm_onayi            rm             onay / düzenleyerek onay / red (ayrı endpoint)
"""
from __future__ import annotations

import json
import time
import uuid
from collections import OrderedDict
from datetime import datetime

from . import evidence, guard, rl, rl_category, rules, scoring, universe
from .config import PERSONA_DIR, config_hash, decision_config, now_iso
from .llm import PlaceholderLLM, fmt_pct, get_client
from .models import Flag, Persona, RunRequest, TraceStep
from .profile import apply_answers, interpret

CATEGORY_ROLES = {
    "mevduat_tl": "Sözleşmeli faiz geliri; anapara nominal olarak korunur, vade boyunca likidite sınırlı.",
    "katilim_hesabi": "Faizsiz kâr payı geliri; oran önceden belirlenmez, vade boyunca likidite sınırlı.",
    "para_piyasasi": "Nakit yönetimi ve likidite; mevduata alternatif kısa vadeli getiri.",
    "borclanma": "Faiz geliri odaklı; faiz oranı değişimlerine duyarlı, orta oynaklık.",
    "katilim": "Faizsiz finans ilkelerine uygun, gelir odaklı alternatif.",
    "eurobond_doviz": "Döviz bazlı gelir ve kur çeşitlendirmesi.",
    "kiymetli_maden": "Kur ve enflasyon riskine karşı çeşitlendirme; düzenli gelir üretmez.",
    "karma_degisken": "Varlık sınıfları arasında dağılım; orta-yüksek oynaklık.",
    "fon_sepeti": "Birden fazla fon üzerinden hazır çeşitlendirme.",
    "hisse": "Uzun vadeli büyüme potansiyeli; yüksek oynaklık.",
    "serbest": "Esnek strateji; yapısı karmaşık, likiditesi sınırlı olabilir.",
    "dibs_tl": "Devlet iç borçlanma senedi; vade sonuna kadar tutulursa nominal getiri bilinir, ara satışta faiz riskine açık.",
    "dibs_tufe": "TÜFE'ye endeksli devlet tahvili; enflasyonun üzerinde reel getiri, vade sonunda enflasyona karşı korunma.",
    "mevduat_doviz": "Döviz cinsi mevduat; kur maruziyeti yaratır, düşük faiz.",
    "maden_hesabi": "Banka kıymetli maden hesabı; maden fiyatını doğrudan izler, faiz/gelir üretmez, alış-satış makası vardır.",
    "yabanci_hisse": "Yurt dışı hisse senetleri; döviz ve hisse riski birlikte.",
    "bist30_hisse": "Doğrudan BIST 30 hissesi; tek şirket riski, yüksek oynaklık.",
}


def load_persona(persona_id: str) -> Persona:
    path = PERSONA_DIR / f"{persona_id.lower()}.json"
    if not path.exists():
        raise KeyError(persona_id)
    return Persona(**json.loads(path.read_text(encoding="utf-8")))


def list_personas() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(PERSONA_DIR.glob("*.json"))]


class Tracer:
    def __init__(self):
        self.steps: list[TraceStep] = []

    def run(self, step: str, actor: str, fn, summarize):
        started, t0 = now_iso(), time.perf_counter()
        result = fn()
        self.steps.append(TraceStep(step=step, actor=actor, started_at=started,
                                    duration_ms=round((time.perf_counter() - t0) * 1000, 1), summary=summarize(result)))
        return result


def _client_questions(persona: Persona, view) -> list[str]:
    ids = {r["id"] for r in view.rules}
    qs = ["Görüşülen tutarın müşterinin toplam finansal varlıkları içindeki payı nedir?"]
    if view.levels.get("likidite_ihtiyaci", 0) >= 0.5 and "K_KISA_VADE_NAKIT_YOK" not in ids:
        qs.append("Önümüzdeki 12 ay içinde planlanan büyük bir nakit çıkışı var mı?")
    if view.levels.get("gelir_nakit_akisi_ihtiyaci", 0) >= 1.0:
        qs.append("Düzenli gelire hangi sıklıkta (aylık / üç aylık) ihtiyaç duyuluyor?")
    if "K_DOVIZ_ARTIRMAMA" in ids:
        qs.append("Şirketin döviz gelirinin ne kadarı TL'ye çevriliyor; kişisel portföyde hedeflenen döviz payı nedir?")
    if "K_YOGUNLASMA" in ids:
        qs.append("Aile varlıklarının diğer varlık sınıflarındaki (gayrimenkul, şirket payı vb.) dağılımı nedir?")
    if view.horizon_years >= 5:
        qs.append("Uzun vadede planlanan önemli bir hedef veya harcama var mı?")
    return qs


MARKET_LABELS = {
    "tcmb.politika_faizi": ("TCMB politika faizi", "pct"),
    "tufe_yillik": ("Yıllık TÜFE", "pct"),
    "mevduat_sektor_ortalamasi_1a": ("Sektör TL mevduat faizi (1 aya kadar)", "pct"),
    "mevduat_sektor_ortalamasi_3a": ("Sektör TL mevduat faizi (3 aya kadar)", "pct"),
    "dibs_gosterge_2y": ("2 yıllık DİBS gösterge faizi", "pct"),
    "gecmis.tlref": ("TLREF gecelik referans faiz", "pct"),
    "gecmis.tcmb_aofm": ("TCMB ağırlıklı ortalama fonlama maliyeti", "pct"),
    "gecmis.dibs_tl_sabit_2y": ("2 yıllık sabit faizli DİBS (son ihale)", "pct"),
    "gecmis.dibs_tl_sabit_10y": ("10 yıllık sabit faizli DİBS (son ihale)", "pct"),
    "gecmis.dibs_tufe_reel_5y": ("5 yıllık TÜFE'ye endeksli DİBS reel getiri (son ihale)", "pct"),
    "kur.USDTRY": ("USD/TRY", "num"),
    "kur.EURTRY": ("EUR/TRY", "num"),
    "gecmis.hedge_prim_usd_1a": ("USD/TRY yıllık vadeli kur primi (faiz paritesi tahmini)", "pct"),
    "altin_gram_tl": ("Gram altın (TL)", "num2"),
    "gecmis.gumus_gram_tl": ("Gram gümüş (TL)", "num2"),
    "gecmis.eurobond_hazine_fiyat": ("Hazine eurobond stoku ortalama fiyatı", "num2"),
}


def _market_lines(market, pct) -> list[str]:
    by_key = {m.key: m for m in market}
    lines = []
    for key, (label, kind) in MARKET_LABELS.items():
        dp = by_key.get(key)
        if dp is None or dp.value is None:
            continue
        digits = 2 if kind == "num2" else 4
        val = pct(dp.value) if kind == "pct" else f"{dp.value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        lines.append(f"{label}: {val} ({dp.source}, {dp.as_of or 'tarih yok'}{', BAYAT' if dp.status == 'stale' else ''})")
    return lines


def _text_context(persona, view, scored, excluded, flags, allowed: set[str], market=()) -> dict:
    cats = decision_config()["kategoriler"]

    def pct(x):
        s = fmt_pct(x)
        if x is not None:
            allowed.add(s)
        return s

    shortlist = []
    for sp in [s for s in scored if s.selected]:
        p = sp.product
        top = sorted(sp.breakdown, key=lambda c: -c.contribution)[:2]
        caveats = [f.message for f in flags if f.product_id == p.product_id]
        caveats += [f"{k}: {dp.note}" for k, dp in p.fields.items() if dp.status in ("stale", "conflict")]
        is_fund = p.product_id.startswith("FON:")
        if is_fund and p.fields.get("rd") and p.fields["rd"].status == "assumption":
            caveats.append("risk değeri tahmini, resmi RD teyit edilmeli")
        if p.product_id.startswith("HISSE:"):
            caveats.append("doğrudan hisse: tek şirket riski taşır; fiyatlar resmi olmayan kaynaktan (Yahoo), risk değeri tahmini")
        if is_fund and p.v("yonetim_ucreti") is None:
            caveats.append("yönetim ücreti verisi yok")
        if p.category in ("mevduat_tl", "katilim_hesabi"):
            lim = p.v("tmsf_limiti")
            caveats.append(f"TMSF güvencesi kişi başı {lim:,.0f} TL'ye kadar; üzeri bankanın kredi riskine tabidir".replace(",", ".")
                           if lim is not None else "TMSF sigorta limiti yüklenmedi; güvence kapsamı teyit edilmeli")
        caveats = [c.rstrip(". ") for c in caveats]
        for c in caveats:  # uyarılardaki oranlar veri kalitesi kontrollerinden gelir (kaynaklı); guard biçiminde izinli say
            allowed.update(m.replace(" ", "").replace(".", ",") for m in guard.PCT.findall(c))
        liq_c = next(c for c in sp.breakdown if c.criterion == "likidite")
        extras = []
        if p.v("reel_getiri_1y") is not None:
            extras.append(f"enflasyona göre reel getiri {pct(p.v('reel_getiri_1y'))}")
        if p.v("benchmark_farki_1y") is not None:
            bench = p.fields["benchmark_farki_1y"].source.split("−")[-1].replace("1 yıllık getiri", "").strip()
            diff = p.v("benchmark_farki_1y")
            extras.append(f"{bench} karşısında {'+' if diff >= 0 else '-'}{pct(abs(diff))[1:]} puan")
            allowed.add(pct(abs(diff)))
        shortlist.append({
            "name": p.name, "category_name": cats.get(p.category, {}).get("ad", p.category),
            "role": CATEGORY_ROLES.get(p.category, "-"),
            "top_reasons": ", ".join(f"{scoring.LABELS[c.criterion].lower()} ({c.explanation})" for c in top),
            "ret": pct(p.v("getiri_1y")), "vol": pct(p.v("volatilite")),
            "rd": p.v("rd") if p.v("rd") is not None else "veri yok",
            "liq": liq_c.explanation, "caveats": "; ".join(dict.fromkeys(caveats)),
            "extras": "; ".join(extras),
        })
    summary: OrderedDict = OrderedDict()
    for e in excluded:
        if e.rule_id not in summary:
            summary[e.rule_id] = {"count": 0, "example": e.reason}
        summary[e.rule_id]["count"] += 1
    confirmations = [f.message for f in flags if f.level == "teyit" and (f.product_id is None or any(
        s.product.product_id == f.product_id and s.selected for s in scored))]
    confirmations += [f"RM tarafından onaylanan çelişki: {a}" for a in view.acknowledged]
    confirmations += [f"RM kararıyla uygulanmayan kısıt: '{c}'" for c in view.ignored_constraints]
    return {"persona": persona.model_dump(), "shortlist": shortlist, "exclusion_summary": summary,
            "market_lines": _market_lines(market, pct),
            "confirmations": list(dict.fromkeys(confirmations)), "client_questions": _client_questions(persona, view)}


def _rl_lines(r: dict, allowed: set[str]) -> list[str]:
    """RL sonucunu taslak metin satırlarına çevirir; kullanılan oranlar guard'ın izinli sayı kümesine eklenir."""
    if r.get("durum") != "tamam":
        return [f"RL dağılım simülasyonu çalışmadı: {r.get('neden')}"] if r.get("durum") else []

    def pct(x):
        s = fmt_pct(x)
        allowed.add(s)
        return s

    names = r["urun_adlari"]
    alloc = "; ".join(f"{names[k]} {pct(v)}" for k, v in r["baslangic_dagilimi"].items())
    t, d = r["test"], r["donem"]["test"]
    rl_t, eq = t["RL politikası (dinamik)"], t["Eşit ağırlık (sabit)"]
    lines = [
        f"Başlangıç dağılımı ({r['baslangic_tarihi']} piyasa koşullarıyla; her 3 ayda bir yeniden değerlendirilir): {alloc}.",
        f"Ajan seçimi: doğrulama döneminde '{r['secilen_ajan']}' seçildi; başlangıç dağılımı '{r['baslangic_ajani']}' ajanından.",
        f"Test dönemi simülasyonu ({d[0]} – {d[1]}, eğitimde kullanılmadı): ortalama reel kazanç {pct(rl_t['ortalama_reel_kazanc'])} "
        f"(eşit ağırlık {pct(eq['ortalama_reel_kazanc'])}); dönem sonunda reel olarak anaparanın altında kalma olasılığı "
        f"{pct(rl_t['anapara_alti_bitis_olasiligi'])} (eşit ağırlık {pct(eq['anapara_alti_bitis_olasiligi'])}).",
    ]
    below = [p for p in r["politika"] if p["adim"] == 1 and p["servet_bandi"] in ("< %95", "%95-100") and p["guvenilir"]]
    above = [p for p in r["politika"] if p["adim"] == 1 and p["servet_bandi"] in ("%100-105", "%105-115") and p["guvenilir"]]
    fmt = lambda p: ", ".join(f"{names[k]} {pct(v)}" for k, v in p["agirliklar"].items())
    if below:
        lines.append(f"İlk 3 ayın sonunda reel servet anaparanın altındaysa politika: {fmt(below[0])}.")
    if above:
        lines.append(f"İlk 3 ayın sonunda reel servet anaparanın üstündeyse politika: {fmt(above[0])}.")
    if r["dislanan"]:
        lines.append("Simülasyona alınamayan ürünler: " + "; ".join(f"{names.get(d['product_id'], d['product_id'])} ({d['neden']})" for d in r["dislanan"]))
    for n in r["notlar"]:
        allowed.update(guard.PCT.findall(n))  # notlardaki oranlar RL değerlendirmesinden hesaplanır
        lines.append(f"Not: {n}")
    lines.append("Bu bölüm geçmiş verilerle yapılan bir simülasyondur; getiri tahmini değildir.")
    return lines


def _policy_uncertainty(original, req) -> dict | None:
    """NN-politika için seçilen dağılımın olasılığı ve olasılık ağırlıklı beklenen dağılım (aynı canlı model, aynı durum).
    Politika düzse tek dağılım (argmax) gürültüye duyarlıdır; RM'e yön olarak beklenen dağılım da gösterilir."""
    from . import rl_tools
    try:
        k = rl_tools.rl_karar({"persona": original.model_dump(), "rm_answers": req.rm_answers or {},
                               "mevcut_dagilim": req.mevcut_dagilim})
    except Exception:
        return None
    if k.get("secilen_olasilik") is None:
        return None
    return {"secilen_olasilik": k["secilen_olasilik"], "beklenen_dagilim": k["beklenen_dagilim"],
            "model_tercihi": k["model_tercihi"], "model": k["model"]}


def _rlk_lines(r: dict, allowed: set[str]) -> list[str]:
    """Profile koşullu kategori modelinin sonucunu taslak metin satırlarına çevirir."""
    def pct(x):
        s = fmt_pct(x)
        allowed.add(s)
        return s

    alloc = "; ".join(f"{m['ad'] or m['kategori'] + ' (uygun ürün yok)'} {pct(m['agirlik'])}" for m in r["urun_eslemesi"])
    lines = [f"Başlangıç dağılımı ({r['tarih']} piyasa koşullarıyla, kategori modeli; her 3 ayda bir yeniden değerlendirilir): {alloc}."]
    u = r.get("politika_belirsizligi")
    if u:
        from .rl_tools import CAT_NAMES
        exp = ", ".join(f"{CAT_NAMES.get(k, k)} {pct(v / 100)}" for k, v in sorted(u["beklenen_dagilim"].items(), key=lambda x: -x[1]) if v >= 5)
        if u["secilen_olasilik"] < 20:
            lines.append(f"Belirsizlik: model bu dağılımı yalnızca {pct(u['secilen_olasilik'] / 100)} olasılıkla seçiyor (politika birçok "
                         f"dağılım arasında kararsız). Olasılık ağırlıklı dağılım: {exp}. Tek dağılımı kesin öneri olarak değil, yön "
                         "göstergesi olarak değerlendirin.")
        else:
            lines.append(f"Modelin bu dağılımı seçme olasılığı {pct(u['secilen_olasilik'] / 100)}; olasılık ağırlıklı dağılım: {exp}.")
    g = r.get("guvenli_iyilestirme")
    if g and g.get("kaynak") == "taban_kural":
        mt = ", ".join(f"{k} {pct(v)}" for k, v in g["model_tercihi"].items())
        lines.append(f"Güvenli iyileştirme: modelin kendi tercihi ({mt}) taban kuraldan güvenilir ölçüde iyi bulunmadığı için "
                     "taban kural uygulandı.")
    if r.get("mevcut_dagilim"):
        cur = ", ".join(f"{k} {pct(v)}" for k, v in r["mevcut_dagilim"].items())
        lines.append(f"RM'in girdiği mevcut dağılım: {cur}; önerilen dağılıma geçiş portföyün {pct(r['devir'])} kadarının el değiştirmesi demek.")
    t = r.get("persona_testi")
    if t:
        dqn = t.get("DQN (profile koşullu)") or t.get("Model (PPO, profile koşullu)")
        pp = t.get("Para piyasası + mevduat/borçlanma")
        if dqn and pp and "Model (PPO, profile koşullu)" in t:
            lines.append(f"Performans tahmini (aynı ayarlarla eğitilmiş, {r['model']['donem']['test'][0]} sonrasını görmemiş modeller): "
                         f"bu persona için ortalama reel kazanç {pct(dqn['ortalama_reel_kazanc'])}, "
                         f"para piyasası + mevduat/borçlanma kuralı {pct(pp['ortalama_reel_kazanc'])}. Canlı model aynı ayarla tüm veride eğitildi.")
        elif dqn and pp:
            lines.append(f"Test dönemi simülasyonu ({r['model']['donem']['test'][0]} – {r['model']['donem']['test'][1]}, eğitimde kullanılmadı): "
                         f"ortalama reel kazanç {pct(dqn['ortalama_reel_kazanc'])} (para piyasası + mevduat/borçlanma {pct(pp['ortalama_reel_kazanc'])}); "
                         f"dönem sonunda reel olarak anaparanın altında kalma olasılığı {pct(dqn['anapara_alti_bitis_olasiligi'])}.")
    if r["dislanan_kategoriler"]:
        lines.append("Modelin kullanmadığı kategoriler: " + "; ".join(r["dislanan_kategoriler"]) + ".")
    for n in r["notlar"]:
        allowed.update(guard.PCT.findall(n))
        lines.append(f"Not: {n}")
    lines.append("Model, sentetik profil parametreleriyle eğitilmiş profile koşullu bir politikadır; bu bölüm geçmiş verilerle "
                 "yapılan bir simülasyondur, getiri tahmini değildir.")
    return lines


def run(req: RunRequest) -> dict:
    tr = Tracer()
    original = req.persona or load_persona(req.persona_id or "")
    persona = apply_answers(original, req.rm_answers)
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{persona.persona_id or 'X'}-{uuid.uuid4().hex[:6]}"
    record = {
        "run_id": run_id, "created_at": now_iso(), "config_hash": config_hash(),
        "persona": persona.model_dump(), "persona_original": original.model_dump(),
        "rm_answers": req.rm_answers, "disabled_sources": req.disabled_sources,
    }

    view, questions = tr.run("persona_yorumlama", "deterministik", lambda: interpret(persona, req.rm_answers),
                             lambda r: f"{len(r[1])} teyit sorusu" if r[1] else "persona yorumlandı, kısıtlar kurallara bağlandı")
    if questions:
        record.update(status="rm_teyidi_bekliyor", questions=[q.model_dump() for q in questions],
                      trace=[s.model_dump() for s in tr.steps])
        evidence.save(record)
        return record

    products, market, data_flags = tr.run("veri_toplama", "deterministik", lambda: universe.build(req.disabled_sources),
                                          lambda r: f"{len(r[0])} ürün, {len(r[1])} piyasa verisi, {len(r[2])} veri uyarısı")
    eligible, excluded, rule_flags = tr.run("uygunluk_kurallari", "deterministik", lambda: rules.apply(products, view),
                                            lambda r: f"{len(r[0])} uygun, {len(r[1])} dışlandı")
    flags: list[Flag] = data_flags + rule_flags

    def score_and_select():
        scored, W = scoring.score(eligible, persona, view)
        return scored, W, scoring.select(scored, view)

    scored, W, sel_warnings = tr.run("puanlama_secim", "deterministik", score_and_select,
                                     lambda r: f"{sum(s.selected for s in r[0])} ürün kısa listede")
    flags += [Flag(level="uyari", message=w) for w in sel_warnings]

    def run_rl():
        try:
            return rl.run([s for s in scored if s.selected], persona, view)
        except Exception as exc:  # RL katmanı karar desteğinin ek parçası; hatası ana akışı durdurmaz
            return {"durum": "hata", "neden": f"{type(exc).__name__}: {exc}"}

    def rl_summary(r):
        if r.get("durum") != "tamam":
            return f"çalışmadı: {r.get('neden')}"
        t = r["test"]
        return (f"başlangıç {', '.join(f'{k.split(':', 1)[1]} %{v*100:.0f}' for k, v in r['baslangic_dagilimi'].items())}; "
                f"test ort. reel kazanç RL {t['RL politikası (dinamik)']['ortalama_reel_kazanc']:+.1%}, "
                f"eşit ağırlık {t['Eşit ağırlık (sabit)']['ortalama_reel_kazanc']:+.1%}")

    rl_result = tr.run("rl_dagilim", "rl", run_rl, rl_summary)

    def run_rlk():
        try:
            r = rl_category.infer(persona, view, scored, req.mevcut_dagilim)
        except Exception as exc:
            return {"durum": "hata", "neden": f"{type(exc).__name__}: {exc}"}
        if r.get("durum") == "tamam":
            r["politika_belirsizligi"] = _policy_uncertainty(original, req)
        return r

    rlk_result = tr.run("rl_kategori_modeli", "rl", run_rlk,
                        lambda r: (", ".join(f"{k} %{v*100:.0f}" for k, v in r["kategori_dagilimi"].items())
                                   if r.get("durum") == "tamam" else f"çalışmadı: {r.get('neden')}"))

    allowed: set[str] = set()
    ctx = _text_context(persona, view, scored, excluded, flags, allowed, market)
    ctx["rl_lines"] = _rlk_lines(rlk_result, allowed) if rlk_result.get("durum") == "tamam" else _rl_lines(rl_result, allowed)
    excluded_codes = [e.product_id.split(":", 1)[1] for e in excluded if e.product_id.startswith("FON:")]
    selected = [s for s in scored if s.selected]
    names = [s.product.name for s in selected]
    # RL eşlemesindeki ürün adları da metinde meşru özel addır (örn. '... Garanti Bankası' hissesi / 'GARANTİ PORTFÖY' fonu)
    names += [m["ad"] for m in rlk_result.get("urun_eslemesi", []) if m.get("ad") and m["ad"] not in names]
    sel_ids_early = {s.product.product_id for s in selected}
    # Ajan (LLM + araçlar) çalıştırmanın kanıt kaydı biçimindeki görünümünü okur
    ctx["_run"] = {
        "persona": persona.model_dump(), "rm_answers": req.rm_answers, "profile_view": view.model_dump(), "weights": W,
        "market": [m.model_dump() for m in market],
        "flags": [f.model_dump() for f in flags if f.product_id is None or f.product_id in sel_ids_early],
        "hidden_flag_count": sum(1 for f in flags if f.product_id is not None and f.product_id not in sel_ids_early),
        "shortlist": [s.model_dump() for s in selected],
        "others": [{"product_id": s.product.product_id, "name": s.product.name, "category": s.product.category, "score": s.score,
                    "confidence": s.confidence, "rank": s.rank, "reason": s.not_selected_reason} for s in scored if not s.selected],
        "excluded": [e.model_dump() for e in excluded], "rl_kategori": rlk_result,
        "teyitler": ctx.get("confirmations", []), "musteri_sorulari": ctx.get("client_questions", []),
    }
    ctx["_excluded_codes"], ctx["_names"] = excluded_codes, names
    client = get_client()
    agent_error = None

    def draft():
        nonlocal client, agent_error
        try:
            return client.draft_meeting_text(ctx)
        except Exception as exc:  # LLM sunucusu yoksa / hata verirse: şablona dön, nedeni kayda geçir
            if isinstance(client, PlaceholderLLM):
                raise
            agent_error = f"{type(exc).__name__}: {exc}"
            client = PlaceholderLLM()
            return client.draft_meeting_text(ctx)

    text = tr.run("metin_taslagi", "llm", draft, lambda r: f"{client.name}: {len(r)} karakter"
                  + (f" (ajan hatası, şablon kullanıldı: {agent_error[:120]})" if agent_error else ""))
    agent_log = getattr(client, "last", None)
    if agent_log:
        allowed |= set(agent_log.get("izin_verilen_oranlar", []))   # ajanın araç çıktılarındaki sayılar
    violations = tr.run("metin_guard", "deterministik", lambda: guard.check(text, excluded_codes, allowed, names),
                        lambda r: "temiz" if not r else f"{len(r)} ihlal")
    guard_info = {"violations": violations, "fallback_used": False, "muaf_adlar": names}
    if violations and not isinstance(client, PlaceholderLLM):
        text = PlaceholderLLM().draft_meeting_text(ctx)
        guard_info.update(fallback_used=True, violations_after=guard.check(text, excluded_codes, allowed, names))

    # RM'e gösterilen uyarılar: genel uyarılar + kısa listedeki ürünlerinkiler. Diğer ürünlerin uyarıları sayı olarak tutulur.
    sel_ids = {s.product.product_id for s in selected}
    shown = [f for f in flags if f.product_id is None or f.product_id in sel_ids]
    hidden = len(flags) - len(shown)
    record.update(
        status="rm_onayi_bekliyor" if selected else "oneri_yok",
        profile_view=view.model_dump(),
        weights=W,
        market=[m.model_dump() for m in market],
        flags=[f.model_dump() for f in shown],
        hidden_flag_count=hidden,
        shortlist=[s.model_dump() for s in selected],
        others=[{"product_id": s.product.product_id, "name": s.product.name, "category": s.product.category,
                 "score": s.score, "confidence": s.confidence, "rank": s.rank, "reason": s.not_selected_reason,
                 "breakdown": [c.model_dump() for c in s.breakdown]} for s in scored if not s.selected],
        excluded=[e.model_dump() for e in excluded],
        universe_size=len(products),
        llm=client.name,
        draft_text=text,
        rl=rl_result,
        rl_kategori=rlk_result,
        guard=guard_info,
        ajan=({k: v for k, v in agent_log.items() if k != "izin_verilen_oranlar"} if agent_log else
              ({"hata": agent_error} if agent_error else None)),
        teyitler=ctx.get("confirmations", []), musteri_sorulari=ctx.get("client_questions", []),
        trace=[s.model_dump() for s in tr.steps],
    )
    evidence.save(record)
    return record
