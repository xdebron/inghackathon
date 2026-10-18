"""Görüşme hazırlık notunun içeriği (şablon ve ajan için ortak).

Not RL dağılımıyla kurulur: kategori modeli dağılımı seçer, her kategori kurallardan geçmiş ürünlere eşlenir (Öneriler
sekmesiyle aynı). Kural ve puanlama kısa listesi karşılaştırma olarak verilir. Teyit listesine yalnızca notta geçen
ürünlerin ve kategorilerin maddeleri girer; her uyarı notta bir kez yazılır.

Bölümler (NOTE_SECTIONS) guard tarafından da denetlenir: eksik ya da yarıda kesilmiş not RM'e gitmez.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from . import guard
from .config import decision_config
from .models import Flag, Persona

NOTE_TITLE = "GÖRÜŞME HAZIRLIK NOTU"
NOTE_SECTIONS = [
    "1) Görüşmenin amacı ve müşteri profili",
    "2) Piyasa bağlamı",
    "3) Önerilen dağılım (RL modeli)",
    "4) Karşılaştırma: kural ve puanlama kısa listesi",
    "5) Listede yer almayan seçenekler",
    "6) Görüşmeden önce teyit edilmesi gerekenler",
    "7) Müşteriye sorulabilecek sorular",
]

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
# Kategori düzeyinde bir kez yazılan notlar (her üründe tekrar etmesin)
CATEGORY_NOTES = {
    "bist30_hisse": "Doğrudan hisse: tek şirket riski taşır; fiyatlar resmi olmayan kaynaktan (Yahoo Finance), risk değeri tahminidir.",
}
RL_CAT_NAMES = {
    "mevduat_tl": "TL mevduat", "para_piyasasi": "Para piyasası", "borclanma": "Borçlanma araçları", "katilim": "Katılım",
    "kiymetli_maden": "Kıymetli maden", "karma_degisken": "Karma / değişken", "fon_sepeti": "Fon sepeti", "hisse": "Hisse fonu",
    "yabanci_hisse": "Yabancı hisse", "eurobond_doviz": "Eurobond / döviz", "bist30_hisse": "BIST 30 hisseleri",
}
FIELD_NAMES = {"rd": "risk değeri", "getiri_1y": "1 yıllık getiri", "volatilite": "oynaklık", "valor_gun": "valör",
               "yonetim_ucreti": "yönetim ücreti", "stopaj": "stopaj", "buyukluk": "fon büyüklüğü"}

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
    # Anahtardaki '1a' kullanılan mevduat vadesidir; değer yıllık primdir (kapalı faiz paritesi)
    "gecmis.hedge_prim_usd_1a": ("USD/TRY vadeli kur primi, yıllık oran (kısa vadeli TL ve USD mevduat faizlerinden faiz paritesi; banka kotasyonu değil)", "pct"),
    "altin_gram_tl": ("Gram altın (TL)", "num2"),
    "gecmis.gumus_gram_tl": ("Gram gümüş (TL)", "num2"),
    "gecmis.eurobond_hazine_fiyat": ("Hazine eurobond stoku ortalama fiyatı", "num2"),
}
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


# --- Biçim ---------------------------------------------------------------------------------------------

def fmt_date(s: str | None) -> str:
    """Kaynaklardaki farklı tarih yazımlarını tek biçime çevirir: günlük GG.AA.YYYY, aylık 'Ay YYYY'."""
    if not s:
        return "tarih yok"
    s = str(s).strip()[:10]
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})", s):
        return f"{MONTHS[int(m[2]) - 1]} {m[1]}"
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s):
        return f"{m[3]}.{m[2]}.{m[1]}"
    if m := re.fullmatch(r"(\d{2})[-.](\d{2})[-.](\d{4})", s):
        return f"{m[1]}.{m[2]}.{m[3]}"
    return s


def _month_age(s: str | None, today: date) -> int | None:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", str(s or "").strip())
    return None if not m else (today.year - int(m[1])) * 12 + today.month - int(m[2])


def fmt_num(v: float, digits: int) -> str:
    return f"{v:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


class Pct:
    """Metne yazılan her oranı guard'ın izinli kümesine ekleyen biçimleyici."""

    def __init__(self, allowed: set[str]):
        self.allowed = allowed

    def __call__(self, x: float | None, nd: int = 1) -> str:
        if x is None:
            return "veri yok"
        s = f"%{abs(x) * 100:.{nd}f}".replace(".", ",")
        self.allowed.add(s)
        return ("−" + s) if x < 0 and round(abs(x) * 100, nd) > 0 else s

    def w(self, x: float) -> str:
        """Ağırlık: tam sayıysa ondalıksız."""
        v = x * 100
        return self(x, 0) if abs(v - round(v)) < 0.05 else self(x, 1)

    def text(self, t: str) -> str:
        """Kaynaklı uyarı metnindeki oranlar (veri kalitesi kontrollerinden) izinli sayılır."""
        self.allowed.update(m.replace(" ", "").replace(".", ",") for m in guard.PCT.findall(t))
        return t


