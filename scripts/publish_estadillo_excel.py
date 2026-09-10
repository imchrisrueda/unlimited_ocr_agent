"""Publish a reviewed dated Excel workbook to canonical estadillo data artifacts."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.fieldnotes.render.excel import estadillo_review_workbook_name, publish_estadillo_xlsx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "session_dir",
        type=Path,
        help="Directorio de sesión que contiene review/datos_<sesión>.xlsx",
    )
    args = parser.parse_args(argv)
    session_dir = args.session_dir.resolve()
    try:
        count = publish_estadillo_xlsx(
            session_dir / "review" / estadillo_review_workbook_name(session_dir.name),
            session_dir / "datos.csv",
            session_dir / "document.json",
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Publicados datos.csv y document.json con {count} registros.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
