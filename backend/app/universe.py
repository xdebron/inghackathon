"""Veri kaynaklarından ürün evrenini ve piyasa bağlamını kurar.

Kaynak önceliği (gösterge başına): EVDS canlı > manuel kayıt > EVDS önbellek (bayat) > eksik.
Fon ürünleri iki TEFAS dışa aktarımının birleşimidir: Tarihsel Veriler (fiyat geçmişi -> metrikler)
ve Fon Karşılaştırma (dönemsel getiriler -> ikinci kaynak / çapraz kontrol).
"""
from __future__ import annotations

import json
import os
from datetime import date

from .config import BASE_DIR, SNAPSHOT_DIR, decision_config, norm_text, now_iso
from .db import store
from .models import DataPoint, Flag, Product
from .sources import evds, manual, tcmb, tefas_import

CONFIG_SOURCE = "Varsayım (config/decision_config.json)"
MANUAL_ONLY_INDICATORS = ("dibs_gosterge_2y", "tmsf_sigorta_limiti_tl")


def assumption(key: str, value, note: str) -> DataPoint:
    return DataPoint(key=key, value=value, source=CONFIG_SOURCE, status="assumption", note=note,
                     fetched_at=now_iso())


def derived(key: str, value, inputs: list[DataPoint], formula: str) -> DataPoint:
    """Başka veri noktalarından hesaplanan değer; durumu girdilerin en zayıfıdır."""
    order = ["missing", "conflict", "stale", "assumption", "ok"]
    status = min((i.status for i in inputs), key=order.index) if inputs else "ok"
    if value is None:
        status = "missing"
    return DataPoint(key=key, value=value, source=f"Hesaplama: {formula}", status=status,
                     as_of=max((i.as_of or "" for i in inputs), default=None) or None, fetched_at=now_iso(),
                     note="Girdiler: " + ", ".join(f"{i.key} ({i.source})" for i in inputs))


def detect_category(name: str) -> str | None:
    n = norm_text(name)
    for pattern, cat in decision_config()["kategori_tespiti"]["kaliplar"]:
        if pattern in n:
            return cat
    return None


def estimate_rd(vol: float) -> int:
    bands = decision_config()["rd_tahmini"]["bantlar_yillik_vol"]
    for i, upper in enumerate(bands):
        if vol < upper:
            return i + 1
    return 7


def _category_fields(pid: str, cat: str) -> dict[str, DataPoint]:
    c = decision_config()["kategoriler"][cat]
    return {
        "gelir_uretimi": assumption(f"{pid}.gelir_uretimi", c["gelir_uretimi"], f"{c['ad']} için yapısal gelir üretimi varsayımı"),
        "anapara_koruma": assumption(f"{pid}.anapara_koruma", c["anapara_koruma"], f"{c['ad']} için yapısal anapara koruma varsayımı"),
        "yapi_sadeligi": assumption(f"{pid}.yapi_sadeligi", c["yapi_sadeligi"], f"{c['ad']} için ürün yapısı sadeliği varsayımı"),
    }


# --- Göstergeler ------------------------------------------------------------------------------

def _cross_check_indicator(ind: str, live: DataPoint | None, man: DataPoint | None, flags: list[Flag] | None):
    """EVDS ve manuel kaynak ikisi de varsa karşılaştırır; fark eşiği aşarsa RM teyit uyarısı."""
    if not (live and man and live.status == "ok" and man.status == "ok"):
        return
    try:
        a, b = float(live.value), float(man.value)
    except (TypeError, ValueError):
        return
    tol = 0.005 if abs(b) < 5 else abs(b) * 0.01
    if abs(a - b) > tol and flags is not None:
        flags.append(Flag(level="teyit", message=f"{ind}: EVDS {a:.4g} ({live.as_of}) ile {man.source} {b:.4g} ({man.as_of}) farklı; EVDS kullanıldı."))


def indicators(raw: dict, disabled: set[str], flags: list[Flag] | None = None) -> dict[str, DataPoint]:
    manual_recs = raw.get("gostergeler", {}) if "manuel" not in disabled else {}
    out: dict[str, DataPoint] = {}
    ids = list(evds.series_config()["seriler"]) + list(MANUAL_ONLY_INDICATORS)
    for ind in ids:
        live = None
        if ind in evds.series_config()["seriler"] and "evds" not in disabled:
            live = evds.fetch(ind)
        man = manual.point(ind, manual_recs[ind].get("deger"), manual_recs[ind]) if ind in manual_recs else None
        _cross_check_indicator(ind, live, man, flags)
        candidates = [c for c in (live if live and live.status == "ok" else None,
                                  man if man and man.status == "ok" else None,
                                  live if live and live.status == "stale" else None) if c]
        if candidates:
            out[ind] = candidates[0]
        else:
            notes = [x.note for x in (live, man) if x and x.note]
            if "evds" in disabled and ind in evds.series_config()["seriler"]:
                notes.insert(0, "EVDS devre dışı (kesinti simülasyonu)")
            out[ind] = DataPoint(key=ind, source="EVDS / manuel", status="missing", fetched_at=now_iso(),
                                 note="; ".join(notes) or "Veri yok (EVDS anahtarı veya manuel kayıt gerekli).")
    return out


def _stopaj_points(raw: dict) -> dict[str, DataPoint]:
    out = {}
    for rec in raw.get("stopaj", []):
        cat = rec.get("kategori")
        if cat:
            out[cat] = manual.point(f"stopaj.{cat}", rec.get("oran"), rec, unit="oran")
    return out


def _add_real_return(p: Product, tufe: DataPoint | None):
    nominal = p.fields.get("getiri_1y")
    if not nominal or nominal.value is None:
        return
    if tufe is None or tufe.value is None:
        p.fields["reel_getiri_1y"] = derived(f"{p.product_id}.reel_getiri_1y", None, [nominal], "(1+nominal)/(1+TÜFE)-1")
        p.fields["reel_getiri_1y"].note = "TÜFE verisi yok."
        return
    p.fields["reel_getiri_1y"] = derived(f"{p.product_id}.reel_getiri_1y",
                                         (1 + nominal.value) / (1 + tufe.value) - 1, [nominal, tufe],
                                         "(1+nominal getiri)/(1+yıllık TÜFE)-1")