# --- Bölümler ------------------------------------------------------------------------------------------

def market_lines(market, pct: Pct) -> list[str]:
    by_key = {m.key: m for m in market}
    lines = []
    for key, (label, kind) in MARKET_LABELS.items():
        dp = by_key.get(key)
        if dp is None or dp.value is None:
            continue
        val = pct(dp.value) if kind == "pct" else fmt_num(dp.value, 2 if kind == "num2" else 4)
        src = "hesaplama" if (dp.source or "").lower().startswith("hesaplama") else dp.source
        lines.append(f"{label}: {val} ({src}, {fmt_date(dp.as_of)}{', BAYAT' if dp.status == 'stale' else ''})")
    return lines


def client_questions(persona: Persona, view, qualified_question: bool = False) -> list[str]:
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
    if qualified_question:
        qs.append("Müşteri SPK tanımına göre nitelikli yatırımcı mı (gerekli varlık ya da deneyim koşullarını sağlıyor mu)?")
    return qs


def exclusion_summary(excluded, view) -> list[str]:
    """Kural bazında genel gerekçe (tek bir ürünün değeri grubun gerekçesi gibi yazılmaz)."""
    q = decision_config()["veri_kalitesi"]
    src = {r["id"]: r.get("kaynak_kisit") for r in view.rules}
    generic = {
        "R_KATEGORI": "fon türü sınıflandırılamadı",
        "R_NITELIKLI": ("serbest fon; yalnızca nitelikli yatırımcıya satılabilir ve müşterinin nitelikli yatırımcı olduğu teyit edilmedi"
                        if view.qualified is None else "serbest fon; müşteri nitelikli yatırımcı değil"),
        "R_RISK_BILINMIYOR": "risk değeri belirlenemedi (resmi RD yok, volatilite için yeterli veri yok)",
        "R_RISK_PROFILI": f"risk değeri profilin üst sınırının (RD {view.max_rd}) üstünde",
        "R_VERI_YETERSIZ": "getiri verisi yok (1 yıllık ve 3 aylık)",
        "R_KUCUK_FON": f"fon büyüklüğü {q['min_fon_buyuklugu_tl'] / 1e6:.0f} mn TL ya da yatırımcı sayısı {q['min_yatirimci']} eşiğinin altında",
        "R_UFUK": "kısa yatırım ufkunda yüksek oynaklık (RD 6 ve üzeri)",
    }
    counts = Counter(e.rule_id for e in excluded)
    first = {}
    for e in excluded:
        first.setdefault(e.rule_id, e.reason)
    out = []
    for rid, n in counts.most_common():
        reason = generic.get(rid) or (f"açık kısıt '{src[rid]}'" if src.get(rid) else first[rid])
        out.append(f"{fmt_num(n, 0)} ürün: {reason}.")
    return out


