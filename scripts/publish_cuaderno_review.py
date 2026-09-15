"""Publish a human-reviewed cuaderno_campo transcription and synchronized JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.fieldnotes.review.cuaderno_campo import publish_cuaderno_review
from src.fieldnotes.config import setup_encoding


def main(argv: list[str] | None = None) -> int:
    setup_encoding()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", type=Path, help="Sesión generada con --profile cuaderno_campo")
    parser.add_argument("--reviewer", required=True, help="Nombre o identificador de la persona revisora")
    args = parser.parse_args(argv)
    try:
        document = publish_cuaderno_review(args.session_dir, args.reviewer)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(f"Publicada revisión de {len(document.pages)} páginas en {args.session_dir.resolve() / 'reviewed'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
