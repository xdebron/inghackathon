"""RM hazırlık ajanı: lokal LLM (Qwen, vLLM OpenAI uyumlu uç) + tool calling.

Ajan kararı vermez; deterministik katmanın ve RL modelinin ürettiği bilgiyi ARAÇLARLA sorgulayıp RM için hazırlık notu
yazar. İş bölümü:
  - Veri araçları (profil, kısa liste, ürün detayı, dışlananlar, piyasa, teyitler): kural motoru ve skorlama çıktısı.
  - Model araçları (rl_karar, rl_neden = tema Shapley'i, rl_ne_olursa, rl_esik, rl_benzer_donemler, rl_guvenilirlik):
    gerçek RL ağının sorgulanması (app/rl_tools.py).
  - Guard: metindeki her oran araç çıktılarındaki bir sayıya dayanmalı; yasaklı ifade ve dışlanan ürün kodu yok;
    zorunlu ibare kod tarafından eklenir. İhlal → modele geri bildirim → en fazla N düzeltme turu → geçmezse şablon.
Her çağrı iz kaydına girer (araç, argüman, süre, sonuç özeti) ve kanıt kaydında saklanır.

Ortam değişkenleri: LLM_BASE_URL (varsayılan http://127.0.0.1:8001/v1), LLM_MODEL (varsayılan: sunucunun ilk modeli),
AJAN_MAX_ADIM, AJAN_DUZELTME.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import httpx

from . import guard, rl_tools
from .config import decision_config, now_iso
from .note import NOTE_SECTIONS, NOTE_TITLE

DEFAULT_BASE = "http://127.0.0.1:8001/v1"
MAX_RESULT_CHARS = 7000

KEY_FIELDS = ["getiri_1y", "reel_getiri_1y", "getiri_3a", "volatilite", "max_dusus", "rd", "valor_gun", "vade_gun",
              "yonetim_ucreti", "cikis_komisyonu", "stopaj", "benchmark_farki_1y", "sektor_farki", "tmsf_limiti",
              "api_capraz_kontrol", "getiri_1y_karsilastirma"]
PCT_FIELDS = {"getiri_1y", "reel_getiri_1y", "getiri_3a", "volatilite", "max_dusus", "yonetim_ucreti", "cikis_komisyonu",
              "stopaj", "benchmark_farki_1y", "sektor_farki", "getiri_1y_karsilastirma"}


class AgentError(RuntimeError):
    pass


def _pct(x: float, nd: int = 1) -> str:
    s = f"%{abs(x) * 100:.{nd}f}".replace(".", ",")
    return ("−" + s) if x < 0 and round(abs(x) * 100, nd) > 0 else s


def _fmt(field: str, v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        if field in PCT_FIELDS:
            return _pct(float(v), 2)
        if field == "tmsf_limiti":
            return f"{v:,.0f} TL".replace(",", ".")
    return v


# --- Çalıştırma görünümü (kanıt kaydı biçiminden) ---------------------------------------------------

class RunView:
    """Bir hazırlık çalıştırmasının ajan tarafından okunabilir görünümü. Kanıt kaydı (dict) ile aynı biçimdedir;
    hem pipeline içinde (metin adımından önce) hem kaydedilmiş bir çalıştırma üzerinde soru-cevapta kullanılır."""

    def __init__(self, rec: dict):
        self.r = rec

    @property
    def persona(self) -> dict:
        return self.r["persona"]

    def rl_args(self) -> dict:
        a = {"persona": self.persona, "rm_answers": self.r.get("rm_answers") or {}}
        cur = (self.r.get("rl_kategori") or {}).get("mevcut_dagilim")
        if cur:
            a["mevcut_dagilim"] = cur
        return a

    def product_brief(self, s: dict) -> dict:
        p = s["product"]
        f = p["fields"]
        fields = {}
        for k in KEY_FIELDS:
            dp = f.get(k)
            if not dp:
                continue
            fields[k] = {"deger": _fmt(k, dp.get("value")), "kaynak": dp.get("source"), "tarih": dp.get("as_of"),
                         "durum": dp.get("status"), **({"not": dp["note"]} if dp.get("note") else {})}
        top = sorted(s.get("breakdown", []), key=lambda c: -c["contribution"])[:3]
        return {"urun_id": p["product_id"], "ad": p["name"], "kategori": p["category"], "para_birimi": p["currency"],
                "sira": s.get("rank"), "skor": round(s["score"] * 100), "guven": round(s["confidence"] * 100),
                "one_cikan_kriterler": [f"{c['criterion']}: {c['explanation']}" for c in top],
                "alanlar": fields,
                "uyarilar": [fl["message"] for fl in self.r.get("flags", []) if fl.get("product_id") == p["product_id"]]}


def data_tools(rv: RunView) -> dict[str, tuple[dict, Callable[[dict], Any]]]:
    def profil(_):
        pv = rv.r.get("profile_view") or {}
        return {"persona": rv.persona,
                "yorum": {"risk_ust_siniri_rd": pv.get("max_rd"), "ufuk_yil": pv.get("horizon_years"),
                          "doviz_tercihi_0_1": pv.get("fx_pref"),
                          "uygulanan_kisit_kurallari": [{"id": r["id"], "kisit": r.get("kaynak_kisit"), "aciklama": r.get("aciklama")}
                                                       for r in pv.get("rules", [])],
                          "rm_onayli_celiskiler": pv.get("acknowledged"), "uygulanmayan_kisitlar": pv.get("ignored_constraints")},
                "kriter_agirliklari": {k: {"agirlik": _pct(v["weight"], 0), "neden": v["explanation"]}
                                       for k, v in (rv.r.get("weights") or {}).items()}}

    ni = rv.r.get("not_icerigi") or {}

    def kisa_liste(_):
        return {"urunler": [rv.product_brief(s) for s in rv.r.get("shortlist", [])],
                "not": "Fon getirileri geçmiş performanstır; mevduat oranları sözleşme oranıdır. Skor 0-100, güven veri kalitesidir. "
                       "Bu liste notun 4. bölümündeki karşılaştırmadır; önerilen dağılım onerilen_dagilim aracındadır."}

    def onerilen_dagilim(_):
        if not ni:
            raise rl_tools.ToolError("Bu kayıtta hazır dağılım bölümü yok; rl_karar ve kisa_liste kullan.")
        return {"baslik": ni.get("section3_title"), "satirlar": ni.get("section3"), "karsilastirma_satirlari": ni.get("section4"),
                "not": "Notun 3. ve 4. bölümleri bu satırlara dayanır. Ürün adlarını, ağırlıkları, oranları ve tarihleri aynen kullan; "
                       "kategori notlarını ve 'Dikkat' maddelerini birer kez yaz."}

    def urun_detay(a):
        pid = a.get("urun_id", "")
        for s in rv.r.get("shortlist", []):
            if s["product"]["product_id"] == pid:
                return {**rv.product_brief(s), "skor_dokumu": s.get("breakdown")}
        for o in rv.r.get("others", []):
            if o["product_id"] == pid:
                return {"urun_id": pid, "ad": o["name"], "durum": "uygun ama kısa listede değil", "neden": o["reason"],
                        "skor": round(o["score"] * 100)}
        for e in rv.r.get("excluded", []):
            if e["product_id"] == pid:
                return {"urun_id": pid, "durum": "dışlandı", "kural": e["rule_id"], "gerekce": e["reason"],
                        "not": "Dışlanan ürünler hazırlık notunda adıyla anılmaz."}
        valid = [s["product"]["product_id"] for s in rv.r.get("shortlist", [])]
        raise rl_tools.ToolError(f"Ürün bulunamadı: {pid}. Kimlik uydurma; yalnızca şu kısa liste kimliklerini kullan: "
                                 f"{', '.join(valid)}")

    def dislananlar(_):
        if ni.get("exclusions") is not None:
            return {"gerekceler": ni["exclusions"], "uygun_ama_listede_olmayan": ni.get("others_count"),
                    "not": "Dışlanan ürünlerin adlarını veya kodlarını metinde kullanma; gerekçeleri bu genel biçimde yaz."}
        out: dict[str, dict] = {}
        for e in rv.r.get("excluded", []):
            g = out.setdefault(e["rule_id"], {"adet": 0, "ornek_gerekce": e["reason"]})
            g["adet"] += 1
        others = rv.r.get("others", [])
        return {"dislanan_kural_bazinda": out, "uygun_ama_kisa_listede_olmayan": len(others),
                "not": "Dışlanan ürünlerin adlarını veya kodlarını metinde kullanma; yalnızca gerekçeleri genel olarak anlat."}

    def piyasa(_):
        if ni.get("market_lines"):
            return {"gostergeler": ni["market_lines"], "not": "Değer, kaynak ve tarihleri aynen kullan."}
        return {"gostergeler": [{"ad": m["key"], "deger": _fmt_market(m), "kaynak": m.get("source"), "tarih": m.get("as_of"),
                                 "durum": m.get("status"), **({"not": m["note"]} if m.get("note") else {})}
                                for m in rv.r.get("market", []) if m.get("value") is not None]}

    def teyitler(_):
        if ni.get("confirmations") is not None:
            return {"teyit_maddeleri": ni["confirmations"],
                    "not": "Notun 6. bölümü yalnızca bu maddelerden oluşur; başka genel madde ekleme, aynı maddeyi tekrarlama."}
        return {"uyarilar": [{"seviye": f["level"], "mesaj": f["message"]} for f in rv.r.get("flags", [])],
                "teyit_maddeleri": rv.r.get("teyitler", []),
                "gizlenen_diger_urun_uyarisi": rv.r.get("hidden_flag_count", 0)}

    def sorular(_):
        return {"oneriler": ni.get("client_questions") or rv.r.get("musteri_sorulari", [])}

    obj = lambda props=None, req=None: {"type": "object", "properties": props or {}, "required": req or []}
    return {
        "profil_ozeti": ({"description": "Müşteri personası, yorumlanmış risk sınırı, uygulanan kısıt kuralları ve kriter ağırlıkları.",
                          "parameters": obj()}, profil),
        "onerilen_dagilim": ({"description": "Notun 3. bölümü: RL modelinin kategori dağılımı, eşlenen ürünler (ağırlık, rol, "
                                             "göstergeler, dikkat notları), belirsizlik ve test dönemi kıyası; ayrıca 4. bölümün "
                                             "karşılaştırma satırları.", "parameters": obj()}, onerilen_dagilim),
        "kisa_liste": ({"description": "Kural ve puanlama kısa listesi (karşılaştırma): her ürün için skor, öne çıkan kriterler, "
                                       "göstergeler (değer, kaynak, tarih, durum) ve uyarılar.", "parameters": obj()}, kisa_liste),
        "urun_detay": ({"description": "Bir ürünün tüm göstergeleri ve skor dökümü; kısa listede değilse nedeni.",
                        "parameters": obj({"urun_id": {"type": "string", "description": "örn. FON:BGP veya MEVDUAT:ING:32"}},
                                          ["urun_id"])}, urun_detay),
        "dislanan_ozeti": ({"description": "Uygunluk kurallarıyla dışlanan ürünlerin kural bazında sayısı ve örnek gerekçesi.",
                            "parameters": obj()}, dislananlar),
        "piyasa_baglami": ({"description": "Piyasa göstergeleri (kur, TÜFE, politika faizi, sektör mevduat faizi vb.) kaynak ve "
                                           "tarihleriyle.", "parameters": obj()}, piyasa),
        "teyit_listesi": ({"description": "RM'in görüşmeden önce teyit etmesi gereken veri ve kısıt uyarıları.",
                           "parameters": obj()}, teyitler),
        "musteri_sorulari": ({"description": "Profile göre görüşmede müşteriye sorulabilecek soru önerileri.",
                              "parameters": obj()}, sorular),
    }


def _fmt_market(m: dict):
    v = m.get("value")
    if isinstance(v, (int, float)) and m.get("unit") == "oran":
        return _pct(float(v), 2)
    if isinstance(v, float):
        return f"{v:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return v


def model_tools(rv: RunView) -> dict[str, tuple[dict, Callable[[dict], Any]]]:
    out = {}
    for s in rl_tools.schemas():
        fn = s["function"]
        params = json.loads(json.dumps(fn["parameters"]))
        for k in ("persona_id", "mevcut_dagilim", "model"):
            params["properties"].pop(k, None)
        params["required"] = [r for r in params.get("required", []) if r not in ("persona_id",)]
        name = fn["name"]
        out[name] = ({"description": fn["description"], "parameters": params},
                     (lambda n: lambda a: rl_tools.call(n, {**a, **rv.rl_args()}))(name))
    return out


# --- İzin verilen oranlar (guard) -------------------------------------------------------------------

def _numbers(obj, out: set[float]):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, out)
    elif isinstance(obj, str):
        for m in re.findall(r"%\s?−?-?\d+(?:[.,]\d+)?", obj):
            out.add(float(m.replace("%", "").replace("−", "").replace("-", "").replace(",", ".").strip()) / 100)


def allowed_percents(results: list) -> set[str]:
    nums: set[float] = set()
    for r in results:
        _numbers(r, nums)
    allowed = set()
    for x in nums:
        for v in (abs(x), abs(x) * 100):
            for nd in (0, 1, 2):
                allowed.add(f"%{v:.{nd}f}".replace(".", ","))
    return allowed


# --- LLM istemcisi ------------------------------------------------------------------------------------

SYSTEM_PREP = """Sen bir Private Banking Relationship Manager'ın (RM) görüşme öncesi hazırlık ajanısın. Araçları kullanarak verileri ve modelleri sorgula, sonra RM için Türkçe bir hazırlık notu yaz.