def _metrics(p, sp, pct: Pct) -> str:
    liq = next((c.explanation for c in sp.breakdown if c.criterion == "likidite"), None)
    parts = [f"1 yıllık getiri {pct(p.v('getiri_1y'))}" if p.v("getiri_1y") is not None
             else (f"3 aylık getiri {pct(p.v('getiri_3a'))} (1 yıllık veri yok)" if p.v("getiri_3a") is not None else "getiri verisi yok")]
    if p.v("reel_getiri_1y") is not None:
        parts.append(f"reel {pct(p.v('reel_getiri_1y'))}")
    if p.v("volatilite") is not None:
        parts.append(f"yıllık oynaklık {pct(p.v('volatilite'))}")
    if p.v("rd") is not None:
        parts.append(f"risk değeri {p.v('rd')}{' (tahmini)' if p.fields['rd'].status == 'assumption' else ''}")
    if liq:
        parts.append(liq)
    as_of = (p.fields.get("getiri_1y") or p.fields.get("getiri_3a"))
    return f"Göstergeler ({fmt_date(as_of.as_of) if as_of else 'tarih yok'}): " + ", ".join(parts) + "."


class _Book:
    """Uyarıları bir kez yazmak için: ürün uyarıları ve teyit maddeleri ayrımı."""

    def __init__(self, flags: list[Flag], pct: Pct):
        self.flags, self.pct = flags, pct
        self.confirm: list[str] = []
        self.seen: set[str] = set()

    def add_confirm(self, msg: str):
        k = msg.rstrip(". ")
        if k not in self.seen:
            self.seen.add(k)
            self.confirm.append(self.pct.text(k) + ".")

    def product(self, p) -> list[str]:
        """Ürünün 'dikkat' notları; teyit gerektirenler teyit listesine gider (ürün adıyla), burada tekrar edilmez."""
        caveats = []
        msgs = [f for f in self.flags if f.product_id == p.product_id]
        for f in msgs:
            m = f.message.rstrip(". ")
            if f.level == "teyit":
                code = p.product_id.split(":")[-1]
                self.add_confirm(m if (p.name in m or m.startswith(code)) else f"{p.name}: {m}")
            else:
                caveats.append(m)
        joined = " ".join(f.message for f in msgs)
        for k, dp in p.fields.items():
            if dp.status in ("stale", "conflict") and dp.note and dp.note.rstrip(". ") not in joined:
                caveats.append(f"{FIELD_NAMES.get(k, k)}: {dp.note.rstrip('. ')}")
        if p.product_id.startswith("FON:") and p.v("yonetim_ucreti") is None:
            caveats.append("yönetim ücreti verisi yok")
        if p.category in ("mevduat_tl", "katilim_hesabi"):
            lim = p.v("tmsf_limiti")
            caveats.append(f"TMSF güvencesi kişi başı {fmt_num(lim, 0)} TL'ye kadar" if lim is not None
                           else "TMSF sigorta limiti veride yok; güvence kapsamı teyit edilmeli")
        return [self.pct.text(c) for c in dict.fromkeys(caveats)]


