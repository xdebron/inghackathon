"""Gamze Akkurt'un topladığı verileri (gamze_work/.../data/processed) sistemin snapshot formatına aktarır.

Hiçbir kaynağa yeni istek atılmaz; yalnızca mevcut dosyalar dönüştürülür. Her kayıt orijinal kaynağını,
toplanma zamanını ve toplama yöntemini taşır. Tekrar çalıştırılabilir. manuel_veriler.json içinde elle girilmiş
kayıtlar korunur; bu script yalnızca kendi eklediği ("_aktaran": "import_gamze") kayıtları yeniler.

Kullanım:  backend/.venv/Scripts/python backend/tools/import_gamze.py [gamze processed klasörü]
"""
from __future__ import annotations

import gzip
import re
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "gamze_work" / "AGENTIC AI HACKATHON-ING" / "data" / "processed"
SNAP = ROOT / "backend" / "data" / "snapshots"
TAG = "import_gamze"
COLLECTOR = "Gamze Akkurt"


def J(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def day(iso: str | None) -> str | None:
    return iso[:10] if iso else None


def tefas_prices(src: Path, log: list):
    q = J(src / "data_quality_report.json")["tefas"]
    df = pd.read_csv(src / "tefas_prices_ing_universe.csv.gz")
    out = df.rename(columns={"date": "tarih", "code": "kod", "name": "ad", "price": "fiyat", "units": "pay",
                             "investors": "kisi", "total_value": "buyukluk"})[["tarih", "kod", "ad", "fiyat", "pay", "kisi", "buyukluk"]]
    out.sort_values(["kod", "tarih"]).to_csv(SNAP / "tefas_fiyatlar.csv", index=False)
    meta_path = SNAP / "tefas_meta.json"
    meta = J(meta_path) if meta_path.exists() else {"imports": [], "conflicts": []}
    meta["imports"] = [i for i in meta["imports"] if i.get("_aktaran") != TAG]
    export_at = datetime.strptime(q["export_at_max"], "%d.%m.%Y %H:%M:%S").isoformat()
    meta["imports"].append({
        "_aktaran": TAG,
        "dosya": f"{q['files']} adet TEFAS 'Fon Verileri' CSV dışa aktarımı (birleştirilmiş: tefas_prices_ing_universe.csv.gz)",
        "toplayan": COLLECTOR, "yontem": "TEFAS sitesinden elle dışa aktarım",
        "kaynak_export_zamani": q["export_at_max"],
        "iceri_alma_zamani": export_at,
        "satir": int(len(out)), "fon_sayisi": int(out["kod"].nunique()),
        "tarih_araligi": [str(out["tarih"].min()), str(out["tarih"].max())],
        "kalite": {k: q[k] for k in ("header_count_mismatches", "duplicate_rows_dropped", "conflicting_duplicates")},
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy(src / "data_quality_report.json", SNAP / "tefas_kalite_raporu.json")
    log.append(f"TEFAS fiyat geçmişi: {len(out)} satır, {out['kod'].nunique()} fon, {out['tarih'].min()} → {out['tarih'].max()}")


def tefas_api(src: Path, log: list):
    m = J(src / "tefas_prices_api_meta.json")
    df = pd.read_csv(src / "tefas_prices_api.csv.gz").rename(columns={"code": "kod", "date": "tarih", "price": "fiyat"})
    df.to_csv(SNAP / "tefas_api_fiyatlar.csv", index=False)
    (SNAP / "tefas_api_meta.json").write_text(json.dumps({
        "source": "TEFAS arayüz API'si (fonFiyatBilgiGetir)", "url": m["source"], "fetched_at": m["fetched_at"],
        "toplayan": COLLECTOR, "yontem": "script ile toplu çekim (09.10.2026). Yeniden çalıştırılmaz; yalnızca çapraz kontrol için ikincil kaynak.",
        "fon_sayisi": m["n_funds_ok"], "tarih_araligi": [m["date_min"], m["date_max"]], "satir": m["rows"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.append(f"TEFAS API (ikincil kaynak): {m['rows']} satır, {m['n_funds_ok']} fon")


def kap_funds(src: Path, log: list):
    kap = J(src / "kap_fund_details_all.json")
    flags = {}
    for bdir in sorted(src.glob("_yedek_*")):  # eski işaretler önce, yenileri üzerine yazar
        if (bdir / "kap_fund_flags.json").exists():
            flags.update({f["code"]: f for f in J(bdir / "kap_fund_flags.json")})
    flags.update({f["code"]: f for f in J(src / "kap_fund_flags.json")})
    valor = J(src / "valor_observations.json")
    as_of = day(kap["fetched_at"])
    # Yeni çekimde alınamayan fonlar (örn. HTTP 429) için önceki çekimin yedeği kullanılır; tarihi yedeğin tarihidir.
    rows, fallback_codes = list(kap["rows"]), []
    ok_codes = {r["code"] for r in rows if r.get("status") == "ok"}
    for bdir in sorted(src.glob("_yedek_*"), reverse=True):
        if not (bdir / "kap_fund_details_all.json").exists():
            continue
        old = J(bdir / "kap_fund_details_all.json")
        for r in old["rows"]:
            if r.get("status") == "ok" and r["code"] not in ok_codes:
                rows.append({**r, "_as_of": day(old["fetched_at"]), "_fetched_at": old["fetched_at"]})
                ok_codes.add(r["code"])
                fallback_codes.append(r["code"])
    funds = {}
    for r in rows:
        if r.get("status") != "ok":
            continue
        rec_as_of = r.get("_as_of") or as_of
        rec = {"source": "KAP Fon Bilgileri sayfası", "url": r["url"], "as_of": rec_as_of,
               "fetched_at": r.get("_fetched_at") or kap["fetched_at"],
               "toplayan": COLLECTOR, "yontem": f"KAP fon sayfalarından script ile ({rec_as_of})"
                                                 + ("; yeni çekimde alınamadı, önceki çekim kullanıldı" if r.get("_as_of") else "")}
        cands = r.get("risk_candidates") or []
        if r.get("risk_value") is not None:
            rec["resmi_rd"] = int(max(cands)) if r.get("risk_ambiguous") and cands else int(r["risk_value"])
            if r.get("risk_ambiguous"):
                rec["rd_belirsiz"] = f"KAP'ta birden fazla RD adayı {cands}; muhafazakâr olan alındı"
        fee = r.get("mgmt_fee_applied_yearly_pct")
        if fee is not None:
            rec["yonetim_ucreti_yillik"] = fee / 100
        for src_k, dst_k in (("entry_commission_pct", "giris_komisyonu"), ("exit_commission_pct", "cikis_komisyonu")):
            if r.get(src_k) is not None:
                rec[dst_k] = r[src_k] / 100
        if r.get("umbrella_type"):
            rec["semsiye_turu"] = r["umbrella_type"]
        if r.get("isin"):
            rec["isin"] = r["isin"]
        if r.get("benchmark"):
            rec["karsilastirma_olcutu"] = ([b.strip() for b in r["benchmark"] if isinstance(b, str)]
                                           if isinstance(r["benchmark"], list) else r["benchmark"])
        fl = flags.get(r["code"])
        if fl is not None:
            rec["nitelikli_yatirimci"] = bool(fl["qualified_investor_text"])
            if not fl["qualified_investor_text"]:
                rec["nitelikli_notu"] = "KAP metninde 'nitelikli yatırımcı' ibaresi bulunamadı"
        sv = (r.get("sell_valor") or {}).get("days")
        bf = valor["by_fund"].get(r["code"])
        if sv is not None:
            rec["valor_satis_gun"] = sv
            rec["valor_kaynak"] = {"source": "KAP alım-satım esasları", "url": r["url"], "as_of": as_of,
                                   "fetched_at": r.get("_fetched_at") or kap["fetched_at"]}
        elif bf and bf.get("measured") and bf.get("conservative_sell_valor") is not None:
            rec["valor_satis_gun"] = bf["conservative_sell_valor"]
            rec["valor_kaynak"] = {"source": "TEFAS Valör Hesaplama Simülatörü (elle girilen ölçüm)",
                                   "url": "https://www.tefas.gov.tr", "as_of": "2026-10-10",
                                   "not": f"{bf['satis']['n']} ölçüm, en kötü durum alındı. {valor.get('note', '')}"}
        funds[r["code"]] = rec
    (SNAP / "kap_fon_bilgileri.json").write_text(json.dumps({
        "_aciklama": "KAP fon bilgileri (Gamze Akkurt, 10.10.2026). manuel_veriler.json > fon_ek_bilgi içindeki elle girilmiş kayıtlar bunların önüne geçer.",
        "fonlar": funds}, ensure_ascii=False, indent=1), encoding="utf-8")
    n = lambda k: sum(1 for f in funds.values() if f.get(k) is not None)
    log.append(f"KAP: {len(funds)} fon; resmi RD {n('resmi_rd')}, yönetim ücreti {n('yonetim_ucreti_yillik')}, "
               f"nitelikli bilgisi {n('nitelikli_yatirimci')}, satış valörü {n('valor_satis_gun')}"
               + (f"; {len(fallback_codes)} fon önceki çekimden: {', '.join(fallback_codes)}" if fallback_codes else ""))


def manual_entries(src: Path, log: list):
    path = SNAP / "manuel_veriler.json"
    raw = J(path)
    mine = lambda rec: isinstance(rec, dict) and rec.get("_aktaran") == TAG

    # Stopaj (gerçek kişi, 09.07.2025 sonrası alımlar)
    tax = J(src / "fund_tax_reference.json")
    table = tax["ing_table"]["rows"]
    last = tax["ing_table"]["periods"][-1]
    ing_url = tax["sources"]["ing"]
    rowmap = {
        "para_piyasasi": "Para Piyasası", "borclanma": "Borçlanma Araçları (Euroband,Yabancı hariç)",
        "fon_sepeti": "Fon Sepeti (Yabancı, Döviz fonları hariç)", "kiymetli_maden": "Kıymetli Madenler",
        "katilim": "Katılım (Döviz fonları hariç)", "serbest": "Serbest (Döviz fonlar dahil)",
        "karma_degisken": "Karma ve Değişken", "hisse": "Hisse Senedi Yoğun",
    }
    stopaj = [s for s in raw.get("stopaj", []) if not mine(s)]
    have = {s.get("kategori") for s in stopaj}
    for cat, row in rowmap.items():
        if cat in have or row not in table:
            continue
        stopaj.append({"_aktaran": TAG, "kategori": cat, "oran": table[row][last] / 100,
                       "source": f"ING yatırım fonlarında vergilendirme tablosu ('{row}', {last})", "url": ing_url,
                       "as_of": day(tax["fetched_at"]), "fetched_at": tax["fetched_at"],
                       "not": "Gerçek kişiler için; iktisap tarihi esas, FIFO. " + tax["note"]})
    dep = J(src / "ing_deposit_rates.json")
    tl = [r for r in dep["rows"] if r["currency"] == "TL" and r["status"] == "ok"]
    if "mevduat_tl" not in have and tl:
        r0 = tl[0]
        stopaj.append({"_aktaran": TAG, "kategori": "mevduat_tl", "oran": r0["withholding_rate_pct"] / 100,
                       "source": "ING mevduat hesaplayıcısı (stopaj satırı)", "url": r0["source"], "as_of": day(r0["fetched_at"]), "fetched_at": r0["fetched_at"],
                       "not": "Hesaplayıcının kendi stopaj hesabı; RM teyit etmeli."})
    raw["stopaj"] = stopaj
    log.append(f"Stopaj: {sum(1 for s in stopaj if mine(s))} kategori (ING tablosu + mevduat hesaplayıcısı); eurobond/yabancı fonlar için kaynakta oran yok")

    # Mevduat: ING, 1.000.000 TL kademesi
    mev = [m for m in raw.get("mevduat", []) if not mine(m)]
    tier = 1_000_000
    for r in sorted((r for r in tl if r["capital"] == tier), key=lambda r: r["day_count"]):
        mev.append({"_aktaran": TAG, "banka": "ING", "tur": "mevduat", "vade_gun": r["day_count"],
                    "oran": r["annual_gross_rate_pct"] / 100, "min_tutar": tier,
                    "source": "ING mevduat hesaplayıcısı", "url": r["source"], "as_of": day(r["fetched_at"]), "fetched_at": r["fetched_at"],
                    "not": f"{tier:,} TL kademesi (Private Banking için varsayım). {dep['note']}".replace(",", ".")})
    raw["mevduat"] = mev
    log.append(f"Mevduat: ING {sum(1 for m in mev if mine(m))} vade (1.000.000 TL kademesi)")

    # Göstergeler
    g = raw.setdefault("gostergeler", {})
    pol = J(src / "tcmb_policy_rate.json")
    if not g.get("politika_faizi") or mine(g.get("politika_faizi")):
        g["politika_faizi"] = {"_aktaran": TAG, "deger": pol["current_rate_pct"] / 100, "source": "TCMB 1 hafta repo faizi sayfası",
                               "url": pol["source"], "as_of": pol["current_since"], "fetched_at": pol["fetched_at"],
                               "not": f"Yürürlük tarihi {pol['current_since']}; sayfa {day(pol['fetched_at'])} tarihinde okundu."}
    tufe = J(src / "tcmb_tufe.json")
    last_row = next(r for r in tufe["rows"] if r["period"] == tufe["latest_period"])
    if not g.get("tufe_yillik") or mine(g.get("tufe_yillik")):
        g["tufe_yillik"] = {"_aktaran": TAG, "deger": last_row["yoy_pct"] / 100, "source": "TCMB tüketici fiyatları sayfası",
                            "url": tufe["source"], "as_of": tufe["latest_period"], "fetched_at": tufe.get("fetched_at")}
    log.append(f"Göstergeler: politika faizi %{pol['current_rate_pct']} ({pol['current_since']}), TÜFE %{last_row['yoy_pct']} ({tufe['latest_period']})")

    # Benchmark: BIST 100. Öncelik resmi EVDS getiri endeksi (temettü dahil, tools/fetch_history.py); yoksa Gamze'nin Yahoo serisi.
    evds_file = SNAP / "gecmis" / "bist100_getiri.csv"
    if evds_file.exists():
        b = pd.read_csv(evds_file, parse_dates=["tarih"]).rename(columns={"tarih": "date", "deger": "close"}).sort_values("date")
        hm = J(SNAP / "gecmis" / "meta.json").get("bist100_getiri", {})
        bsrc = (f"TCMB EVDS ({hm.get('seri_kodu', 'TP.MK.G.BILESIK')}): BIST 100 getiri endeksi (XU100-CFNNTLTL, temettü dahil)",
                hm.get("url", "https://evds3.tcmb.gov.tr"), "EVDS", hm.get("alinma"))
    else:
        bm = J(src / "bist100_meta.json")
        b = pd.read_csv(src / "bist100_daily.csv", parse_dates=["date"]).sort_values("date")
        bsrc = (f"{bm['source']}: resmi Borsa İstanbul kaynağı değil; {bm['kind']}", "https://finance.yahoo.com/quote/XU100.IS", "Yahoo Finance",
                bm.get("fetched_at"))
    end = b.iloc[-1]
    start = b[b["date"] <= end["date"] - pd.Timedelta(days=365)].iloc[-1]
    bench = [x for x in raw.get("benchmarklar", []) if not mine(x)]
    bench.append({"_aktaran": TAG, "ad": "BIST 100", "kategoriler": ["hisse"], "getiri_1y": float(end["close"] / start["close"] - 1),
                  "source": bsrc[0], "url": bsrc[1], "as_of": str(end["date"].date()), "fetched_at": bsrc[3]})
    raw["benchmarklar"] = bench
    log.append(f"Benchmark: BIST 100 1Y %{(end['close'] / start['close'] - 1) * 100:.1f} ({start['date'].date()} → {end['date'].date()}, {bsrc[2]})")

    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def stopaj_history(src: Path, log: list):
    """Fon stopajının dönemlere göre tarihçesi (ING vergilendirme tablosu, gerçek kişi). RL eğitiminde geçmiş
    senaryolara o dönemin oranını uygulamak için."""
    tax = J(src / "fund_tax_reference.json")
    rowmap = {
        "para_piyasasi": "Para Piyasası", "borclanma": "Borçlanma Araçları (Euroband,Yabancı hariç)",
        "fon_sepeti": "Fon Sepeti (Yabancı, Döviz fonları hariç)", "kiymetli_maden": "Kıymetli Madenler",
        "katilim": "Katılım (Döviz fonları hariç)", "serbest": "Serbest (Döviz fonlar dahil)",
        "karma_degisken": "Karma ve Değişken", "hisse": "Hisse Senedi Yoğun",
    }
    periods = []
    for p in tax["ing_table"]["periods"]:
        dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", p)
        to_iso = lambda s: datetime.strptime(s, "%d.%m.%Y").date().isoformat()
        if "öncesi" in p.lower():
            periods.append((p, None, to_iso(dates[0])))
        elif "sonrası" in p.lower():
            periods.append((p, to_iso(dates[0]), None))
        else:
            periods.append((p, to_iso(dates[0]), to_iso(dates[1])))
    out = {"source": "ING yatırım fonlarında vergilendirme tablosu (gerçek kişi)", "url": tax["sources"]["ing"],
           "as_of": day(tax["fetched_at"]), "toplayan": COLLECTOR,
           "not": "Oranlar fonun alım tarihine göre uygulanır. Eurobond/yabancı fonlar tabloda yok. " + tax["note"],
           "donemler": [{"ad": n, "baslangic": a, "bitis": b} for n, a, b in periods],
           "kategoriler": {cat: [tax["ing_table"]["rows"][row][n] / 100 for n, _, _ in periods]
                           for cat, row in rowmap.items() if row in tax["ing_table"]["rows"]}}
    (SNAP / "stopaj_tarihce.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log.append(f"Stopaj tarihçesi: {len(out['kategoriler'])} kategori × {len(periods)} dönem")


def ing_fund_list(src: Path, log: list):
    u = J(src / "fund_universe.json")
    codes = sorted({r["code"] for r in u})
    (SNAP / "ing_fon_listesi.json").write_text(json.dumps({
        "source": "ING fon fiyatları sayfası", "url": u[0]["source"], "as_of": day(u[0]["fetched_at"]), "toplayan": COLLECTOR,
        "not": "Bilgi amaçlı: fonun ING platformunda sunulup sunulmadığı. Skora girmez.", "kodlar": codes}, ensure_ascii=False, indent=1), encoding="utf-8")
    log.append(f"ING fon listesi: {len(codes)} fon (bilgi amaçlı)")


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    log: list[str] = []
    for step in (tefas_prices, tefas_api, kap_funds, manual_entries, stopaj_history, ing_fund_list):
        step(src, log)
    (SNAP / "gamze_import_log.json").write_text(json.dumps({"zaman": now(), "kaynak_klasor": str(src), "adimlar": log},
                                                           ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(log))


if __name__ == "__main__":
    main()
