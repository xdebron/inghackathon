"""Uçtan uca çekim: TEFAS'tan veriyi al, CSV'ye yaz, istenirse backend/data/snapshots
içine doğrudan aktar (manuel "indir -> arayüzden yükle" adımlarının otomasyonu;
bkz. docs/tefas.md "Yükleme" bölümü).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Callable, Optional

from . import export, getiriler, portfoy, tarihsel
from .endpoints import FON_TIPI_YAT
from .session import TefasSession

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"


def _backend_tefas_import():
    """backend/app/sources/tefas_import.py'yi, backend'in kendi venv'i aktifken import eder."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from app.sources import tefas_import  # type: ignore  # noqa: PLC0415

    return tefas_import


def run(
    out_dir: str = "tefascrawl/out",
    months: int = 13,
    fon_tipi: str = FON_TIPI_YAT,
    headless: bool = True,
    import_to_backend: bool = False,
    include_portfoy: bool = False,
    on_progress: Optional[Callable[..., None]] = None,
) -> dict:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    result: dict = {"as_of": today, "fon_tipi": fon_tipi}

    with TefasSession(headless=headless) as session:
        df_tarihsel = tarihsel.fetch_tarihsel(session, months=months, fon_tipi=fon_tipi, on_progress=on_progress)
        start = df_tarihsel["Tarih"].min() if not df_tarihsel.empty else today
        end = df_tarihsel["Tarih"].max() if not df_tarihsel.empty else today
        tarihsel_path = out_path / f"tefas_tarihsel_{start}_{end}.csv"
        export.write_tarihsel_csv(df_tarihsel, tarihsel_path)
        result["tarihsel"] = {"dosya": str(tarihsel_path), "satir": int(len(df_tarihsel))}

        if on_progress:
            on_progress("getiriler", "basliyor")
        df_getiriler = getiriler.fetch_getiriler(session, fon_tipi=fon_tipi)
        getiriler_path = out_path / f"tefas_getiriler_{today}.csv"
        export.write_getiriler_csv(df_getiriler, getiriler_path)
        result["getiriler"] = {"dosya": str(getiriler_path), "satir": int(len(df_getiriler))}

        if include_portfoy:
            if on_progress:
                on_progress("portfoy", "basliyor")
            df_portfoy = portfoy.fetch_portfoy_dagilimi(session, fon_tipi=fon_tipi)
            portfoy_path = out_path / f"tefas_portfoy_{today}.csv"
            export.write_portfoy_csv(df_portfoy, portfoy_path)
            result["portfoy"] = {"dosya": str(portfoy_path), "satir": int(len(df_portfoy))}

    if import_to_backend:
        tefas_import = _backend_tefas_import()
        tarihsel_file = Path(result["tarihsel"]["dosya"])
        with tarihsel_file.open("rb") as f:
            result["tarihsel"]["import_sonucu"] = tefas_import.import_any(f.read(), tarihsel_file.name, today)
        getiriler_file = Path(result["getiriler"]["dosya"])
        with getiriler_file.open("rb") as f:
            result["getiriler"]["import_sonucu"] = tefas_import.import_any(f.read(), getiriler_file.name, today)

    return result