def rl_section(r: dict, by_id: dict, book: _Book, pct: Pct, rule_ids: set[str] = frozenset()) -> list[str]:
    cats = decision_config()["kategoriler"]
    m = r.get("model") or {}
    period = m.get("donem") or {}
    lines = [f"Kategori dağılımını profile koşullu RL modeli (sinir ağı politikası) seçer; piyasa verisi {fmt_date(r.get('tarih'))}. Her kategori "
             "yalnızca bu müşterinin kurallarından geçen ürünlere eşlenir. Bu bölüm geçmiş verilerle yapılmış bir simülasyona "
             "dayanır, getiri tahmini değildir; fon getirileri geçmiş performanstır ve gelecekteki sonuçların göstergesi değildir."]
    order = list(dict.fromkeys(x["kategori"] for x in r["urun_eslemesi"]))
    cat_w = {x["kategori"]: x["kategori_agirligi"] for x in r["urun_eslemesi"]}
    lines.append("Dağılım: " + ", ".join(f"{RL_CAT_NAMES.get(c, c)} {pct.w(cat_w[c])}" for c in order) + ".")
    u = r.get("politika_belirsizligi")
    if u and u.get("secilen_olasilik") is not None:
        exp = ", ".join(f"{RL_CAT_NAMES.get(k, k)} {pct(v / 100)}" for k, v in sorted(u["beklenen_dagilim"].items(), key=lambda x: -x[1]) if v >= 5)
        p_sel = pct(u["secilen_olasilik"] / 100)
        lines.append((f"Belirsizlik: model bu dağılımı yalnızca {p_sel} olasılıkla seçiyor; birçok dağılım arasında kararsız. "
                      if u["secilen_olasilik"] < 20 else f"Modelin bu dağılımı seçme olasılığı {p_sel}. ")
                     + f"Olasılık ağırlıklı dağılım (yön göstergesi): {exp}.")
    t = r.get("persona_testi") or {}
    mdl, rule = t.get("Model (PPO, profile koşullu)") or t.get("DQN (profile koşullu)"), t.get("Para piyasası + mevduat/borçlanma")
    if mdl and rule and period.get("test"):
        a, b = mdl["ortalama_reel_kazanc"], rule["ortalama_reel_kazanc"]
        line = (f"Test dönemi ({fmt_date(period['test'][0])} – {fmt_date(period['test'][1])}; aynı ayarlarla eğitilmiş ve bu dönemi "
                f"görmemiş modeller): bu müşteri profilinde ortalama reel kazanç model {pct(a)}, basit kural (para piyasası + "
                f"mevduat/borçlanma) {pct(b)}.")
        if a < b:
            line += " Bu profilde basit kural test döneminde modelden daha iyi sonuç verdi; model dağılımını basit kuralla birlikte değerlendirin."
        lines.append(line + " Canlı model aynı ayarla tüm veride eğitildi.")
    g = r.get("guvenli_iyilestirme")
    if g and g.get("kaynak") == "taban_kural":
        lines.append("Güvenli iyileştirme: modelin kendi tercihi taban kuraldan güvenilir ölçüde iyi bulunmadığı için taban kural uygulandı.")
    if r.get("mevcut_dagilim"):
        cur = ", ".join(f"{RL_CAT_NAMES.get(k, k)} {pct.w(v)}" for k, v in r["mevcut_dagilim"].items())
        lines.append(f"RM'in girdiği mevcut dağılım: {cur}; önerilen dağılıma geçiş portföyün {pct(r['devir'])} kadarının el değiştirmesi demek.")
    else:
        lines.append("Mevcut portföy dağılımı girilmedi; model nakitten başlangıç varsaydı (geçiş maliyeti hesaba katılmadı).")
    if "K_YOGUNLASMA" in rule_ids:
        cap = decision_config()["rl"]["yogunlasma_max_agirlik"]
        lines.append(f"Yoğunlaşma kısıtı RL modelinde tek kategorinin payı en fazla {pct.w(cap)} olacak biçimde uygulanır; "
                     "kısa listede ise her kategoriden en fazla 1 ürün ve en az 3 kategori aranır.")
    for ci, c in enumerate(order, 1):
        items = [x for x in r["urun_eslemesi"] if x["kategori"] == c]
        lines += ["", f"{chr(96 + ci)}) {RL_CAT_NAMES.get(c, c)} – {pct.w(cat_w[c])}"]
        sps = [by_id.get(x.get("product_id")) for x in items]
        cavs = [book.product(sp.product) if sp else [] for sp in sps]
        common = [c_ for c_ in cavs[0] if all(c_ in cv for cv in cavs)] if len(cavs) > 1 else []
        first = next((sp for sp in sps if sp), None)
        if first:
            lines.append(f"   Rol: {CATEGORY_ROLES.get(first.product.category, cats.get(first.product.category, {}).get('ad', first.product.category))}")
        notes = [CATEGORY_NOTES[c]] if c in CATEGORY_NOTES else []
        notes += list(dict.fromkeys(x["takip_uyarisi"] for x in items if x.get("takip_uyarisi")))
        if common:
            notes.append(("İki üründe de: " if len(cavs) == 2 else "Tüm ürünlerde: ") + "; ".join(common))
        if notes:
            lines.append("   Kategori notu: " + " ".join(pct.text(n.rstrip(".") + ".") for n in notes))
        for k, (x, sp, cv) in enumerate(zip(items, sps, cavs), 1):
            lines.append(f"   {k}. {x.get('ad') or 'Bu kategoride uygun ürün yok'} – {pct.w(x['agirlik'])}")
            if sp is None:
                continue
            lines.append(f"      {_metrics(sp.product, sp, pct)}")
            rest = [c_ for c_ in cv if c_ not in common]
            if rest:
                lines.append("      Dikkat: " + "; ".join(rest) + ".")
    excl = []
    for s in r.get("dislanan_kategoriler") or []:
        code, _, why = s.partition(":")
        excl.append(f"{RL_CAT_NAMES.get(code.strip(), code.strip())} ({why.strip()})" if why else RL_CAT_NAMES.get(s, s))
    if excl:
        lines += ["", "Modelin kullanamadığı kategoriler: " + "; ".join(excl) + "."]
    return lines


