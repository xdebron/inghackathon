"""docs/tefas.md #1 (P0, zorunlu): Fon fiyat geçmişi.

Dönem: son 13 ay, Fon tipi: YAT. Gerekli sütunlar: Tarih, Fon Kodu, Fon Adı,
Fiyat, Tedavüldeki Pay Sayısı, Kişi Sayısı, Fon Toplam Değer — hepsi
fonGnlBlgSiraliGetir ucundan tek seferde geliyor (ayrıca fonFiyatBilgiGetir'e
gerek yok, o yalnızca fiyatı; pay/kişi/büyüklük bilgisini içermiyor).

TEFAS tarih aralığını 1 ayla sınırladığı için 13 aylık dönem ~1 aylık
parçalara bölünür; her parça da kendi içinde basSira/bitSira ile sayfalanır.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Callable, Optional

import pandas as pd

from .endpoints import FON_TIPI_YAT, fon_gnl_blg_sirali_page
from .pagination import paginate
from .session import TefasSession

COLUMNS = [
    "Tarih", "Fon Kodu", "Fon Adı", "Fiyat",
    "Tedavüldeki Pay Sayısı", "Kişi Sayısı", "Fon Toplam Değer",
]

_RENAME = {
    "tarih": "Tarih",
    "fonKodu": "Fon Kodu",
    "fonUnvan": "Fon Adı",
    "fiyat": "Fiyat",
    "tedPaySayisi": "Tedavüldeki Pay Sayısı",
    "kisiSayisi": "Kişi Sayısı",
    "portfoyBuyukluk": "Fon Toplam Değer",
}


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (dt.date(year, month + 1, 1) - dt.timedelta(days=1)).day


def _shift_months(d: dt.date, delta: int) -> dt.date:
    total = d.month - 1 + delta
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return dt.date(year, month, day)


def _subtract_months(d: dt.date, months: int) -> dt.date:
    return _shift_months(d, -months)


def _chunk_range(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """TEFAS'ın kuralı sabit bir gün sayısı değil, takvim ayı bazlı: bitTarih,
    basTarih + 1 takvim ayını aşamaz (örn. 12 Şubat -> en geç 12 Mart; 28/29/30/31
    günlük aylar arasında geçişte sabit 30 günlük pencere bazen bu sınırı aşıyor).
    Her pencereyi (basTarih + 1 ay - 1 gün) olarak kurup bu kurala tam oturtuyoruz.
    """
    windows = []
    cursor = start
    while cursor <= end:
        window_end = min(_shift_months(cursor, 1) - dt.timedelta(days=1), end)
        windows.append((cursor, window_end))
        cursor = window_end + dt.timedelta(days=1)
    return windows


def fetch_tarihsel(
    session: TefasSession,
    months: int = 13,
    fon_tipi: str = FON_TIPI_YAT,
    fon_kodu: Optional[str] = None,
    page_size: int = 2000,
    end_date: Optional[dt.date] = None,
    start_date: Optional[dt.date] = None,
    on_progress: Optional[Callable[..., None]] = None,
) -> pd.DataFrame:
    """Son `months` ay için günlük fon verilerini çeker ve tefas.md #1 sütunlarıyla döner.

    `start_date` verilirse `months` yok sayılır; belirli bir boşluğu (ör. başarısız
    kalan bir pencereyi) yeniden çekmek için kullanılır.
    """
    end = end_date or dt.date.today()
    start = start_date or _subtract_months(end, months)
    windows = _chunk_range(start, end)

    rows: list[dict] = []
    failed_windows: list[tuple[str, str]] = []
    for i, (w_start, w_end) in enumerate(windows, 1):
        bas_tarih = w_start.strftime("%Y%m%d")
        bit_tarih = w_end.strftime("%Y%m%d")

        def fetch_page(bas: int, bit: int, _bas_tarih: str = bas_tarih, _bit_tarih: str = bit_tarih) -> dict:
            return fon_gnl_blg_sirali_page(session, fon_tipi, _bas_tarih, _bit_tarih, bas, bit, fon_kodu)

        window_rows: list[dict] = []
        window_max_attempts = 3
        for window_attempt in range(1, window_max_attempts + 1):  # bir pencere tükenirse tüm işi kaybetmeyelim
            try:
                window_rows = paginate(fetch_page, page_size=page_size)
                break
            except Exception as exc:
                if on_progress:
                    on_progress("tarihsel-hata", i, len(windows), w_start.isoformat(), w_end.isoformat(),
                                window_attempt, str(exc))
                if window_attempt < window_max_attempts:
                    session.reconnect()
                    time.sleep(60)
        else:
            failed_windows.append((bas_tarih, bit_tarih))

        rows.extend(window_rows)
        if on_progress:
            on_progress("tarihsel", i, len(windows), w_start.isoformat(), w_end.isoformat(), len(window_rows))
        if i < len(windows):
            time.sleep(4)  # pencereler arası nefes payı

    if failed_windows and on_progress:
        on_progress("tarihsel-eksik-pencereler", failed_windows)

    if not rows:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(rows)
    df = df.rename(columns=_RENAME)
    df = df[[c for c in COLUMNS if c in df.columns]]
    df = df.drop_duplicates(subset=["Tarih", "Fon Kodu"])
    # Sıralama/karşılaştırma için ISO (YYYY-MM-DD) tutulur; yalnızca dışa aktarırken
    # DD.MM.YYYY'ye çevrilir (bkz. export.write_tarihsel_csv) çünkü backend'in
    # tefas_import.parse_export()'u pd.to_datetime(..., dayfirst=True) kullanıyor ve
    # bu, ISO tarihleri (gün<=12 olan günlerde) sessizce ay/gün'ü takas ediyor ya da
    # (gün>12 olanlarda) tamamen NaT'a düşürüp satırı siliyor.
    df = df.sort_values(["Fon Kodu", "Tarih"]).reset_index(drop=True)
    return df


def to_dayfirst_display(df: pd.DataFrame) -> pd.DataFrame:
    """'Tarih' sütununu ISO'dan DD.MM.YYYY'ye çevirir (bkz. fetch_tarihsel notu)."""
    if "Tarih" not in df.columns or df.empty:
        return df
    df = df.copy()
    df["Tarih"] = pd.to_datetime(df["Tarih"]).dt.strftime("%d.%m.%Y")
    return df