Kurallar (kesin):
1. Her sayı, oran, tarih ve ürün bilgisi bir araç çıktısından gelmeli ve araçtaki yazımla aynen kullanılmalı (örn. "%46,50", "15.10.2026"). Araçta olmayan oran, getiri veya tahmin yazma; bilmiyorsan "veri yok" de. Oranları yüzde olarak yaz (0,3001 değil %30,0); ham alan adı, araç adı veya kod yazma.
2. Önerilen dağılım RL modelinden gelir: 3. bölümü onerilen_dagilim aracının satırlarına dayandır. Kural ve puanlama kısa listesi 4. bölümde karşılaştırmadır. Dışlanan ürünlerin adını veya kodunu yazma.
3. Getiri vaadi yok. "garanti", "kesin getiri", "risksiz", "tavsiye ediyorum" gibi ifadeler yasak. Geçmiş getirinin geleceği göstermediğini notta bir kez belirt.
4. Metin RM içindir: müşteriye hitap etme, RM yerine karar verme.
5. Modelin güvenilirlik uyarılarını ve basit kurala karşı test sonucunu gizleme. Bu müşteri için kıyas, onerilen_dagilim'daki "Test dönemi" satırıdır; rl_guvenilirlik'teki görülmemiş profil testi binlerce sentetik profilin genel ortalamasıdır, bu müşteriye özgü değildir ve "bu profilde" diye yazılmaz. Model nedenlerini rl_neden ile en fazla 3 maddede, güvenilirlik uyarılarını en fazla 2 cümlede 3. bölümün sonunda özetle.
6. Teyit bölümü yalnızca teyit_listesi maddelerinden oluşur. Bir uyarıyı notta yalnızca bir kez yaz (ürünün altında ya da teyit bölümünde, ikisinde birden değil).
7. Doğrudan notla başla: ilk satır başlıktır. "Notu yazıyorum", "---" gibi ara cümle ya da ayraç yazma.

