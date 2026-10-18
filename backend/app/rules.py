"""Deterministik uygunluk kuralları. Bir ürün ilk ihlal ettiği kuralla dışlanır ve gerekçesi kaydedilir."""
from __future__ import annotations

from .config import decision_config
from .models import Exclusion, Flag, Product
from .profile import ProfileView


def _exclude(p: Product, rule_id: str, reason: str) -> Exclusion:
    return Exclusion(product_id=p.product_id, name=p.name, category=p.category, rule_id=rule_id, reason=reason)


def base_checks(p: Product, view: ProfileView) -> Exclusion | None:
    q = decision_config()["veri_kalitesi"]
    if p.category == "bilinmiyor":
        return _exclude(p, "R_KATEGORI", "Fon türü adından tespit edilemedi; sınıflandırılamayan ürün önerilmez.")
    if p.category == "serbest" and p.v("nitelikli_yatirimci") is not False:
        return _exclude(p, "R_NITELIKLI", "Serbest fon; personalarda nitelikli yatırımcı bilgisi yok ve fonun nitelikli yatırımcıya özel olmadığı teyit edilmedi.")
    rd = p.v("rd")
    if rd is None:
        return _exclude(p, "R_RISK_BILINMIYOR", "Risk değeri belirlenemedi (resmi RD yok, volatilite için yeterli veri yok); riski bilinmeyen ürün önerilmez.")
    if rd > view.max_rd:
        return _exclude(p, "R_RISK_PROFILI", f"Risk değeri RD {rd}, müşteri profilinin üst sınırı RD {view.max_rd}.")
    if p.v("getiri_1y") is None and p.v("getiri_3a") is None:
        return _exclude(p, "R_VERI_YETERSIZ", "Getiri verisi yok (1 yıllık ve 3 aylık); karşılaştırma yapılamaz.")
    size, people = p.v("buyukluk"), p.v("kisi")
    if p.product_id.startswith("FON:"):
        if size is not None and size < q["min_fon_buyuklugu_tl"]:
            return _exclude(p, "R_KUCUK_FON", f"Fon büyüklüğü {size/1e6:.1f} mn TL, eşik {q['min_fon_buyuklugu_tl']/1e6:.0f} mn TL.")
        if people is not None and people < q["min_yatirimci"]:
            return _exclude(p, "R_KUCUK_FON", f"Yatırımcı sayısı {people:.0f}, eşik {q['min_yatirimci']}.")
    if view.horizon_years <= 1 and rd >= 6:
        return _exclude(p, "R_UFUK", f"Kısa yatırım ufkunda (≈{view.horizon_years} yıl) RD {rd} oynaklık uygun değil.")
    return None


def constraint_checks(p: Product, view: ProfileView, flags: list[Flag]) -> Exclusion | None:
    for rule in view.rules:
        if rule["tip"] != "dislama":
            continue
        prm, rid, src = rule["parametre"], rule["id"], rule["kaynak_kisit"]
        if rid == "K_YUKSEK_RISK":
            rd = p.v("rd")
            if (rd is not None and rd >= prm["min_rd"]) or p.category in prm["kategoriler"]:
                return _exclude(p, rid, f"Açık kısıt '{src}': RD {rd}, kategori {p.category}.")
        elif rid == "K_LIKIDITE_KILIDI":
            vade = p.v("vade_gun")
            if vade is not None:
                if vade > prm["max_mevduat_vade_gun"]:
                    return _exclude(p, rid, f"Açık kısıt '{src}': {vade} gün vadeli mevduat kilitli sayılır (eşik {prm['max_mevduat_vade_gun']} gün).")
                continue
            valor = p.fields.get("valor_gun")
            if valor is None or valor.value is None:
                flags.append(Flag(product_id=p.product_id, level="teyit",
                                  message=f"{p.name}: satış valörü bilinmiyor; '{src}' kısıtı için teyit gerekli."))
            elif valor.value > prm["max_valor_gun"]:
                return _exclude(p, rid, f"Açık kısıt '{src}': satış valörü {valor.value} iş günü (eşik {prm['max_valor_gun']}).")
            elif valor.status == "assumption":
                flags.append(Flag(product_id=p.product_id, level="teyit",
                                  message=f"{p.name}: valör ({valor.value} iş günü) kategori varsayımı; izahnameden teyit edilmeli."))
        elif rid == "K_DOVIZ_ARTIRMAMA":
            if p.currency in prm["para_birimleri_haric"]:
                return _exclude(p, rid, f"Açık kısıt '{src}': {p.currency} bazlı ürün mevcut döviz gelirine ek maruziyet yaratır; RM gerekçe ile manuel ekleyebilir.")
        else:
            ex = _generic_exclusion(p, rid, prm, src, flags)
            if ex:
                return ex
    return None


