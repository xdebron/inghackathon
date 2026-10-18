"""docs/tefas.md #2 (güçlü öneri, ikinci kaynak): Fon getirileri.

Tüm fonların dönemsel getirileri tek çağrıda (fonGetiriBazliBilgiGetir).
Fiyat geçmişinden hesaplanan getiriyle çapraz kontrol ve resmi fon türü
sınıflaması için kullanılır.
"""
from __future__ import annotations

import pandas as pd

from .endpoints import FON_TIPI_YAT, fon_getiri_bazli
from .session import TefasSession

COLUMNS = [
    "Fon Kodu", "Fon Adı", "Şemsiye Fon Türü",
    "1 Ay (%)", "3 Ay (%)", "6 Ay (%)", "Yılbaşı (%)", "1 Yıl (%)", "3 Yıl (%)", "5 Yıl (%)",
]

# "Fonun Risk Değeri" tefas.md'de istenen bir sütun değil (mevcut importer bu sütunu
# yok sayar); ama TEFAS ücretsiz verdiği için ekstra fayda olarak dosyaya ekleniyor.
EXTRA_COLUMNS = ["Fonun Risk Değeri"]

_RENAME = {
    "fonKodu": "Fon Kodu",
    "fonUnvan": "Fon Adı",
    "fonTurAciklama": "Şemsiye Fon Türü",
    "getiri1a": "1 Ay (%)",
    "getiri3a": "3 Ay (%)",
    "getiri6a": "6 Ay (%)",
    "getiriyb": "Yılbaşı (%)",
    "getiri1y": "1 Yıl (%)",
    "getiri3y": "3 Yıl (%)",
    "getiri5y": "5 Yıl (%)",
    "riskDegeri": "Fonun Risk Değeri",
}


def fetch_getiriler(session: TefasSession, fon_tipi: str = FON_TIPI_YAT) -> pd.DataFrame:
    rows = fon_getiri_bazli(session, fon_tipi)
    all_cols = COLUMNS + EXTRA_COLUMNS
    if not rows:
        return pd.DataFrame(columns=all_cols)
    df = pd.DataFrame(rows)
    df = df.rename(columns=_RENAME)
    return df[[c for c in all_cols if c in df.columns]]