Önerilen çalışma sırası: profil_ozeti, piyasa_baglami, onerilen_dagilim, rl_neden, rl_guvenilirlik, teyit_listesi, dislanan_ozeti, musteri_sorulari; gerekiyorsa kisa_liste, rl_esik, rl_ne_olursa. Bilgiyi topladıktan sonra (en fazla 3-4 tur) notu yaz.

Not biçimi (düz metin; başlıklar aynen bu yazımla ve sırayla, yedisi de zorunlu):
GÖRÜŞME HAZIRLIK NOTU – <persona kodu> (<profil>, <yaş>)
""" + "\n".join(NOTE_SECTIONS) + """
Son bölüm müşteriye sorulabilecek sorularla biter; 7. bölümden sonra not, özet ya da başka metin yazma. Metni yarıda bırakma, uzun listeleri kısalt."""

SYSTEM_QA = """Sen bir Private Banking RM'inin hazırlık asistanısın. RM'in sorusunu araçları kullanarak, kısa ve kaynaklı biçimde Türkçe yanıtla.
Kurallar: her sayı bir araç çıktısından gelmeli ve araçtaki yazımla aynen kullanılmalı; dışlanan ürünlerin adını veya kodunu yazma; getiri vaadi yok ("garanti", "kesin getiri", "risksiz", "tavsiye ediyorum" yasak); RM yerine karar verme. RL modeli bir simülasyondur; ne-olursa sorularında rl_ne_olursa veya rl_esik kullan ve güvenilirlik uyarılarını belirt. Cevabın sonunda kullandığın araçları ve verilerin tarihini kısaca an."""


class Agent:
    def __init__(self, base_url: str | None = None, model: str | None = None, max_steps: int | None = None,
                 revisions: int | None = None, timeout: float = 300.0, post: Callable | None = None):
        self.base = (base_url or os.getenv("LLM_BASE_URL", DEFAULT_BASE)).rstrip("/")
        self.model = model or os.getenv("LLM_MODEL")
        self.max_steps = max_steps or int(os.getenv("AJAN_MAX_ADIM", "6"))
        self.revisions = revisions if revisions is not None else int(os.getenv("AJAN_DUZELTME", "2"))
        self.timeout = timeout
        self._post = post or self._http_post

    def _http_post(self, payload: dict) -> dict:
        try:
            r = httpx.post(f"{self.base}/chat/completions", json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise AgentError(f"LLM sunucusuna ulaşılamadı ({self.base}): {type(e).__name__}: {e}") from e

    def _model_name(self) -> str:
        if self.model:
            return self.model
        try:
            r = httpx.get(f"{self.base}/models", timeout=10)
            r.raise_for_status()
            self.model = r.json()["data"][0]["id"]
            return self.model
        except Exception as e:
            raise AgentError(f"LLM sunucusundan model adı alınamadı ({self.base}): {type(e).__name__}: {e}") from e

    def _chat(self, messages, tools, tool_choice="auto") -> tuple[dict, dict]:
        payload = {"model": self._model_name(), "messages": messages, "temperature": 0.2, "max_tokens": 6000,
                   "chat_template_kwargs": {"enable_thinking": False}}
        if tools:
            payload["tools"], payload["tool_choice"] = tools, tool_choice
        d = self._post(payload)
        self.last_finish = d["choices"][0].get("finish_reason")
        return d["choices"][0]["message"], d.get("usage") or {}

    def run(self, rv: RunView, task: str, system: str, excluded_codes: list[str], allowed_names: list[str],
            append_disclaimer: bool = True, on_step: Callable[[dict], None] | None = None,
            tool_names: list[str] | None = None, disclaimer: str | None = None, structure: bool = False) -> dict:
        tools_map = {**data_tools(rv), **model_tools(rv)}
        if tool_names is not None:     # görev bazında araç kısıtı (örn. müşteri metninde iç uyarılar yok)
            tools_map = {k: v for k, v in tools_map.items() if k in tool_names}
        tools = [{"type": "function", "function": {"name": n, **spec}} for n, (spec, _) in tools_map.items()]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
        trace, results = _LiveList(on_step), []
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        t_start = time.perf_counter()
        text, steps, errors = None, 0, 0

        def chat(tc="auto", with_tools=True):
            t0 = time.perf_counter()
            msg, u = self._chat(messages, tools if with_tools else None, tc)
            for k in usage:
                usage[k] += int(u.get(k) or 0)
            return msg, round((time.perf_counter() - t0) * 1000)

        while steps < self.max_steps:
            steps += 1
            msg, ms = chat()
            calls = msg.get("tool_calls") or []
            if not calls:
                text = (msg.get("content") or "").strip()
                trace.append({"adim": steps, "tip": "yanit", "sure_ms": ms, "karakter": len(text)})
                messages.append({"role": "assistant", "content": text})
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            trace.append({"adim": steps, "tip": "llm", "sure_ms": ms, "arac_istegi": [c["function"]["name"] for c in calls]})
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                t0 = time.perf_counter()
                try:
                    if name not in tools_map:
                        raise rl_tools.ToolError(f"Bilinmeyen araç: {name}")
                    res = tools_map[name][1](args)
                    ok, err = True, None
                except Exception as e:  # araç hatası modele bildirilir; uydurmaması için açıkça
                    res, ok, err = {"hata": str(e)}, False, str(e)
                results.append(res)
                content = json.dumps(res, ensure_ascii=False, default=str)
                if len(content) > MAX_RESULT_CHARS:
                    content = content[:MAX_RESULT_CHARS] + ' …"(kısaltıldı)"'
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
                trace.append({"adim": steps, "tip": "arac", "ad": name, "argumanlar": args, "basarili": ok,
                              "sure_ms": round((time.perf_counter() - t0) * 1000), "sonuc_ozet": err or content[:300]})
                errors += 0 if ok else 1
            if errors >= 3:   # model geçersiz çağrılarda döngüye girdiyse: araçları kapat, elindekiyle yazdır
                trace.append({"adim": steps, "tip": "yonlendirme", "not": f"{errors} başarısız araç çağrısı; yazmaya yönlendirildi"})
                break
        if text is None:  # adım sınırı veya hata döngüsü: araçsız son yanıt iste
            messages.append({"role": "user", "content": "Araç çağırmayı bitir ve şimdi elindeki bilgilerle istenen metnin tamamını yaz."})
            msg, ms = chat(with_tools=False)
            text = (msg.get("content") or "").strip()
            trace.append({"adim": steps + 1, "tip": "yanit", "sure_ms": ms, "karakter": len(text), "not": "adım sınırı"})

        required = decision_config()["metin_guard"]["zorunlu_ibare"]
        disclaimer = disclaimer or required
        def finish(t: str) -> str:
            if structure and NOTE_TITLE in t:      # baştaki ajan ara cümleleri/ayraçlar notun parçası değildir
                t = t[t.index(NOTE_TITLE):]
            return (t.rstrip() + "\n\n" + disclaimer) if append_disclaimer and disclaimer not in t else t

        def violations_of(t: str) -> list[str]:
            v = guard.check(check_text(t), excluded_codes, allowed, allowed_names)
            if structure:
                v += guard.structure(t)
                if getattr(self, "last_finish", None) == "length":
                    v.append("Metin uzunluk sınırında kesildi; listeleri kısaltarak notun tamamını yaz")
            return v
        # Müşteri metninin ibaresi farklıdır; guard'ın zorunlu ibare kontrolü iç ibareyle karşılanır
        check_text = lambda t: t if required in t else t + "\n" + required
        allowed = allowed_percents(results)
        text = finish(text)
        violations = violations_of(text)
        rounds = 0
        while violations and rounds < self.revisions:
            rounds += 1
            messages.append({"role": "user", "content": "Metinde şu kural ihlalleri var:\n- " + "\n- ".join(violations)
                             + "\nYalnızca araç çıktılarındaki sayıları kullanarak, ihlalleri gidererek metnin TAMAMINI yeniden yaz."})
            msg, ms = chat(with_tools=False)
            text = finish((msg.get("content") or "").strip())
            violations = violations_of(text)
            trace.append({"adim": steps + rounds, "tip": "duzeltme", "sure_ms": ms, "kalan_ihlal": len(violations)})
            messages.append({"role": "assistant", "content": text})
        return {"metin": text, "ihlaller": violations, "duzeltme_turu": rounds, "izin_verilen_oranlar": sorted(allowed),
                "iz": trace, "model": self.model, "sunucu": self.base, "sure_sn": round(time.perf_counter() - t_start, 1),
                "token": usage, "arac_sayisi": sum(1 for t in trace if t["tip"] == "arac"), "baslangic": now_iso()}


class _LiveList(list):
    """İz listesi: her yeni adımda geri çağırma (arayüzde canlı izleme için)."""

    def __init__(self, cb):
        super().__init__()
        self.cb = cb

    def append(self, item):
        super().append(item)
        if self.cb:
            self.cb(item)


class AgentClient:
    """Pipeline'ın LLMClient arayüzü: draft_meeting_text(ctx). ctx["_run"] ajan görünümünü (kanıt kaydı biçimi) taşır."""
    name = "ajan"

    def __init__(self):
        self.agent = Agent()
        self.last: dict | None = None

    def draft_meeting_text(self, ctx: dict) -> str:
        rv = RunView(ctx["_run"])
        p = rv.persona
        task = (f"Persona {p.get('persona_id')} ({p.get('profil')}) için görüşme hazırlık notunu hazırla. "
                f"Önce araçlarla bilgileri topla, sonra notu belirtilen biçimde yaz.")
        res = self.agent.run(rv, task, SYSTEM_PREP, ctx["_excluded_codes"], ctx["_names"], structure=True)
        self.name = f"ajan:{res['model']}"
        self.last = res
        return res["metin"]


