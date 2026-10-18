"""docs/tefas.md #3 (opsiyonel, henüz kodda yoktu): Portföy dağılımı.

dagilimSiraliGetirT ucu varlık sınıflarını kısaltılmış kodlarla döndürüyor
(hs, tr, vmtl, dt, fb, ...) ve resmi bir alan sözlüğü yayınlanmamış. Aşağıdaki
KNOWN_FIELDS yalnızca TEFAS'ın "Fon Varlık Dağılımı" panelinde insan-okur
etiketleriyle görülüp (AAL fonu üzerinden, 15.10.2026) çapraz kontrol edilerek
doğrulanan alt kümedir. Eşlenmeyen kodlar ham haliyle bırakılır; rules.py/
universe.py bu veriyi henüz tüketmiyor (tefas.md: "ayrıca kod gerekiyor").
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import pandas as pd

from .endpoints import FON_TIPI_YAT, dagilim_sirali_page
from .pagination import paginate
from .session import TefasSession

KNOWN_FIELDS = {
    "hs": "Hisse Senedi (%)",
    "tr": "Ters Repo (%)",
    "vmtl": "Mevduat TL (%)",
    "tpp": "Takasbank Para Piyasası (%)",
    "dt": "Devlet Tahvili (%)",
    "fb": "Finansman Bonosu (%)",
    "khtl": "Katılma Hesabı TL (%)",
    "hb": "Hazine Bonosu (%)",
    "ost": "Özel Sektör Tahvili (%)",
    "vdm": "Varlığa Dayalı Menkul Kıymetler (%)",
    "osks": "Özel Sektör Kira Sertifikaları (%)",
    "vint": "Vadeli İşlemler Nakit Teminatları (%)",
}


def fetch_portfoy_dagilimi(
    session: TefasSession,
    fon_tipi: str = FON_TIPI_YAT,
    tarih: Optional[str] = None,
    fon_kodu: Optional[str] = None,
    page_size: int = 1000,
) -> pd.DataFrame:
    """tarih: 'YYYYMMDD'; verilmezse bugün (TEFAS bu uçta yalnızca güncel dağılımı tutuyor)."""
    tarih = tarih or dt.date.today().strftime("%Y%m%d")

    def fetch_page(bas: int, bit: int) -> dict:
        return dagilim_sirali_page(session, fon_tipi, tarih, tarih, bas, bit, fon_kodu)

    rows = paginate(fetch_page, page_size=page_size)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.rename(columns={"fonKodu": "Fon Kodu", "fonUnvan": "Fon Adı", "tarih": "Tarih"})
    df = df.rename(columns={k: v for k, v in KNOWN_FIELDS.items() if k in df.columns})
    df = df.drop(columns=[c for c in ("bilFiyat",) if c in df.columns])
    return df
