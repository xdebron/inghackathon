"""EVDS, TEFAS karşılaştırma, çapraz kontroller, reel getiri, benchmark, katılım hesabı ve TMSF testleri."""
from __future__ import annotations

import pytest

from app import universe
from app.models import DataPoint
from app.sources import evds, tefas_import

REC = {"source": "Test kaynağı", "url": "https://example.test", "as_of": "2026-10-14"}


def ok(key, value):
    return DataPoint(key=key, value=value, source="Test", as_of="2026-10-14", status="ok")


# --- EVDS ---

def test_evds_parse_latest_skips_nulls_and_formula_suffix():
    payload = {"items": [{"Tarih": "2026-08", "TP_FG_J0-3": "33.5"}, {"Tarih": "2026-09", "TP_FG_J0-3": None}]}
    assert evds.parse_latest(payload, "TP.FG.J0") == (33.5, "2026-08")


def test_evds_without_key_is_missing(monkeypatch, tmp_db):
    monkeypatch.delenv("EVDS_API_KEY", raising=False)
    dp = evds.fetch("tufe_yillik")
    assert dp.status == "missing" and "EVDS_API_KEY" in dp.note


def test_evds_without_key_uses_last_saved_value_as_stale(monkeypatch, tmp_db):
    from app.db import store
    monkeypatch.delenv("EVDS_API_KEY", raising=False)
    store.put_indicator("evds:tufe_yillik", 0.30, "oran", "TCMB EVDS (TP.GENENDEKS.T1)", "https://evds3.tcmb.gov.tr",
                        "2026-9", "2026-10-01T10:00:00+03:00", None)
    dp = evds.fetch("tufe_yillik")
    assert dp.status == "stale" and dp.value == 0.30 and dp.fetched_at.startswith("2026-10-01")


def test_indicator_source_priority(monkeypatch):
    monkeypatch.setattr(evds, "fetch", lambda ind: DataPoint(key=ind, source="EVDS", status="missing", note="yok"))
    raw = {"gostergeler": {"tufe_yillik": {"deger": 0.33, **REC}}}
    ind = universe.indicators(raw, set())
    assert ind["tufe_yillik"].value == 0.33 and ind["tufe_yillik"].source == "Test kaynağı"
    monkeypatch.setattr(evds, "fetch", lambda ind: DataPoint(key=ind, value=0.30, source="EVDS", status="ok"))
    assert universe.indicators(raw, set())["tufe_yillik"].source == "EVDS"
    # EVDS kesintisi simülasyonunda manuel değer kullanılır
    assert universe.indicators(raw, {"evds"})["tufe_yillik"].value == 0.33


def test_manual_indicator_without_provenance_is_not_used(monkeypatch):
    monkeypatch.setattr(evds, "fetch", lambda ind: DataPoint(key=ind, source="EVDS", status="missing"))
    ind = universe.indicators({"gostergeler": {"tufe_yillik": {"deger": 0.33}}}, set())
    assert ind["tufe_yillik"].status == "missing"


# --- TEFAS karşılaştırma ---

CMP = ("Fon Kodu;Fon Adı;Şemsiye Fon Türü;1 Ay (%);3 Ay (%);6 Ay (%);Yılbaşı (%);1 Yıl (%);3 Yıl (%);5 Yıl (%)\n"
       "abc;ABC PORTFÖY PARA PİYASASI FONU;Para Piyasası Şemsiye Fonu;3,50;10,20;21,00;35,00;46,52;;\n"
       "xyz;XYZ PORTFÖY BİRİNCİ FON;Hisse Senedi Şemsiye Fonu;1,00;2,00;3,00;4,00;50,00;;\n").encode("utf-8")


def test_comparison_parse_and_detect():
    assert tefas_import.detect_kind(CMP, "k.csv") == "karsilastirma"
    df = tefas_import.parse_comparison(CMP, "k.csv").set_index("kod")
    assert abs(df.loc["ABC", "getiri_1y"] - 0.4652) < 1e-9
    assert abs(df.loc["ABC", "getiri_1a"] - 0.035) < 1e-9


def test_history_file_detected_as_tarihsel():
    data = "Tarih;Fon Kodu;Fon Adı;Fiyat\n01.10.2026;ABC;ABC FONU;1,2\n".encode("utf-8")
    assert tefas_import.detect_kind(data, "t.csv") == "tarihsel"