# --- Fonlar -----------------------------------------------------------------------------------

def _kap_snapshot() -> dict:
    d = store.kap_funds()
    return d["fonlar"] if d else {}


def _fund_products(metrics: dict, meta: dict, comparison: dict, cmp_meta: dict | None, raw: dict,
                   stopaj: dict, benchmarks: list[dict], flags: list[Flag]) -> list[Product]:
    q = decision_config()["veri_kalitesi"]
    tol = decision_config()["capraz_kontrol"]["tefas_getiri_fark_puan"]
    extra = {**_kap_snapshot(), **raw.get("fon_ek_bilgi", {})}  # elle girilen kayıt KAP aktarımının önüne geçer
    api_check, api_meta = tefas_import.api_cross_check()
    ing_codes_src = store.platform_funds("ING")
    ing_codes = set(ing_codes_src["kodlar"]) if ing_codes_src else None
    products = []
    today = date.today()
    for kod in sorted(set(metrics) | set(comparison)):
        m, c = metrics.get(kod), comparison.get(kod)
        pid = f"FON:{kod}"
        name = (m or {}).get("ad") or (c or {}).get("ad") or kod
        cat = detect_category(name) or (detect_category(str(c.get("tur"))) if c and c.get("tur") else None) or "bilinmiyor"
        cat_cfg = decision_config()["kategoriler"].get(cat)
        fields: dict[str, DataPoint] = {}

        if m:
            as_of = m["son_tarih"]
            age = (today - date.fromisoformat(as_of)).days
            stale = age > q["bayat_veri_gun"]

            def mp(field, value, unit=None, note=None):
                dp = tefas_import.metric_point(kod, field, value, meta, as_of, unit, note)
                if stale and dp.status == "ok":
                    dp.status, dp.note = "stale", f"Son fiyat tarihi {as_of} ({age} gün önce)."
                return dp

            fields.update({
                "getiri_1y": mp("getiri_1y", m["getiri_1y"], "oran", None if m["getiri_1y"] is not None else "1 yıllık geçmiş yok"),
                "getiri_3a": mp("getiri_3a", m["getiri_3a"], "oran"),
                "getiri_1a": mp("getiri_1a", m["getiri_1a"], "oran"),
                "volatilite": mp("volatilite_yillik", m["volatilite_yillik"], "oran",
                                 None if m["volatilite_yillik"] is not None else f"En az {q['min_gozlem']} gözlem gerekli"),
                "max_dusus": mp("max_dusus", m["max_dusus"], "oran"),
                "buyukluk": mp("buyukluk", m["buyukluk"], "TRY"),
                "kisi": mp("kisi", m["kisi"], "kişi"),
                "gozlem": mp("gozlem", m["gozlem"], "gün"),
            })
            if m["kalite_sorunlari"]:
                fields["kalite"] = mp("kalite", "; ".join(m["kalite_sorunlari"]))
                fields["kalite"].status = "conflict"
                flags.append(Flag(product_id=pid, level="teyit",
                                  message=f"{kod}: fiyat serisinde kalite sorunu: {'; '.join(m['kalite_sorunlari'])}"))
            if stale:
                flags.append(Flag(product_id=pid, level="uyari", message=f"{kod}: veri bayat, son fiyat {as_of}."))

        # İkinci kaynak: Fon Karşılaştırma
        if c and cmp_meta:
            prov = dict(source=tefas_import.COMPARISON_SOURCE, url=tefas_import.COMPARISON_URL,
                        as_of=c.get("as_of") or cmp_meta.get("as_of"), fetched_at=cmp_meta.get("iceri_alma_zamani"))
            for f in ("getiri_1y", "getiri_3a", "getiri_1a"):
                cv = c.get(f)
                if cv is None:
                    continue
                cdp = DataPoint(key=f"fon.{kod}.{f}.karsilastirma", value=cv, unit="oran", **prov)
                fields[f"{f}_karsilastirma"] = cdp
                own = fields.get(f)
                if own is None or own.value is None:
                    fields[f] = cdp.model_copy(update={"key": f"fon.{kod}.{f}",
                                                       "note": "Fiyat geçmişi yok; TEFAS karşılaştırma getirisi kullanıldı."})
                elif abs(own.value - cv) > tol:
                    fields[f] = own.model_copy(update={
                        "status": "conflict",
                        "note": f"Fiyat geçmişinden hesaplanan %{own.value*100:.2f}, TEFAS karşılaştırma %{cv*100:.2f} "
                                f"(fark eşiği {tol*100:.0f} puan). Tarih aralıkları teyit edilmeli."})
                    flags.append(Flag(product_id=pid, level="teyit",
                                      message=f"{kod}: {f} iki TEFAS kaynağında farklı ({own.value*100:.2f} / {cv*100:.2f})."))

        ex = extra.get(kod) or {}
        est = estimate_rd(m["volatilite_yillik"]) if m and m["volatilite_yillik"] is not None else None
        official = ex.get("resmi_rd")
        tef_rd = None
        try:
            v = float(str((c or {}).get("resmi_rd_tefas")).strip())
            tef_rd = int(v) if v == int(v) and 1 <= v <= 7 else None
        except (TypeError, ValueError):
            pass
        tef_prov = dict(source=tefas_import.COMPARISON_SOURCE + " ('Fonun Risk Değeri')", url=tefas_import.COMPARISON_URL,
                        as_of=(c or {}).get("as_of") or (cmp_meta or {}).get("as_of"), fetched_at=(cmp_meta or {}).get("iceri_alma_zamani"))
        if official is None and tef_rd is not None:
            official, ex = tef_rd, {**ex, "resmi_rd": tef_rd, **tef_prov}
        if official is not None:
            rd_dp = manual.point(f"fon.{kod}.rd", official, ex)
            if tef_rd is not None and rd_dp.status == "ok" and ex.get("source") != tef_prov["source"]:
                if tef_rd == official:
                    rd_dp.note = ((rd_dp.note + " ") if rd_dp.note else "") + "KAP ve TEFAS risk değerleri aynı."
                else:
                    rd_dp = rd_dp.model_copy(update={
                        "value": max(tef_rd, official), "status": "conflict",
                        "note": f"KAP RD {official}, TEFAS RD {tef_rd}; muhafazakâr olan ({max(tef_rd, official)}) kullanıldı."})
                    flags.append(Flag(product_id=pid, level="teyit", message=f"{kod}: {rd_dp.note}"))
                    official = max(tef_rd, official)
            if est is not None and rd_dp.status == "ok" and abs(est - official) >= 2:
                rd_dp = rd_dp.model_copy(update={
                    "value": max(est, official), "status": "conflict",
                    "note": f"Resmi RD {official}, volatiliteden tahmin {est}; muhafazakâr olan ({max(est, official)}) kullanıldı."})
                flags.append(Flag(product_id=pid, level="teyit", message=f"{kod}: {rd_dp.note}"))
        elif est is not None:
            rd_dp = DataPoint(key=f"fon.{kod}.rd", value=est, source="Hesaplama: yıllık volatilite → SRRI bandı",
                              status="assumption", as_of=m["son_tarih"], fetched_at=now_iso(),
                              note="Resmi RD yüklenmedi; tahmini değer. KAP yatırımcı bilgi formundan teyit edilmeli.")
        else:
            rd_dp = DataPoint(key=f"fon.{kod}.rd", source="—", status="missing", fetched_at=now_iso(),
                              note="Ne resmi RD ne de volatilite hesaplanabilecek kadar fiyat geçmişi var.")
        fields["rd"] = rd_dp

        fields["yonetim_ucreti"] = (manual.point(f"fon.{kod}.yonetim_ucreti", ex.get("yonetim_ucreti_yillik"), ex, "oran")
                                    if ex else DataPoint(key=f"fon.{kod}.yonetim_ucreti", source="KAP (yüklenmedi)",
                                                         status="missing", note="Yönetim ücreti verisi yok."))
        if ex.get("rd_belirsiz") and fields["rd"].status == "ok":
            fields["rd"] = fields["rd"].model_copy(update={"note": ex["rd_belirsiz"]})
            flags.append(Flag(product_id=pid, level="teyit", message=f"{kod}: {ex['rd_belirsiz']}."))
        for f, unit in (("giris_komisyonu", "oran"), ("cikis_komisyonu", "oran"), ("isin", None), ("semsiye_turu", None)):
            if ex.get(f) is not None:
                fields[f] = manual.point(f"fon.{kod}.{f}", ex[f], ex, unit)
        if ex.get("cikis_komisyonu"):
            flags.append(Flag(product_id=pid, level="teyit",
                              message=f"{kod}: çıkış komisyonu %{ex['cikis_komisyonu']*100:.2f} (KAP); erken çıkış maliyeti RM'e gösterilmeli."))
        if ex.get("valor_satis_gun") is not None:
            # Valörün kendi kaynağı varsa alınma zamanı da ondan gelir (fon kaydının KAP çekim zamanı miras alınmaz)
            vsrc = {**ex, "fetched_at": None, **ex["valor_kaynak"]} if ex.get("valor_kaynak") else ex
            fields["valor_gun"] = manual.point(f"fon.{kod}.valor_gun", ex["valor_satis_gun"], vsrc, "iş günü")
            if ex.get("valor_kaynak", {}).get("not"):
                fields["valor_gun"].note = ex["valor_kaynak"]["not"]
        elif cat_cfg and cat_cfg["valor_gun"] is not None:
            fields["valor_gun"] = assumption(f"fon.{kod}.valor_gun", cat_cfg["valor_gun"],
                                             f"{cat_cfg['ad']} için kategori varsayılanı; izahnameden teyit edilmeli.")
        else:
            fields["valor_gun"] = DataPoint(key=f"fon.{kod}.valor_gun", source="—", status="missing", note="Valör bilinmiyor.")
        fields["nitelikli_yatirimci"] = (manual.point(f"fon.{kod}.nitelikli", ex.get("nitelikli_yatirimci"), ex)
                                         if ex else DataPoint(key=f"fon.{kod}.nitelikli", source="KAP (yüklenmedi)", status="missing"))
        if ex.get("nitelikli_notu"):
            fields["nitelikli_yatirimci"].note = ex["nitelikli_notu"]
        # İkincil kaynak çapraz kontrolü: TEFAS API fiyatları
        cc = api_check.get(kod)
        if cc is not None and api_meta:
            ok = cc["uyumsuz_gun"] == 0
            fields["api_capraz_kontrol"] = DataPoint(
                key=f"fon.{kod}.api_capraz_kontrol",
                value=f"{cc['karsilastirilan_gun']} ortak günde {cc['uyumsuz_gun']} uyumsuz fiyat",
                source=f"Çapraz kontrol: {api_meta['source']} (ikincil, {api_meta['yontem'][:40]}…)", url=api_meta.get("url"),
                as_of=api_meta["tarih_araligi"][1], fetched_at=api_meta["fetched_at"], status="ok" if ok else "conflict",
                note=None if ok else f"Örnek: {cc['ornek']}; en büyük göreli fark %{cc['max_goreli_fark']*100:.3f}")
            if not ok:
                flags.append(Flag(product_id=pid, level="teyit",
                                  message=f"{kod}: fiyat geçmişi iki TEFAS kaynağında {cc['uyumsuz_gun']} günde farklı (ilk: {cc['ornek']['tarih']})."))
        if ing_codes is not None:
            fields["ing_platformunda"] = DataPoint(key=f"fon.{kod}.ing", value=kod in ing_codes, source=ing_codes_src["source"],
                                                   url=ing_codes_src["url"], as_of=ing_codes_src["as_of"], fetched_at=ing_codes_src["as_of"],
                                                   note="Bilgi amaçlı; skora girmez.")
        if ex.get("uyari_notu"):
            fields["uyari_notu"] = manual.point(f"fon.{kod}.uyari_notu", ex["uyari_notu"], ex)
            flags.append(Flag(product_id=pid, level="teyit", message=f"{kod}: {ex['uyari_notu']} (kaynak: {ex.get('source', '?')})"))
        if cat in stopaj:
            fields["stopaj"] = stopaj[cat]
        for b in benchmarks:
            if cat in b.get("kategoriler", []) and fields.get("getiri_1y") and fields["getiri_1y"].value is not None:
                bdp = manual.point(f"benchmark.{b.get('ad')}", b.get("getiri_1y"), b, "oran")
                if bdp.status == "ok":
                    fields["benchmark_farki_1y"] = derived(f"{pid}.benchmark_farki_1y", fields["getiri_1y"].value - bdp.value,
                                                           [fields["getiri_1y"], bdp], f"fon 1 yıllık getiri − {b.get('ad')} 1 yıllık getiri")
                    break
        if cat_cfg:
            fields.update(_category_fields(pid, cat))
        products.append(Product(product_id=pid, name=name, category=cat,
                                currency=cat_cfg["para_birimi"] if cat_cfg else "?", fields=fields))
    return products