def comparison_section(scored, rl_ids: set[str], book: _Book, pct: Pct) -> list[str]:
    sel = [s for s in scored if s.selected]
    if not sel:
        return ["Kurallardan geçip kısa listeye giren ürün yok."]
    lines = ["Uygun ürünlerin 8 kriterli puanlamayla sıralaması (ağırlıklar müşteri profilinden). RL dağılımı kategori başına bu "
             "listedeki ürünleri öncelikli aday olarak kullanır."]
    for i, sp in enumerate(sel, 1):
        p = sp.product
        tag = " [RL dağılımında]" if p.product_id in rl_ids else ""
        lines.append(f"{i}. {p.name} – skor {round(sp.score * 100)}/100{tag}")
        if p.product_id not in rl_ids:          # RL bölümünde yazılanın göstergeleri tekrar edilmez
            lines.append(f"   {_metrics(p, sp, pct)}")
            cav = book.product(p)
            if cav:
                lines.append("   Dikkat: " + "; ".join(cav) + ".")
    return lines


def relevant_flags(flags: list[Flag], note_ids: set[str], note_cats: set[str]) -> list[Flag]:
    """Notta geçen ürünlerin uyarıları ve yalnızca notta bulunan kategorilere ait genel uyarılar."""
    return [f for f in flags if (f.product_id in note_ids) if f.product_id] + \
           [f for f in flags if not f.product_id and (f.category is None or f.category in note_cats)]