def _metrics(r1y):
    return {"ad": "ABC PORTFÖY PARA PİYASASI FONU", "gozlem": 300, "ilk_tarih": "2025-09-01", "son_tarih": "2099-01-01",
            "son_fiyat": 1.0, "buyukluk": 1e9, "kisi": 1e4, "kalite_sorunlari": [], "getiri_1a": 0.035, "getiri_3a": 0.10,
            "getiri_6a": 0.2, "getiri_1y": r1y, "volatilite_yillik": 0.004, "max_dusus": -0.001}


def _cmp(r1y, tur="Para Piyasası Şemsiye Fonu", ad="ABC PORTFÖY PARA PİYASASI FONU"):
    return {"kod": "ABC", "ad": ad, "tur": tur, "getiri_1y": r1y, "getiri_3a": None, "getiri_1a": None, "as_of": "2026-10-14"}


CMP_META = {"iceri_alma_zamani": "2026-10-15T10:00:00+03:00", "as_of": "2026-10-14"}


def test_cross_check_conflict_between_tefas_sources():
    flags = []
    p = universe._fund_products({"ABC": _metrics(0.46)}, {"imports": []}, {"ABC": _cmp(0.50)}, CMP_META, {}, {}, [], flags)[0]
    assert p.fields["getiri_1y"].status == "conflict"
    assert p.fields["getiri_1y_karsilastirma"].value == 0.50
    assert any("iki TEFAS kaynağında" in f.message for f in flags)


def test_cross_check_within_tolerance_ok():
    p = universe._fund_products({"ABC": _metrics(0.46)}, {"imports": []}, {"ABC": _cmp(0.465)}, CMP_META, {}, {}, [], [])[0]
    assert p.fields["getiri_1y"].status == "ok"


def test_comparison_only_fund_uses_comparison_return_and_type():
    p = universe._fund_products({}, {"imports": []}, {"ABC": _cmp(0.5, tur="Hisse Senedi Şemsiye Fonu", ad="ABC PORTFÖY BİRİNCİ FON")},
                                CMP_META, {}, {}, [], [])[0]
    assert p.category == "hisse"
    assert p.fields["getiri_1y"].value == 0.5 and "karşılaştırma" in p.fields["getiri_1y"].note
    assert p.fields["rd"].status == "missing"  # fiyat geçmişi ve resmi RD yok -> risk bilinmiyor


def test_benchmark_difference():
    bench = [{"ad": "BIST 100", "kategoriler": ["para_piyasasi"], "getiri_1y": 0.40, **REC}]
    p = universe._fund_products({"ABC": _metrics(0.46)}, {"imports": []}, {}, None, {}, {}, bench, [])[0]
    assert abs(p.fields["benchmark_farki_1y"].value - 0.06) < 1e-9


def test_spk_warning_note_becomes_flag():
    flags = []
    raw = {"fon_ek_bilgi": {"ABC": {"uyari_notu": "Yönetim ücreti artışı duyuruldu", **REC}}}
    universe._fund_products({"ABC": _metrics(0.46)}, {"imports": []}, {}, None, raw, {}, [], flags)
    assert any("Yönetim ücreti artışı" in f.message for f in flags)


# --- Mevduat / katılım ---

def test_deposit_sector_outlier_flag_and_katilim_category():
    raw = {"mevduat": [{"banka": "A", "vade_gun": 32, "oran": 0.30, **REC},
                       {"banka": "B", "tur": "katilim", "vade_gun": 32, "oran": 0.38, **REC}]}
    ind = {"mevduat_sektor_ortalamasi_1a": ok("mevduat_sektor_ortalamasi_1a", 0.44),
           "tmsf_sigorta_limiti_tl": ok("tmsf_sigorta_limiti_tl", 950000)}
    flags = []
    dep, kat = universe._deposit_products(raw, {}, ind, flags)
    assert abs(dep.fields["sektor_farki"].value - (-0.14)) < 1e-9
    assert any("sektör ortalamasından" in f.message for f in flags)
    assert kat.category == "katilim_hesabi" and kat.product_id.startswith("KATILIM:")
    assert dep.fields["tmsf_limiti"].value == 950000


def test_real_return():
    p = universe._fund_products({"ABC": _metrics(0.46)}, {"imports": []}, {}, None, {}, {}, [], [])[0]
    universe._add_real_return(p, ok("tufe", 0.33))
    assert abs(p.fields["reel_getiri_1y"].value - (1.46 / 1.33 - 1)) < 1e-9
    universe._add_real_return(p, None)
    assert p.fields["reel_getiri_1y"].value is None