# --- Mevduat / katılım hesabı -----------------------------------------------------------------

def _deposit_products(raw: dict, stopaj: dict, ind: dict[str, DataPoint], flags: list[Flag]) -> list[Product]:
    cc = decision_config()["capraz_kontrol"]
    products = []
    for rec in raw.get("mevduat", []):
        banka, vade = rec.get("banka", "?"), rec.get("vade_gun")
        katilim = rec.get("tur") == "katilim"
        cat = "katilim_hesabi" if katilim else "mevduat_tl"
        pid = f"{'KATILIM' if katilim else 'MEVDUAT'}:{banka}:{vade}"
        rate = manual.point(f"{pid}.oran", rec.get("oran"), rec, "yıllık brüt oran")
        rate.note = ((rate.note + " ") if rate.note else "") + (
            "Geçmiş dönem kâr payı oranı; değişkendir, önceden belirlenmez." if katilim
            else "Sözleşme oranı (ileriye dönük); fon getirileri geçmiş performanstır.")
        fields = {
            "getiri_1y": rate,
            "volatilite": assumption(f"{pid}.volatilite", 0.0, "Vade boyunca fiyat dalgalanması yok."),
            "max_dusus": assumption(f"{pid}.max_dusus", 0.0, "Nominal anapara değer kaybı yok."),
            "rd": assumption(f"{pid}.rd", decision_config()["kategoriler"][cat]["varsayilan_rd"],
                             "En düşük risk grubunda varsayıldı; güvence tutarı TMSF limitine bağlı."),
            "yonetim_ucreti": assumption(f"{pid}.yonetim_ucreti", 0.0, "Yönetim ücreti yok."),
            "vade_gun": manual.point(f"{pid}.vade_gun", vade, rec, "gün"),
            "valor_gun": assumption(f"{pid}.valor_gun", 0, "Vade sonunda anında; vade bozulursa getiri kaybı."),
        }
        if ind.get("tmsf_sigorta_limiti_tl") and ind["tmsf_sigorta_limiti_tl"].status in ("ok", "stale"):
            fields["tmsf_limiti"] = ind["tmsf_sigorta_limiti_tl"]
        # Çapraz kontrol: sektör ortalaması
        if not katilim and rate.value is not None and vade is not None:
            band = next((b for b in cc["mevduat_bantlari"] if vade <= b["max_gun"]), None)
            sector = ind.get(band["gosterge"]) if band else None
            if sector and sector.value is not None:
                diff = rate.value - sector.value
                fields["sektor_farki"] = derived(f"{pid}.sektor_farki", diff, [rate, sector], "banka oranı − TCMB sektör ortalaması")
                if abs(diff) > cc["mevduat_sektor_sapma_puan"]:
                    flags.append(Flag(product_id=pid, level="teyit",
                                      message=f"{banka} {vade} gün oranı sektör ortalamasından {diff*100:+.1f} puan sapıyor; "
                                              f"oran ve koşullar (tutar, kampanya, yeni müşteri şartı) teyit edilmeli."))
        if cat in stopaj:
            fields["stopaj"] = stopaj[cat]
        fields.update(_category_fields(pid, cat))
        label = "katılım hesabı" if katilim else "TL vadeli mevduat"
        products.append(Product(product_id=pid, name=f"{banka} {vade} gün {label}", category=cat, currency="TRY", fields=fields))
    return products