def build(persona: Persona, view, scored, excluded, flags: list[Flag], products, market, rlk: dict,
          fallback_rl_lines: list[str], allowed: set[str]) -> dict:
    """Notun bölümlerini kurar. Dönen ctx hem şablonda (render) hem ajan araçlarında kullanılır."""
    pct = Pct(allowed)
    cats = decision_config()["kategoriler"]
    by_id = {s.product.product_id: s for s in scored}
    rl_ok = rlk.get("durum") == "tamam"
    rl_ids = {x["product_id"] for x in rlk.get("urun_eslemesi", []) if x.get("product_id")} if rl_ok else set()
    sel_ids = {s.product.product_id for s in scored if s.selected}
    note_ids = rl_ids | sel_ids
    note_cats = {by_id[i].product.category for i in note_ids if i in by_id}
    shown = relevant_flags(flags, note_ids, note_cats)
    book = _Book(shown, pct)

    profile = [
        f"Müşteri: {persona.profil}{f', {persona.yas} yaş' if persona.yas else ''}. Hedef: {persona.yatirim_amaci or '-'}.",
        f"Risk profili {persona.risk_profili}, ufuk {persona.yatirim_ufku}, likidite ihtiyacı {persona.likidite_ihtiyaci}, "
        f"gelir/nakit akışı ihtiyacı {persona.gelir_nakit_akisi_ihtiyaci}, döviz tercihi '{persona.doviz_maruz_kalma_tercihi}', "
        f"anapara koruma {persona.anapara_koruma_tercihi}.",
        f"Öncelikler: {', '.join(persona.oncelikler or []) or '-'}. Açık kısıtlar: {', '.join(persona.acik_kisitlar or []) or 'yok'}.",
        f"Yorumlanan sınırlar: risk üst sınırı RD {view.max_rd}, ufuk {fmt_num(view.horizon_years, 0 if view.horizon_years == int(view.horizon_years) else 1)} yıl.",
    ]
    if persona.rm_notu:
        profile.append(f"RM notu: {persona.rm_notu}")

    if rl_ok:
        section3 = rl_section(rlk, by_id, book, pct, {x["id"] for x in view.rules})
    else:
        section3 = [f"RL kategori modeli çalışmadı ({rlk.get('neden') or 'model yok'}); aşağıdaki karşılaştırma bölümündeki "
                    "kural ve puanlama kısa listesi esas alınmalı."] + [f"- {x}" for x in fallback_rl_lines]
    section4 = comparison_section(scored, rl_ids, book, pct)

    # Genel teyitler (ürüne bağlı olmayan): yalnızca ilgili kategoriler; ardından RM kararları ve veri tazeliği
    for f in shown:
        if not f.product_id and f.level == "teyit":
            book.add_confirm(f.message)
    for a in view.acknowledged:
        book.add_confirm(f"RM tarafından onaylanan çelişki: {a}")
    for c in view.ignored_constraints:
        book.add_confirm(f"RM kararıyla uygulanmayan kısıt: '{c}'")
    today = date.today()
    for dp in market:
        if dp.key == "tcmb.politika_faizi" and (_month_age(dp.as_of, today) or 0) >= 2:
            book.add_confirm(f"TCMB politika faizi verisi {fmt_date(dp.as_of)} tarihli; son PPK kararından sonra güncel oran teyit edilmeli")
    n_serbest = sum(1 for e in excluded if e.rule_id == "R_NITELIKLI"
                    and (getattr(products.get(e.product_id), "v", lambda k: None)("rd") or 99) <= view.max_rd)
    ask_q = view.qualified is None and n_serbest > 0
    if ask_q:
        book.add_confirm(f"Müşterinin nitelikli yatırımcı olup olmadığı teyit edilmedi; risk sınırına uyan {fmt_num(n_serbest, 0)} serbest fon bu "
                         "nedenle değerlendirmeye alınmadı. Nitelikli yatırımcıysa profilde işaretlenip hazırlık yenilenebilir")

    return {
        "persona": persona.model_dump(), "profile_lines": profile, "market_lines": market_lines(market, pct),
        "section3": section3, "section3_title": NOTE_SECTIONS[2] if rl_ok else "3) Önerilen dağılım (RL modeli): model çalışmadı",
        "section4": section4, "exclusions": exclusion_summary(excluded, view),
        "others_count": sum(1 for s in scored if not s.selected and s.product.product_id not in rl_ids),
        "confirmations": book.confirm, "client_questions": client_questions(persona, view, ask_q),
        "shown_flags": shown, "note_ids": note_ids,
    }


def render(ctx: dict) -> str:
    p = ctx["persona"]
    head = f"{NOTE_TITLE} – {p['persona_id']} ({p['profil']}{f', {p['yas']}' if p.get('yas') else ''})"
    lines = [head, "", NOTE_SECTIONS[0], *[f"- {x}" for x in ctx["profile_lines"]],
             "", NOTE_SECTIONS[1], *([f"- {m}" for m in ctx["market_lines"]] or ["- Piyasa göstergesi verisi yok."]),
             "", ctx["section3_title"], *ctx["section3"],
             "", NOTE_SECTIONS[3], *ctx["section4"],
             "", NOTE_SECTIONS[4], *([f"- {x}" for x in ctx["exclusions"]] or ["- Dışlanan ürün yok."])]
    if ctx.get("others_count"):
        lines.append(f"- Ayrıca {fmt_num(ctx['others_count'], 0)} ürün uygun ama kısa listede ve RL dağılımında yer almıyor.")
    lines += ["", NOTE_SECTIONS[5], *([f"- {x}" for x in ctx["confirmations"]] or ["- Yok."]),
              "", NOTE_SECTIONS[6], *[f"- {q}" for q in ctx["client_questions"]],
              "", decision_config()["metin_guard"]["zorunlu_ibare"]]
    return "\n".join(lines)