def _codes_names(rec: dict) -> tuple[list[str], list[str]]:
    excluded_codes = [e["product_id"].split(":", 1)[1] for e in rec.get("excluded", []) if e["product_id"].startswith("FON:")]
    names = [s["product"]["name"] for s in rec.get("shortlist", [])]
    names += [m["ad"] for m in (rec.get("rl_kategori") or {}).get("urun_eslemesi", []) if m.get("ad") and m["ad"] not in names]
    return excluded_codes, names


def write(rec: dict, on_step=None) -> dict:
    """Kaydedilmiş bir hazırlık çalıştırması için ajanla hazırlık notu yazar."""
    rv = RunView(rec)
    p = rv.persona
    codes, names = _codes_names(rec)
    task = (f"Persona {p.get('persona_id')} ({p.get('profil')}) için görüşme hazırlık notunu hazırla. "
            f"Önce araçlarla bilgileri topla, sonra notu belirtilen biçimde yaz.")
    return Agent().run(rv, task, SYSTEM_PREP, codes, names, on_step=on_step, structure=True)


def answer(rec: dict, question: str, on_step=None) -> dict:
    """Kaydedilmiş bir hazırlık çalıştırması üzerinde RM'in serbest sorusunu ajanla yanıtlar."""
    codes, names = _codes_names(rec)
    return Agent(revisions=1).run(RunView(rec), question, SYSTEM_QA, codes, names, append_disclaimer=False, on_step=on_step)