# --- Doğrudan BIST 30 hisseleri ---------------------------------------------------------------

STOCK_CAT = "bist30_hisse"


def _stock_metrics(kod: str) -> dict | None:
    """Düzeltilmiş kapanıştan (temettü ve bölünme dahil toplam getiri) fon metrikleriyle aynı tanımlar."""
    import numpy as np
    import pandas as pd
    df = store.series(f"bist/{kod}")
    if df is None or "duzeltilmis_kapanis" not in df:
        return None
    s = pd.Series(df["duzeltilmis_kapanis"].to_numpy(dtype=float), index=pd.to_datetime(df["tarih"])).replace(0, np.nan).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if len(s) < 2:
        return None
    last = s.index[-1]

    def ret(days):
        base = s.loc[:last - pd.Timedelta(days=days)]
        return float(s.iloc[-1] / base.iloc[-1] - 1) if len(base) and (last - base.index[-1]).days <= days + 7 else None

    y = s.loc[last - pd.Timedelta(days=365):]
    r = y.pct_change().dropna()
    q = decision_config()["veri_kalitesi"]
    return {"son_tarih": str(last.date()), "getiri_1y": ret(365), "getiri_3a": ret(91), "getiri_1a": ret(30),
            "volatilite_yillik": float(r.std() * np.sqrt(252)) if len(r) >= q["min_gozlem"] else None,
            "max_dusus": float((y / y.cummax() - 1).min()) if len(y) else None, "gozlem": int(len(r))}


