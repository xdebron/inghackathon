"""TEFAS içe aktarım güvenliği (veri tabanı): birleştirme eski kayıtları korur, okunamayan tarihte hiçbir satıra
dokunulmaz, eşzamanlı içe aktarımlar satır kaybetmez, her içe aktarım bir çekim kaydı bırakır."""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import text

from app.db import store
from app.sources import tefas_import as ti


def _csv(rows):
    head = "Tarih;Fon Kodu;Fon Adı;Fiyat;Tedavüldeki Pay Sayısı;Kişi Sayısı;Fon Toplam Değer\n"
    return (head + "\n".join(";".join(map(str, r)) for r in rows)).encode("utf-8")


def _prices():
    return store.fund_prices("tefas_export")


def test_merge_keeps_old_rows_and_logs_runs(tmp_db):
    ti.import_export(_csv([["01.10.2026", "AAA", "A FONU", "1,0", "", "", ""], ["02.10.2026", "AAA", "A FONU", "1,1", "", "", ""]]), "a.csv")
    r = ti.import_export(_csv([["03.10.2026", "AAA", "A FONU", "1,2", "", "", ""]]), "b.csv")
    df = _prices()
    assert len(df) == 3 and r["toplam_sonrasi"]["satir"] == 3
    assert list(df["tarih"]) == ["2026-10-01", "2026-10-02", "2026-10-03"]   # gg.aa.yyyy -> ISO (02.10 = 2 Ekim)
    assert [i["dosya"] for i in ti._load_meta()["imports"]] == ["a.csv", "b.csv"]
    runs = [x for x in store.runs() if x["kaynak"] == "tefas_export"]
    assert len(runs) == 2 and all(x["durum"] == "basarili" and x["yontem"] == "yukleme" for x in runs)


def test_same_day_new_price_is_reported_as_conflict(tmp_db):
    ti.import_export(_csv([["01.10.2026", "AAA", "A FONU", "1,0", "", "", ""]]), "a.csv")
    r = ti.import_export(_csv([["01.10.2026", "AAA", "A FONU", "1,5", "", "", ""]]), "b.csv")
    assert r["celiskiler"] and r["celiskiler"][0]["eski"] == 1.0 and r["celiskiler"][0]["yeni"] == 1.5
    assert len(_prices()) == 1 and _prices()["fiyat"].iloc[0] == 1.5


def test_unreadable_existing_dates_abort_without_touching_rows(tmp_db):
    with tmp_db.begin() as c:
        c.execute(text("INSERT INTO fon_fiyat (kaynak, kod, tarih, fiyat) VALUES ('tefas_export', 'AAA', 'bozuk', 1.0)"))
    before = _prices().to_dict("records")
    with pytest.raises(ValueError):
        ti.import_export(_csv([["03.10.2026", "AAA", "A FONU", "1,2", "", "", ""]]), "d.csv")
    assert _prices().to_dict("records") == before


def test_concurrent_imports_do_not_lose_rows(tmp_db):
    files = [_csv([[f"{d:02d}.10.2026", f"F{i:02d}", "X FONU", "1,0", "", "", ""] for d in range(1, 11)]) for i in range(6)]
    threads = [threading.Thread(target=ti.import_export, args=(f, f"f{i}.csv")) for i, f in enumerate(files)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_prices()) == 60
    assert len(ti._load_meta()["imports"]) == 6
