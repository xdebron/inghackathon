"""Çekilen veriyi, backend/app/sources/tefas_import.py'nin beklediği sütun adlarıyla
birebir eşleşen CSV dosyalarına yazar (TEFAS'ın kendi "Excel'e Aktar" çıktısının
taklidi) — böylece hem elle yükleme hem de doğrudan içe aktarım için kullanılabilir.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import pandas as pd

from . import tarihsel as _tarihsel_mod
from . import getiriler as _getiriler_mod

PathLike = Union[str, Path]


def write_tarihsel_csv(df: pd.DataFrame, path: PathLike) -> Path:
    """'Tarih' sütununu DD.MM.YYYY'ye çevirip yazar — backend'in
    tefas_import.parse_export()'u pd.to_datetime(dayfirst=True) kullandığı için ISO
    (YYYY-MM-DD) tarihler gün<=12 olduğunda ay/günü takas ediyor, gün>12 olduğunda
    ise satırı sessizce düşürüyordu (bkz. tarihsel.to_dayfirst_display)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = _tarihsel_mod.to_dayfirst_display(df)
    cols = [c for c in _tarihsel_mod.COLUMNS if c in df.columns]
    df.to_csv(path, index=False, columns=cols, encoding="utf-8-sig")
    return path


def write_getiriler_csv(df: pd.DataFrame, path: PathLike) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [c for c in (_getiriler_mod.COLUMNS + _getiriler_mod.EXTRA_COLUMNS) if c in df.columns]
    df.to_csv(path, index=False, columns=cols, encoding="utf-8-sig")
    return path


def write_portfoy_csv(df: pd.DataFrame, path: PathLike) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path