def _stock_products(stopaj: dict, flags: list[Flag]) -> list[Product]:
    meta = store.document("bist_meta")
    if not meta:
        return []
    comp = meta.get("bilesim", {})
    have = {k.split("/", 1)[1] for k in store.series_codes("bist")}
    codes = [c for c in comp.get("kodlar", []) if c in have]
    q = decision_config()["veri_kalitesi"]
    cat_cfg = decision_config()["kategoriler"][STOCK_CAT]
    src = meta.get("kaynak", "Yahoo Finance")
    today = date.today()
    if codes:
        flags.append(Flag(level="teyit", message=(
            f"BIST 30 hisseleri ({len(codes)} pay): fiyatlar {src}; bileşim '{comp.get('durum', '?')}' "
            f"({comp.get('kaynak', '?')}). Bileşim ve fiyatlar resmi Borsa İstanbul kaynağından teyit edilmeli.")))
    products = []
    for kod in codes:
        m = _stock_metrics(kod)
        if not m:
            continue
        s_meta = meta.get("seriler", {}).get(kod, {})
        pid = f"HISSE:{kod}"
        age = (today - date.fromisoformat(m["son_tarih"])).days
        stale = age > q["bayat_veri_gun"]
        prov = dict(source=f"{src} ({kod}.IS)", url=meta.get("url", "").replace("<KOD>", kod), as_of=m["son_tarih"],
                    fetched_at=meta.get("alinma"))

        def mp(field, value, unit=None, note=None):
            status = "missing" if value is None else ("stale" if stale else "ok")
            if stale and value is not None:
                note = f"Son fiyat tarihi {m['son_tarih']} ({age} gün önce)."
            return DataPoint(key=f"hisse.{kod}.{field}", value=value, unit=unit, status=status, note=note, **prov)

        tr_note = "Düzeltilmiş kapanıştan (temettü ve bölünme dahil toplam getiri)."
        fields = {
            "getiri_1y": mp("getiri_1y", m["getiri_1y"], "oran", tr_note), "getiri_3a": mp("getiri_3a", m["getiri_3a"], "oran", tr_note),
            "getiri_1a": mp("getiri_1a", m["getiri_1a"], "oran", tr_note),
            "volatilite": mp("volatilite_yillik", m["volatilite_yillik"], "oran"), "max_dusus": mp("max_dusus", m["max_dusus"], "oran"),
            "gozlem": mp("gozlem", m["gozlem"], "gün"),
        }
        if m["volatilite_yillik"] is not None:
            fields["rd"] = DataPoint(key=f"hisse.{kod}.rd", value=estimate_rd(m["volatilite_yillik"]),
                                     source="Hesaplama: yıllık volatilite → SRRI bandı", status="assumption", as_of=m["son_tarih"],
                                     fetched_at=now_iso(), note="Hisse senedinin resmi risk değeri yoktur; fon RD bantlarıyla tahmin.")
        else:
            fields["rd"] = DataPoint(key=f"hisse.{kod}.rd", source="—", status="missing", fetched_at=now_iso(),
                                     note="Volatilite için yeterli fiyat geçmişi yok.")
        fields["yonetim_ucreti"] = assumption(f"hisse.{kod}.yonetim_ucreti", 0.0,
                                              "Doğrudan hissede yönetim ücreti yok; aracı kurum komisyonu ve BSMV ayrıca (teyit).")
        fields["valor_gun"] = assumption(f"hisse.{kod}.valor_gun", decision_config()["rl_kategori"]["bist30"]["valor_gun"],
                                         "Pay piyasasında takas T+2.")
        fields["stopaj"] = stopaj.get(STOCK_CAT) or assumption(
            f"stopaj.{STOCK_CAT}", decision_config()["rl_kategori"]["bist30"]["stopaj"],
            decision_config()["rl_kategori"]["bist30"]["stopaj_notu"])
        if stale:
            flags.append(Flag(product_id=pid, level="uyari", message=f"{kod}: veri bayat, son fiyat {m['son_tarih']}."))
        fields.update(_category_fields(pid, STOCK_CAT))
        name = f"{s_meta.get('ad') or kod} ({kod}) hisse senedi"
        products.append(Product(product_id=pid, name=name, category=STOCK_CAT, currency=cat_cfg["para_birimi"], fields=fields))
    return products


# --- Fon dışı alternatifler: doğrudan DİBS, döviz mevduatı, kıymetli maden hesabı ----------------

def _series_metrics(sid: str) -> dict | None:
    """gecmis/<sid>.csv fiyat serisinden fonlarla aynı tanımlarla getiri / volatilite / max düşüş."""
    import numpy as np
    import pandas as pd
    d = store.series(sid)
    if d is None or "deger" not in d:
        return None
    s = pd.Series(d["deger"].to_numpy(dtype=float), index=pd.to_datetime(d["tarih"])).replace(0, np.nan).dropna().sort_index()
    if len(s) < 2:
        return None
    last = s.index[-1]

    def ret(days):
        base = s.loc[:last - pd.Timedelta(days=days)]
        return float(s.iloc[-1] / base.iloc[-1] - 1) if len(base) and (last - base.index[-1]).days <= days + 7 else None

    y = s.loc[last - pd.Timedelta(days=365):]
    r = y.pct_change().dropna()
    q = decision_config()["veri_kalitesi"]
    return {"son_tarih": str(last.date()), "getiri_1y": ret(365), "getiri_3a": ret(91), "getiri_1a": ret(30),
            "volatilite_yillik": float(r.std() * np.sqrt(252)) if len(r) >= q["min_gozlem"] else None,
            "max_dusus": float((y / y.cummax() - 1).min()) if len(y) else None, "gozlem": int(len(r))}


