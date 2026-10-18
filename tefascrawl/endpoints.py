"""TEFAS iç API uçlarının ince sarmalayıcıları.

15.10.2026'da https://www.tefas.gov.tr üzerinde tarayıcıdan ağ trafiği izlenerek
doğrulandı: istek gövdeleri sayfanın kendi derlenmiş JS'inden (webpack fetch
çağrıları) ve DevTools ağ kaydından birebir kopyalandı.
"""
from __future__ import annotations

from typing import Optional

from .session import TefasSession

FON_TIPI_YAT = "YAT"
FON_TIPI_EMK = "EMK"
FON_TIPI_BYF = "BYF"


def fon_getiri_bazli(session: TefasSession, fon_tipi: str = FON_TIPI_YAT) -> list[dict]:
    """tefas.md #2: Tüm fonların dönemsel getirileri, tek çağrıda (sayfalama yok).

    Kaynak: /tr/fon-getirileri sayfası -> POST /api/funds/fonGetiriBazliBilgiGetir
    """
    body = {
        "dil": "TR",
        "fonTipi": fon_tipi,
        "kurucuKodu": None,
        "sfonTurKod": None,
        "fonTurAciklama": None,
        "islem": 1,
        "fonTurKod": None,
        "fonGrubu": None,
        "donemGetiri1a": "1",
        "donemGetiri3a": "1",
        "donemGetiri6a": "1",
        "donemGetiri1y": "1",
        "donemGetiriyb": "1",
        "donemGetiri3y": "1",
        "donemGetiri5y": "1",
        "basTarih": None,
        "bitTarih": None,
        "calismaTipi": 2,
        "getiriOrani": "1",
    }
    data = session.post_json("fonGetiriBazliBilgiGetir", body)
    return data.get("resultList") or []


def fon_gnl_blg_sirali_page(session: TefasSession, fon_tipi: str, bas_tarih: str, bit_tarih: str,
                             bas_sira: int, bit_sira: int, fon_kodu: Optional[str] = None) -> dict:
    """tefas.md #1: Fon bazlı günlük fiyat + pay sayısı + kişi sayısı + fon toplam değeri.

    Eski `TarihselVeriler.aspx` / `BindHistoryInfo`'nun yeni site karşılığı.
    Kaynak: /tr/fon-verileri sayfası -> POST /api/funds/fonGnlBlgSiraliGetir

    bas_tarih/bit_tarih: 'YYYYMMDD'. TEFAS tarafında tarih aralığı 1 ayı aşamaz
    (sunucu "Geçersiz veri: Tarih aralığı 1 ayı aşamaz" hatası döner).
    basSira/bitSira: 1-tabanlı, (fon x gün) birleşik kümesi üzerinde satır aralığı.
    """
    body = {
        "fonTipi": fon_tipi,
        "fonKodu": fon_kodu,
        "aramaMetni": None,
        "fonTurKod": None,
        "fonGrubu": None,
        "sfonTurKod": None,
        "basTarih": bas_tarih,
        "bitTarih": bit_tarih,
        "basSira": bas_sira,
        "bitSira": bit_sira,
        "fonTurAciklama": None,
        "dil": "TR",
        "kurucuKod": None,
        "sira": None,
        "yon": None,
    }
    return session.post_json("fonGnlBlgSiraliGetir", body)


def dagilim_sirali_page(session: TefasSession, fon_tipi: str, bas_tarih: str, bit_tarih: str,
                         bas_sira: int, bit_sira: int, fon_kodu: Optional[str] = None) -> dict:
    """tefas.md #3 (opsiyonel): Fon varlık dağılımı yüzdeleri, ham (kısaltılmış) alan kodlarıyla.

    Kaynak: /tr/fon-verileri?view=portfolioDistribution -> POST /api/funds/dagilimSiraliGetirT
    Aynı basSira/bitSira + basTarih/bitTarih (<=1 ay) sözleşmesini kullanır.
    """
    body = {
        "fonTipi": fon_tipi,
        "fonKodu": fon_kodu,
        "aramaMetni": None,
        "fonTurKod": None,
        "fonGrubu": None,
        "sfonTurKod": None,
        "basTarih": bas_tarih,
        "bitTarih": bit_tarih,
        "basSira": bas_sira,
        "bitSira": bit_sira,
        "fonTurAciklama": None,
        "dil": "TR",
        "kurucuKod": None,
        "sFonTurKod": "",
        "fonKod": None,
        "fonGrup": "",
        "fonUnvanTip": "",
    }
    return session.post_json("dagilimSiraliGetirT", body)
