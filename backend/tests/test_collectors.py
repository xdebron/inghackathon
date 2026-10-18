"""Toplayıcı ayrıştırıcıları ve veri tabanı katmanı (ağ isteği yok; küçük sentetik HTML/JSON örnekleriyle).
Değer uydurulmaz: kaynakta olmayan alan boş kalmalı; her yazım bir çekim kaydı bırakmalı."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from app.collectors import hazine_dibs, ing_fonlar, ing_mevduat, ing_stopaj, kap_fon, tcmb_politika
from app.db import models as M, store


# --- KAP ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("3", (3, [3])),
    ("Fonun risk değeri 2 olarak belirlenmiştir.", (2, [2])),
    ("TL pay grubu 6, USD pay grubu 3", (6, [3, 6])),     # birden çok değer: tutucu (en yüksek)
    ("", (None, [])),
])
def test_kap_risk_value(raw, expected):
    assert kap_fon.risk_info(raw) == expected


@pytest.mark.parametrize("text,days", [
    ("Satım talimatları izleyen ikinci iş günü gerçekleştirilir.", 2),
    ("Fon paylarının satımında T+1 valör uygulanır.", 1),
    ("Satım talimatının verildiği aynı gün ödenir.", 0),
    ("Alım talimatları ertesi iş günü gerçekleştirilir.", None),   # yalnızca alım cümlesi: satım valörü bilinmiyor
])
def test_kap_sell_valor_only_from_explicit_text(text, days):
    assert kap_fon.valor_from_text(text, "sell")[0] == days


def test_kap_record_keeps_previous_valor_when_page_is_silent():
    detail = [{"itemKey": "kpy81_acc1_amac_strateji", "value": [{"riskDegeri": "1"}]},
              {"itemKey": "kpy81_acc2_fon_ucret_kom_info", "value": [{"uygulananYonetimUcretiOranYillikYuzde": "0,49"}]},
              {"itemKey": "kpy81_acc1_temel_alim_satim_info", "value": [{"alimSatimSaatleri": "Saat 13.30'a kadar."}]}]
    prev = M.FonBilgi(kod="BGP", valor_satis_gun=0, valor_kaynak={"source": "TEFAS Valör Hesaplama Simülatörü (elle girilen ölçüm)"})
    rec = kap_fon.to_record(detail, "https://www.kap.org.tr/tr/fon-bilgileri/genel/bgp-x", "2026-10-18T10:00:00+03:00", prev)
    assert rec["resmi_rd"] == 1 and abs(rec["yonetim_ucreti_yillik"] - 0.0049) < 1e-12
    assert rec["valor_satis_gun"] == 0 and rec["valor_kaynak"]["source"].startswith("TEFAS Valör")
    assert rec["nitelikli_yatirimci"] is False and rec["giris_komisyonu"] is None   # sayfada yok: boş
    assert rec["kaynak"] == kap_fon.SOURCE and rec["fetched_at"].startswith("2026-10-18")


def test_kap_fund_detail_from_next_payload():
    payload = json.dumps('"fundDetail":' + json.dumps([{"itemKey": "kpy81_acc1_ISIN", "value": "TRYXXX"}]))
    assert kap_fon.fund_detail(f"self.__next_f.push([1,{payload}])") == [{"itemKey": "kpy81_acc1_ISIN", "value": "TRYXXX"}]


# --- ING / TCMB / Hazine ------------------------------------------------------------------------------------------

def test_ing_deposit_response():
    payload = {"d": json.dumps([{"interestRate": "35,50", "grossInterest": "1.000,00", "incomeTax": "175,00"}])}
    assert ing_mevduat.parse_response(payload) == {"rate_pct": 35.5, "withholding_pct": 17.5}
    assert ing_mevduat.parse_response({"d": "[]"}) is None


def test_ing_stopaj_table_and_periods():
    html = """<table><tr><th>Fon Türü</th><th>23.12.2020 öncesi</th><th>09.07.2025 ve sonrası</th></tr>
              <tr><td>Para Piyasası</td><td>%10</td><td>%17,5</td></tr>
              <tr><td>Hisse Senedi Yoğun</td><td>%0</td><td>-</td></tr></table>"""
    periods, rows = ing_stopaj.parse_table(html)
    assert periods[-1] == "09.07.2025 ve sonrası"
    assert rows["Para Piyasası"]["09.07.2025 ve sonrası"] == 0.175
    assert rows["Hisse Senedi Yoğun"]["09.07.2025 ve sonrası"] is None          # okunamayan hücre uydurulmaz
    assert ing_stopaj._period_bounds("23.12.2020 öncesi") == (None, "2020-12-23")
    assert ing_stopaj._period_bounds("09.07.2025 ve sonrası") == ("2025-07-09", None)


def test_ing_fund_codes_and_name_matching():
    html = """<table><tr><td>Yatırım Fonları</td></tr><tr><td>BGP - AK PORTFÖY ÜÇÜNCÜ PARA PİYASASI FONU</td></tr>
              <tr><td>xx - geçersiz</td></tr></table>"""
    assert ing_fonlar.parse_codes(html) == ["BGP"]
    assert ing_fonlar.name_key("Ak Portföy Üçüncü Para Piyasası Fonu - TL") == ing_fonlar.name_key("AK PORTFÖY ÜÇÜNCÜ PARA PİYASASI FONU")


def test_tcmb_policy_table():
    html = """<table id="midTable"><tr><th>Tarih</th><th>Borç alma</th><th>Borç verme</th></tr>
              <tr><td>23.01.2026</td><td>-</td><td>37,00</td></tr><tr><td>12.12.2025</td><td>-</td><td>38,00</td></tr></table>"""
    rows = tcmb_politika.parse_repo_table(html)
    assert [r["effective_date"] for r in rows] == ["2025-12-12", "2026-01-23"] and rows[-1]["lending_rate_pct"] == 37.0


def test_hazine_auction_page():
    text = ("İhale No : 2475\nISIN Kodu : TRT091122T10\nSenet Tanımı : Sabit Kuponlu Devlet Tahvili Ortalama\n"
            "İhale Tarihi : 04.01.2021\nİhraç (Valör) Tarihi : 06.01.2021\nVade Tarihi : 09.11.2022\n"
            "Ortalama Yıllık Bileşik : 15,10 15,04\n")
    rec = hazine_dibs.parse_page(text)
    assert rec["isin"] == "TRT091122T10" and rec["tur"] == "tl_sabit_kuponlu" and rec["bilesik_faiz"] == 15.04
    assert rec["vade_yil"] == pytest.approx(1.84, abs=0.01)


# --- Veri tabanı katmanı -------------------------------------------------------------------------------------------

def test_run_logs_success_and_failure(tmp_db):
    with store.Run("deneme") as run:
        store.replace_rows(M.Gosterge, [{"kod": "x", "deger": 1.0, "kaynak": "K", "url": "u", "as_of": "2026-10-01",
                                         "fetched_at": "2026-10-02T00:00:00+03:00"}], run=run)
    with pytest.raises(ValueError):
        with store.Run("deneme"):
            raise ValueError("kaynak kapalı")
    runs = store.runs()
    assert runs[0]["durum"] == "hata" and "kaynak kapalı" in runs[0]["mesaj"]
    assert runs[1]["durum"] == "basarili" and runs[1]["satir"] == 1


def test_manual_raw_shape_and_namespaced_indicators_hidden(tmp_db):
    store.put_indicator("politika_faizi", 0.37, "oran", "TCMB", "u", "2026-01-23", "2026-10-18T10:00:00+03:00", None)
    store.put_indicator("evds:tufe_yillik", 0.30, "oran", "EVDS", "u", "2026-9", "2026-10-18T10:00:00+03:00", None)
    raw = store.manual_raw()
    assert set(raw["gostergeler"]) == {"politika_faizi"}
    g = raw["gostergeler"]["politika_faizi"]
    assert g["deger"] == 0.37 and g["source"] == "TCMB" and g["fetched_at"].startswith("2026-10-18")


def test_series_roundtrip_keeps_columns(tmp_db):
    df = pd.DataFrame({"tarih": ["2026-10-01", "2026-10-02"], "kapanis": [10.0, 11.0], "duzeltilmis_kapanis": [9.5, None]})
    store.save_series("bist/TEST", "bist", df, {"kaynak": "Yahoo"})
    back = store.series("bist/TEST")
    assert list(back["tarih"]) == ["2026-10-01", "2026-10-02"] and back["kapanis"].tolist() == [10.0, 11.0]
    assert pd.isna(back["duzeltilmis_kapanis"].iloc[1])
    assert store.series_meta("bist") == {"TEST": {"kaynak": "Yahoo"}}


def test_kap_refreshes_stalest_first_across_time_zones(tmp_db, monkeypatch):
    """Zaman damgaları farklı saat dilimlerinde olsa da (+03:00 ve UTC) en eski kayıt önce, yenisi hiç seçilmez."""
    from app.sources import tefas_import
    monkeypatch.setattr(tefas_import, "price_names", lambda: {})
    store.replace_rows(M.FonBilgi, [
        {"kod": "AAA", "kaynak": "K", "fetched_at": "2026-10-18T19:00:00+03:00"},   # 16:00 UTC
        {"kod": "BBB", "kaynak": "K", "fetched_at": "2026-10-18T17:00:00+00:00"},   # 17:00 UTC
        {"kod": "CCC", "kaynak": "K", "fetched_at": "2026-10-18T08:00:00+00:00"},   # 08:00 UTC: en eski
        {"kod": "DDD", "kaynak": "K", "fetched_at": "2099-01-01T00:00:00+00:00"},   # taze: seçilmemeli
    ])
    res = kap_fon.run(limit=10, max_age_days=0, log=lambda m: None)        # adres yok: istek atılmaz, sıra 'alinamayan'da görünür
    assert [x.split(":")[0] for x in res["alinamayan"]] == ["CCC", "AAA", "BBB"]
