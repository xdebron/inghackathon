"""TEFAS içe aktarım güvenliği: yedek, kayıt azalmasına karşı koruma, kilit, karışık tarih biçimi."""
from __future__ import annotations

import threading

import pandas as pd
import pytest

from app.sources import tefas_import as ti


def _csv(rows):
    head = "Tarih;Fon Kodu;Fon Adı;Fiyat;Tedavüldeki Pay Sayısı;Kişi Sayısı;Fon Toplam Değer\n"
    return (head + "\n".join(";".join(map(str, r)) for r in rows)).encode("utf-8")


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ti, "PRICES", tmp_path / "tefas_fiyatlar.csv")
    monkeypatch.setattr(ti, "META", tmp_path / "tefas_meta.json")
    monkeypatch.setattr(ti, "LOCK", tmp_path / ".lock")
    monkeypatch.setattr(ti, "BACKUP_DIR", tmp_path / "_yedek")
    return tmp_path


def test_merge_keeps_old_rows_and_backs_up(tmp_store):
    ti.import_export(_csv([["01.10.2026", "AAA", "A FONU", "1,0", "", "", ""], ["02.10.2026", "AAA", "A FONU", "1,1", "", "", ""]]), "a.csv")
    r = ti.import_export(_csv([["03.10.2026", "AAA", "A FONU", "1,2", "", "", ""]]), "b.csv")
    df = pd.read_csv(ti.PRICES)
    assert len(df) == 3 and r["toplam_sonrasi"]["satir"] == 3
    assert r["yedek"] and len(list((tmp_store / "_yedek").glob("tefas_fiyatlar_*.csv"))) == 1
    assert [i["dosya"] for i in ti._load_meta()["imports"]] == ["a.csv", "b.csv"]


def test_mixed_date_formats_in_existing_file_are_read(tmp_store):
    # Mevcut dosyada hem ISO hem gg.aa.yyyy tarih olsa da kayıt kaybolmamalı
    ti.PRICES.write_text("tarih,kod,ad,fiyat,pay,kisi,buyukluk\n2026-10-01,AAA,A,1.0,,,\n02.10.2026,AAA,A,1.1,,,\n", encoding="utf-8")
    ti.import_export(_csv([["03.10.2026", "AAA", "A FONU", "1,2", "", "", ""]]), "c.csv")
    df = pd.read_csv(ti.PRICES)
    assert list(df["tarih"]) == ["2026-10-01", "2026-10-02", "2026-10-03"]  # 02.10 -> 2 Ekim (10 Şubat değil)


def test_unreadable_existing_dates_abort_without_touching_files(tmp_store):
    ti.PRICES.write_text("tarih,kod,ad,fiyat,pay,kisi,buyukluk\nbozuk,AAA,A,1.0,,,\n2026-10-02,AAA,A,1.1,,,\n", encoding="utf-8")
    before = ti.PRICES.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        ti.import_export(_csv([["03.10.2026", "AAA", "A FONU", "1,2", "", "", ""]]), "d.csv")
    assert ti.PRICES.read_text(encoding="utf-8") == before


def test_concurrent_imports_do_not_lose_rows(tmp_store):
    files = [_csv([[f"{d:02d}.10.2026", f"F{i:02d}", "X FONU", "1,0", "", "", ""] for d in range(1, 11)]) for i in range(6)]
    threads = [threading.Thread(target=ti.import_export, args=(f, f"f{i}.csv")) for i, f in enumerate(files)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(pd.read_csv(ti.PRICES)) == 60
    assert len(ti._load_meta()["imports"]) == 6
