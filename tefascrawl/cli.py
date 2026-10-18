"""tefascrawl CLI.

UYARI: docs/veri_kaynaklari.md ekibin "TEFAS API'sini script ile çekmeyiz" kararını
içeriyordu. Bu araç, kullanıcının bu kararın aksine verdiği açık talimatla, TEFAS'ın
WAF korumasını gerçek bir tarayıcıyla geçip sitenin iç API'lerine doğrudan istek
atar. Bkz. tefascrawl/README.md.

Kullanım:
    python -m tefascrawl --help
    python -m tefascrawl --months 13 --with-portfoy
    python -m tefascrawl --import-to-backend
"""
from __future__ import annotations

import argparse
import sys

from . import pipeline
from .endpoints import FON_TIPI_YAT


def _progress(*args: object) -> None:
    print("  ", *args, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tefascrawl", description=__doc__.strip().splitlines()[0])
    p.add_argument("--out-dir", default="tefascrawl/out", help="Çıktı CSV dosyalarının yazılacağı klasör")
    p.add_argument("--months", type=int, default=13, help="Tarihsel veri için kaç aylık geçmiş (varsayılan: 13)")
    p.add_argument("--fon-tipi", default=FON_TIPI_YAT, choices=["YAT", "EMK", "BYF"],
                    help="tefas.md kapsamı YAT'tır; EMK/BYF bilgi amaçlı eklendi")
    p.add_argument("--headed", action="store_true", help="Tarayıcıyı görünür çalıştır (hata ayıklama)")
    p.add_argument("--with-portfoy", action="store_true", help="Portföy dağılımını da çek (opsiyonel, tefas.md #3)")
    p.add_argument("--import-to-backend", action="store_true",
                    help="Çekilen CSV'leri backend/data/snapshots içine doğrudan aktar "
                         "(backend/.venv aktifken çalıştırılmalı)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = pipeline.run(
        out_dir=args.out_dir,
        months=args.months,
        fon_tipi=args.fon_tipi,
        headless=not args.headed,
        import_to_backend=args.import_to_backend,
        include_portfoy=args.with_portfoy,
        on_progress=_progress,
    )
    print()
    for key, val in result.items():
        print(f"{key}: {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