def _hist_dp(key: str, value, sid: str, as_of: str, unit=None, note=None) -> DataPoint:
    meta = store.series_meta("gecmis").get(sid, {})
    return DataPoint(key=key, value=value, unit=unit, source=meta.get("kaynak", "—"), url=meta.get("url"), as_of=as_of,
                     fetched_at=meta.get("alinma"), status="ok" if value is not None else "missing", note=note)


def _dibs_products(stopaj: dict, ind: dict[str, DataPoint], flags: list[Flag]) -> list[Product]:
    cfg = decision_config()["alternatif_urunler"]["dibs"]
    products = []
    for sid, cat, tenor in cfg["seriler"]:
        dp = history_point(sid, cfg["max_ihale_yasi_gun"])
        if dp is None:
            continue
        pid = f"DIBS:{sid.replace('dibs_', '').upper()}"
        rd = cfg["rd_vadeye_gore"][str(tenor)]
        real = cat == "dibs_tufe"
        fields = {}
        if real:
            fields["reel_getiri_ihale"] = dp.model_copy(update={"key": f"{pid}.reel_getiri"})
            tufe = ind.get("tufe_yillik")
            nominal = (1 + dp.value) * (1 + tufe.value) - 1 if tufe is not None and tufe.value is not None else None
            fields["getiri_1y"] = derived(f"{pid}.getiri_1y", nominal, [dp] + ([tufe] if tufe else []),
                                          "(1 + son ihale reel getirisi) × (1 + yıllık TÜFE) − 1 (nominal karşılık tahmini)")
        else:
            fields["getiri_1y"] = dp.model_copy(update={"key": f"{pid}.getiri_1y", "note": f"{cfg['not']} {dp.note or ''}".strip()})
        fields.update({
            "volatilite": DataPoint(key=f"{pid}.volatilite", source="—", status="missing", fetched_at=now_iso(),
                                    note="Doğrudan tahvilin günlük ikincil piyasa fiyatı veride yok."),
            "max_dusus": DataPoint(key=f"{pid}.max_dusus", source="—", status="missing", fetched_at=now_iso()),
            "rd": assumption(f"{pid}.rd", rd, cfg["rd_gerekce"]),
            "yonetim_ucreti": assumption(f"{pid}.yonetim_ucreti", 0.0, "Yönetim ücreti yok; saklama ve aracılık komisyonu ayrıca (teyit)."),
            "valor_gun": assumption(f"{pid}.valor_gun", 1, "İkincil piyasada satılabilir (BIST borçlanma araçları piyasası); fiyat riski vardır."),
            "vade_yil": assumption(f"{pid}.vade_yil", tenor, "Vade bandının temsili süresi."),
        })
        if cat in stopaj:
            fields["stopaj"] = stopaj[cat]
        fields.update(_category_fields(pid, cat))
        label = "TÜFE'ye endeksli" if real else "sabit faizli"
        products.append(Product(product_id=pid, name=f"{tenor} yıl vadeli {label} devlet tahvili (son Hazine ihalesi)",
                                category=cat, currency="TRY", fields=fields))
    if products and "dibs_tl" not in stopaj:
        flags.append(Flag(level="teyit", message="Doğrudan DİBS için stopaj oranı yüklenmedi; tahvil faiz gelirinin vergilendirmesi RM tarafından teyit edilmeli."))
    return products


def _fx_deposit_products(stopaj: dict, ind: dict[str, DataPoint], flags: list[Flag]) -> list[Product]:
    """ING mevduat hesaplayıcısının USD/EUR satırları (Gamze Akkurt; 15.10.2026 yenilemesi)."""
    cfg = decision_config()["alternatif_urunler"]["doviz_mevduat"]
    rows = store.fx_deposit_rows("ING")
    if not rows:
        return []
    d = {"rows": rows}
    products = []
    fx_vol = {}
    for cur, sid in (("USD", "usdtry"), ("EUR", "eurtry")):
        m = _series_metrics(sid)
        fx_vol[cur] = (m or {}).get("volatilite_yillik")
    for r in d["rows"]:
        if r["currency"] not in ("USD", "EUR") or r["status"] != "ok" or r["day_count"] not in cfg["vadeler"]:
            continue
        # Kademe: TL karşılığı yaklaşık 1 mn TL'ye en yakın döviz tutarı
        spot = next((p.value for p in [history_point(f"{r['currency'].lower()}try", 7)] if p is not None), None)
        if spot is None:
            continue
        caps = sorted({x["capital"] for x in d["rows"] if x["currency"] == r["currency"]})
        tier = min(caps, key=lambda c: abs(c * spot - cfg["kademe_tl_karsiligi"]))
        if r["capital"] != tier:
            continue
        cur, vade = r["currency"], r["day_count"]
        pid = f"MEVDUAT_DOVIZ:ING:{cur}:{vade}"
        rec = {"source": "ING mevduat hesaplayıcısı", "url": r["source"], "as_of": r["fetched_at"][:10], "fetched_at": r["fetched_at"]}
        vol = fx_vol.get(cur)
        fields = {
            "getiri_1y": manual.point(f"{pid}.oran", r["annual_gross_rate_pct"] / 100, rec, f"yıllık brüt oran ({cur})"),
            "volatilite": _hist_dp(f"{pid}.kur_volatilite", vol, f"{cur.lower()}try", rec["as_of"], "oran",
                                   f"TL bazında {cur}/TRY kurunun son 1 yıllık volatilitesi."),
            "max_dusus": DataPoint(key=f"{pid}.max_dusus", source="—", status="missing", fetched_at=now_iso()),
            "rd": DataPoint(key=f"{pid}.rd", value=estimate_rd(vol) if vol is not None else None,
                            source="Hesaplama: kur volatilitesi → SPK bandı", status="assumption" if vol is not None else "missing",
                            as_of=rec["as_of"], fetched_at=now_iso(), note=cfg["rd_kaynagi"]),
            "yonetim_ucreti": assumption(f"{pid}.yonetim_ucreti", 0.0, "Yönetim ücreti yok."),
            "vade_gun": manual.point(f"{pid}.vade_gun", vade, rec, "gün"),
            "valor_gun": assumption(f"{pid}.valor_gun", 0, "Vade sonunda anında; vade bozulursa faiz kaybı."),
        }
        fields["getiri_1y"].note = (f"{cur} cinsinden faiz; TL getirisi kur hareketine bağlıdır. Kademe {tier:,} {cur} "
                                    f"(≈{cfg['kademe_tl_karsiligi']:,} TL).").replace(",", ".")
        sector = history_point(f"mevduat_{cur.lower()}_1a", 21) if vade <= 32 else None
        if sector is not None and fields["getiri_1y"].value is not None:
            fields["sektor_farki"] = derived(f"{pid}.sektor_farki", fields["getiri_1y"].value - sector.value,
                                             [fields["getiri_1y"], sector], f"banka oranı − TCMB {cur} tasarruf mevduatı ortalaması")
        if any(f"{cur} {vade} gün" in a for a in d.get("anomalies", [])):
            flags.append(Flag(product_id=pid, level="teyit",
                              message=f"ING {cur} {vade} gün: tutar arttıkça oran düşüyor (hesaplayıcı anomalisi); büyük tutar için oran RM tarafından teyit edilmeli."))
        if "mevduat_doviz" in stopaj:
            fields["stopaj"] = stopaj["mevduat_doviz"]
        fields.update(_category_fields(pid, "mevduat_doviz"))
        products.append(Product(product_id=pid, name=f"ING {vade} gün {cur} vadeli mevduat", category="mevduat_doviz",
                                currency=cur, fields=fields))
    return products


