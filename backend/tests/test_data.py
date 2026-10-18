"""Veri katmanı testleri: TEFAS dışa aktarım ayrıştırma, metrikler, kategori ve RD tahmini, TCMB ayrıştırma."""
from __future__ import annotations

import pandas as pd

from app import universe
from app.sources import tcmb, tefas_import


def _csv(rows):
    head = "Tarih;Fon Kodu;Fon Adı;Fiyat;Tedavüldeki Pay Sayısı;Kişi Sayısı;Fon Toplam Değer\n"
    return (head + "\n".join(";".join(map(str, r)) for r in rows)).encode("utf-8")


def test_parse_turkish_csv_with_comma_decimals():
    data = _csv([["01.10.2026", "abc", "ABC PARA PİYASASI FONU", "1,234567", "1.000", "500", "100.000.000,50"]])
    df = tefas_import.parse_export(data, "x.csv")
    row = df.iloc[0]
    assert row["kod"] == "ABC" and abs(row["fiyat"] - 1.234567) < 1e-9 and row["buyukluk"] == 100000000.5


def test_parse_rejects_missing_columns():
    import pytest
    with pytest.raises(ValueError):
        tefas_import.parse_export(b"Tarih;Ad\n01.10.2026;x", "x.csv")


def test_metrics_return_vol_and_jump_detection():
    dates = pd.bdate_range("2025-09-01", "2026-10-14")
    prices = [1.0 * (1.0015 ** i) for i in range(len(dates))]
    prices[200] = prices[199] * 1.5  # yapay sıçrama
    df = pd.DataFrame({"tarih": dates, "kod": "ABC", "ad": "ABC BORÇLANMA ARAÇLARI FONU", "fiyat": prices,
                       "pay": None, "kisi": None, "buyukluk": None})
    m = tefas_import.compute_metrics(df)["ABC"]
    assert m["getiri_1y"] is not None and m["getiri_1y"] > 0
    assert m["volatilite_yillik"] is not None
    assert m["kalite_sorunlari"]


def test_short_history_has_no_1y_return():
    dates = pd.bdate_range("2026-08-01", "2026-10-14")
    df = pd.DataFrame({"tarih": dates, "kod": "NEW", "ad": "NEW HİSSE SENEDİ FONU", "fiyat": range(1, len(dates) + 1),
                       "pay": None, "kisi": None, "buyukluk": None})
    m = tefas_import.compute_metrics(df)["NEW"]
    assert m["getiri_1y"] is None


def test_category_detection_order():
    assert universe.detect_category("XYZ PORTFÖY PARA PİYASASI (TL) FONU") == "para_piyasasi"
    assert universe.detect_category("XYZ PORTFÖY ALTIN FON SEPETİ FONU") == "kiymetli_maden"  # altın maruziyeti
    assert universe.detect_category("XYZ PORTFÖY SERBEST (DÖVİZ) FON") == "serbest"
    assert universe.detect_category("XYZ PORTFÖY EUROBOND BORÇLANMA ARAÇLARI FONU") == "eurobond_doviz"
    assert universe.detect_category("XYZ PORTFÖY BİRİNCİ FON") is None
    assert universe.detect_category("AK PORTFÖY AMERİKA YABANCI HİSSE SENEDİ FONU") == "yabanci_hisse"


def test_rd_estimate_bands():
    # SPK rehberi (25.08.2025) bantları: 2 / 5 / 10 / 15 / 20 / 30
    assert universe.estimate_rd(0.003) == 1
    assert universe.estimate_rd(0.03) == 2
    assert universe.estimate_rd(0.12) == 4
    assert universe.estimate_rd(0.30) == 7


def test_tcmb_parse():
    xml = """<?xml version="1.0"?><Tarih_Date Tarih="14.10.2026" Date="10/14/2026">
    <Currency CurrencyCode="USD"><ForexSelling>41.5</ForexSelling></Currency>
    <Currency CurrencyCode="EUR"><ForexSelling></ForexSelling></Currency></Tarih_Date>"""
    pts = {p.key: p for p in tcmb.parse(xml, "now")}
    assert pts["kur.USDTRY"].value == 41.5 and pts["kur.USDTRY"].as_of == "14.10.2026"
    assert pts["kur.EURTRY"].status == "missing"


def test_old_zero_prices_ignored_recent_ones_flagged():
    dates = pd.bdate_range("2024-01-01", "2026-10-14")
    prices = [1.0 + i * 0.001 for i in range(len(dates))]
    prices[10] = 0.0  # 2024: metrik penceresi dışında
    df = pd.DataFrame({"tarih": dates, "kod": "ABC", "ad": "ABC PARA PİYASASI FONU", "fiyat": prices,
                       "pay": None, "kisi": None, "buyukluk": None})
    m = tefas_import.compute_metrics(df)["ABC"]
    assert m["kalite_sorunlari"] == [] and m["sifir_fiyat_gun_toplam"] == 1
    prices[-5] = 0.0
    m2 = tefas_import.compute_metrics(df.assign(fiyat=prices))["ABC"]
    assert any("0 fiyat" in s for s in m2["kalite_sorunlari"])