def _generic_exclusion(p: Product, rid: str, prm: dict, src: str, flags: list[Flag]) -> Exclusion | None:
    """Katalog kısıtları için genel ürün süzgeci (config/constraint_rules.json 'parametre' alanları)."""
    is_fund = p.product_id.startswith("FON:")
    if "kategoriler_haric" in prm and p.category in prm["kategoriler_haric"]:
        return _exclude(p, rid, f"Açık kısıt '{src}': '{p.category}' kategorisi dışlanır.")
    if "yalniz_kategoriler" in prm and p.category not in prm["yalniz_kategoriler"]:
        return _exclude(p, rid, f"Açık kısıt '{src}': yalnızca {', '.join(prm['yalniz_kategoriler'])} kategorilerine izin var.")
    if "para_birimleri_haric" in prm and p.currency in prm["para_birimleri_haric"]:
        return _exclude(p, rid, f"Açık kısıt '{src}': {p.currency} bazlı ürün dışlanır.")
    if "max_yonetim_ucreti" in prm and is_fund:
        fee = p.v("yonetim_ucreti")
        if fee is None:
            flags.append(Flag(product_id=p.product_id, level="teyit", message=f"{p.name}: yönetim ücreti bilinmiyor; '{src}' kısıtı için teyit gerekli."))
        elif fee > prm["max_yonetim_ucreti"]:
            return _exclude(p, rid, f"Açık kısıt '{src}': yıllık yönetim ücreti %{fee*100:.2f} > %{prm['max_yonetim_ucreti']*100:.2f}.")
    if "min_fon_buyuklugu_tl" in prm and is_fund:
        size = p.v("buyukluk")
        if size is not None and size < prm["min_fon_buyuklugu_tl"]:
            return _exclude(p, rid, f"Açık kısıt '{src}': fon büyüklüğü {size/1e6:.0f} mn TL < {prm['min_fon_buyuklugu_tl']/1e6:.0f} mn TL.")
    if prm.get("yalniz_ing_platformu") and is_fund:
        ing = p.v("ing_platformunda")
        if ing is None:
            flags.append(Flag(product_id=p.product_id, level="teyit", message=f"{p.name}: ING platformunda olup olmadığı bilinmiyor; '{src}' için teyit gerekli."))
        elif ing is False:
            return _exclude(p, rid, f"Açık kısıt '{src}': fon ING platformunda değil.")
    if "max_mevduat_vade_gun" in prm:
        vade = p.v("vade_gun")
        if vade is not None and vade > prm["max_mevduat_vade_gun"]:
            return _exclude(p, rid, f"Açık kısıt '{src}': {vade} gün vade > {prm['max_mevduat_vade_gun']} gün.")
    return None


def apply(products: list[Product], view: ProfileView) -> tuple[list[Product], list[Exclusion], list[Flag]]:
    eligible, excluded, flags = [], [], []
    for p in products:
        local: list[Flag] = []
        ex = base_checks(p, view) or constraint_checks(p, view, local)
        if ex:
            excluded.append(ex)
        else:
            eligible.append(p)
            flags += local
    return eligible, excluded, flags