# --- Müşteri yüzü (yalnızca RM onayından sonra müşteriye gösterilir) ----------------------------------

CUSTOMER_DISCLAIMER = ("Bu özet danışmanınız tarafından onaylanmıştır ve bilgilendirme amaçlıdır; yatırım tavsiyesi değildir. "
                       "Geçmiş getiriler gelecekteki sonuçların göstergesi değildir. Karar vermeden önce danışmanınızla görüşün.")
CUSTOMER_TOOLS = ["profil_ozeti", "kisa_liste", "piyasa_baglami", "rl_karar"]

SYSTEM_CUSTOMER = """Sen bir Private Banking müşterisine, danışmanının (RM) onayladığı görüşme paketini sade ve anlaşılır Türkçe ile anlatan asistansın. Bu metin RM onayından sonra müşteriye gösterilecek.
Kurallar (kesin):
1. Sayılar yalnızca araç çıktılarından gelir ve araçtaki yazımla aynen kullanılır; araçta olmayan oran yazma.
2. Yalnızca kisa_liste'deki seçenekleri anlat; her biri için ne olduğu, ne işe yarayabileceği ve başlıca riski (dalgalanma, vade, faiz değişimi) birkaç sade cümleyle. Teknik kısaltmaları açıkla (örn. risk değeri 1-7 ölçeğinde, valör = paranın hesaba geçme süresi).
3. Getiri vaadi yok: "garanti", "kesin getiri", "risksiz", "tavsiye ediyorum" yasak. Geçmiş getirinin geleceği göstermediğini söyle.
4. Karar müşterinindir ve danışmanıyla birlikte verilir; "şunu alın" deme, "danışmanınızla şunları konuşabilirsiniz" de.
5. İç değerlendirme ayrıntıları (skor, model güven puanları, Shapley, kural kimlikleri, veri kalitesi uyarıları) yazma; model destekli analizi en fazla bir cümleyle anlat.
Biçim (kısa, en fazla ~250 kelime, Markdown kullanabilirsin):
Sayın müşterimiz, ... (1-2 cümle: hedefiniz ve profiliniz)
**Danışmanınızla konuşabileceğiniz seçenekler** (madde madde)
**Dikkat edilmesi gerekenler** (madde madde)
**Sonraki adım** (danışmanınızla görüşme)"""