def _metal_account_products(stopaj: dict, flags: list[Flag]) -> list[Product]:
    cfg = decision_config()["alternatif_urunler"]["maden_hesabi"]
    q = decision_config()["veri_kalitesi"]
    products = []
    for sid, cur, label in cfg["seriler"]:
        m = _series_metrics(sid)
        if not m:
            continue
        pid = f"MADEN_HESABI:{cur}"
        a = m["son_tarih"]
        age = (date.today() - date.fromisoformat(a)).days
        fields = {
            "getiri_1y": _hist_dp(f"{pid}.getiri_1y", m["getiri_1y"], sid, a, "oran", "TL bazında son 1 yıl fiyat değişimi."),
            "getiri_3a": _hist_dp(f"{pid}.getiri_3a", m["getiri_3a"], sid, a, "oran"),
            "volatilite": _hist_dp(f"{pid}.volatilite", m["volatilite_yillik"], sid, a, "oran"),
            "max_dusus": _hist_dp(f"{pid}.max_dusus", m["max_dusus"], sid, a, "oran"),
            "rd": DataPoint(key=f"{pid}.rd", value=estimate_rd(m["volatilite_yillik"]) if m["volatilite_yillik"] is not None else None,
                            source="Hesaplama: yıllık volatilite → SPK bandı", status="assumption", as_of=a, fetched_at=now_iso(),
                            note="Maden hesabının resmi RD'si yoktur; fiyat volatilitesinden tahmin."),
            "yonetim_ucreti": assumption(f"{pid}.yonetim_ucreti", 0.0, cfg["not"]),
            "valor_gun": assumption(f"{pid}.valor_gun", 0, "Banka maden hesabında alım-satım anında."),
        }
        if age > q["bayat_veri_gun"]:
            for f in ("getiri_1y", "getiri_3a", "volatilite", "max_dusus"):
                fields[f] = fields[f].model_copy(update={"status": "stale", "note": f"Son fiyat {a} ({age} gün önce)."})
        if "maden_hesabi" in stopaj:
            fields["stopaj"] = stopaj["maden_hesabi"]
        fields.update(_category_fields(pid, "maden_hesabi"))
        products.append(Product(product_id=pid, name=f"{label} hesabı (banka kıymetli maden hesabı)", category="maden_hesabi",
                                currency=cur, fields=fields))
    if products:
        flags.append(Flag(level="teyit", message="Kıymetli maden hesapları: banka alış-satış makası ve vergilendirme veride yok; RM teyit etmeli."))
    return products


# --- Tarihçe serilerinden piyasa bağlamı (gecmis/) ------------------------------------------------

# id -> (bağlam anahtarı, en fazla kaç gün eski olabilir)
HISTORY_MARKET = {
    "tlref": ("gecmis.tlref", 7), "tcmb_aofm": ("gecmis.tcmb_aofm", 7),
    "dibs_tl_sabit_2y": ("gecmis.dibs_tl_sabit_2y", 120), "dibs_tl_sabit_10y": ("gecmis.dibs_tl_sabit_10y", 180),
    "dibs_tufe_reel_5y": ("gecmis.dibs_tufe_reel_5y", 180), "eurobond_hazine_fiyat": ("gecmis.eurobond_hazine_fiyat", 21),
    "hedge_prim_usd_1a": ("gecmis.hedge_prim_usd_1a", 21), "gumus_gram_tl": ("gecmis.gumus_gram_tl", 7),
    "altin_usd_ons": ("gecmis.altin_usd_ons", 7),
}