SYSTEM_CUSTOMER_QA = """Sen bir Private Banking müşterisinin sorusuna, danışmanı (RM) adına bir YANIT TASLAĞI hazırlayan asistansın. Taslak RM onayından sonra müşteriye gösterilir.
Önce mutlaka kisa_liste ve profil_ozeti araçlarını çağır; yanıttaki her süre, oran veya ürün bilgisi bu araçlardan gelmeli, genel piyasa bilgisi veya "genellikle ... gün" gibi araçta olmayan bilgi yazma (valör = satıştan sonra paranın hesaba geçme süresi, T+0 aynı gün). Kurallar: sade ve kibar Türkçe; sayılar yalnızca araç çıktılarından; getiri vaadi yok ("garanti", "kesin getiri", "risksiz", "tavsiye ediyorum" yasak); müşteri yerine karar verme, gerekirse "danışmanınızla görüşmenizi öneririz" de; iç değerlendirme ayrıntılarını (skor, kural kimliği, model teknik terimleri) yazma. En fazla ~150 kelime."""


def customer_summary(rec: dict, on_step=None) -> dict:
    codes, names = _codes_names(rec)
    p = rec["persona"]
    task = f"{p.get('profil')} profilindeki müşterimiz için danışmanının onayladığı paketi sade bir özetle anlat."
    return Agent().run(RunView(rec), task, SYSTEM_CUSTOMER, codes, names, on_step=on_step,
                       tool_names=CUSTOMER_TOOLS, disclaimer=CUSTOMER_DISCLAIMER)


def customer_answer(rec: dict, question: str, on_step=None) -> dict:
    codes, names = _codes_names(rec)
    return Agent(revisions=1).run(RunView(rec), f"Müşterinin sorusu: {question}", SYSTEM_CUSTOMER_QA, codes, names,
                                  on_step=on_step, tool_names=CUSTOMER_TOOLS, disclaimer=CUSTOMER_DISCLAIMER)