def history_point(sid: str, max_age_days: int, key: str | None = None) -> DataPoint | None:
    """gecmis/<sid>.csv serisinin son gözlemi; kaynak ve dönüşüm bilgisi gecmis/meta.json'dan."""
    meta = store.series_meta("gecmis").get(sid)
    s = store.series(sid)
    if not meta or s is None or "deger" not in s:
        return None
    s = s[["tarih", "deger"]].dropna()
    if s.empty:
        return None
    last = s.iloc[-1]
    as_of = str(last["tarih"])[:10]
    age = (date.today() - date.fromisoformat(as_of)).days
    note = "; ".join(x for x in (meta.get("ad"), meta.get("donusum"), meta.get("not")) if x)
    status = "assumption" if meta.get("turetilmis") else "ok"
    if age > max_age_days:
        status, note = "stale", f"Son gözlem {as_of} ({age} gün önce). " + note
    return DataPoint(key=key or f"gecmis.{sid}", value=float(last["deger"]), unit=meta.get("birim"), source=meta.get("kaynak", "—"),
                     url=meta.get("url"), as_of=as_of, fetched_at=meta.get("alinma"), status=status, note=note)


def _history_market() -> list[DataPoint]:
    out = []
    for sid, (key, max_age) in HISTORY_MARKET.items():
        dp = history_point(sid, max_age, key)
        if dp is not None:
            out.append(dp)
    return out


# --- Ana giriş --------------------------------------------------------------------------------

MARKET_KEYS = {
    "politika_faizi": "tcmb.politika_faizi", "tufe_yillik": "tufe_yillik", "altin_gram_tl": "altin_gram_tl",
    "dibs_gosterge_2y": "dibs_gosterge_2y", "tmsf_sigorta_limiti_tl": "tmsf_sigorta_limiti_tl",
    "mevduat_sektor_ortalamasi_1a": "mevduat_sektor_ortalamasi_1a", "mevduat_sektor_ortalamasi_3a": "mevduat_sektor_ortalamasi_3a",
    "mevduat_sektor_ortalamasi_6a": "mevduat_sektor_ortalamasi_6a", "mevduat_sektor_ortalamasi_1y": "mevduat_sektor_ortalamasi_1y",
}


def build(disabled_sources: list[str] | None = None) -> tuple[list[Product], list[DataPoint], list[Flag]]:
    """Ürün evreni, piyasa bağlamı veri noktaları ve veri uyarıları."""
    disabled = set(disabled_sources or [])
    flags: list[Flag] = []
    market: list[DataPoint] = []

    if os.getenv("SENTETIK_DEMO") == "1":
        # Sadece geliştirme: gerçek veri yüklenmeden arayüzü denemek için. Asla gerçek veri gibi sunulmaz.
        import sys
        sys.path.insert(0, str(BASE_DIR / "tests"))
        from conftest import synthetic_universe  # noqa: E402
        flags.append(Flag(level="uyari", message="SENTETİK DEMO VERİSİ: ürünler test fikstürüdür, gerçek piyasa verisi değildir."))
        prods = [] if "tefas" in disabled else synthetic_universe()
        return prods, tcmb.fetch() if "tcmb" not in disabled else [], flags

    if "tcmb" in disabled:
        market += [DataPoint(key=f"kur.{c}TRY", source=tcmb.SOURCE, url=tcmb.URL, fetched_at=now_iso(),
                             status="missing", note="Kaynak devre dışı (kesinti simülasyonu).") for c in tcmb.CURRENCIES]
    else:
        market += tcmb.fetch()

    raw = {} if "manuel" in disabled else manual.load_raw()
    if "manuel" in disabled:
        flags.append(Flag(level="uyari", message="Manuel veri kaynağı devre dışı: mevduat, stopaj, KAP ve manuel göstergeler kullanılamıyor."))
    ind = indicators(raw, disabled, flags)
    market += [dp.model_copy(update={"key": MARKET_KEYS.get(k, k)}) for k, dp in ind.items()]
    if "gecmis" not in disabled:
        market += _history_market()
    stopaj = _stopaj_points(raw)
    benchmarks = raw.get("benchmarklar", [])

    products: list[Product] = []
    if "tefas" in disabled:
        flags.append(Flag(level="uyari", message="TEFAS kaynağı devre dışı: fon ürünleri değerlendirilemiyor."))
    else:
        metrics, meta = tefas_import.load()
        comparison, cmp_meta = tefas_import.load_comparison()
        if not metrics and not comparison:
            flags.append(Flag(level="uyari", message="TEFAS fon verisi yüklenmemiş; fon ürünleri değerlendirilemiyor."))
        elif not comparison:
            flags.append(Flag(level="bilgi", message="TEFAS Fon Karşılaştırma dosyası yüklenmedi; fon getirileri tek kaynaktan (fiyat geçmişi)."))
        products += _fund_products(metrics, meta, comparison, cmp_meta, raw, stopaj, benchmarks, flags)
    deposits = _deposit_products(raw, stopaj, ind, flags)
    if not deposits and "manuel" not in disabled:
        flags.append(Flag(level="uyari", message="Mevduat / katılım hesabı oranı girilmemiş; bu alternatifler değerlendirilemiyor."))
    products += deposits
    if "bist" not in disabled:
        products += _stock_products(stopaj, flags)
    if "gecmis" not in disabled:
        products += _dibs_products(stopaj, ind, flags)
        products += _metal_account_products(stopaj, flags)
    if "manuel" not in disabled:
        products += _fx_deposit_products(stopaj, ind, flags)

    for p in products:
        _add_real_return(p, ind.get("tufe_yillik"))

    if not stopaj:
        flags.append(Flag(level="teyit", message="Stopaj oranları güncel kaynaktan yüklenmedi; getiriler brüttür, vergi etkisi RM tarafından teyit edilmeli."))
    if ind["tufe_yillik"].value is None:
        flags.append(Flag(level="uyari", message="TÜFE verisi yok; reel (enflasyon üstü) getiri hesaplanamadı."))
    for dp in market:
        if dp.status == "stale":
            flags.append(Flag(level="uyari", message=f"Piyasa verisi {dp.key}: {dp.note}"))
        elif dp.status == "missing" and dp.key.startswith("kur."):
            flags.append(Flag(level="uyari", message=f"Piyasa verisi {dp.key}: {dp.note}"))
    return products, market, flags
