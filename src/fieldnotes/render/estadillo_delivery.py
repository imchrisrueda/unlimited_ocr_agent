"""Renderizadores deterministas de la entrega canónica del perfil estadillo."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
import io
import json
import math
import re
from typing import Any, Optional

from ..schemas.estadillo import EstadilloDocument
from ..schemas.evidence import EvidenceValue


CSV_COLUMNS = (
    "id",
    "col",
    "fil",
    "especie",
    "altura_cm",
    "foto",
    "bbch",
    "observaciones",
)

_SPECIES_CODES = {"ap": "P", "ah": "H", "ar": "R", "mz": "M", "p": "P", "h": "H", "r": "R", "m": "M"}
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SPANISH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")


def evidence_value(ev: Optional[EvidenceValue[Any]]) -> str:
    """Devuelve el valor canónico sin aplicar escaping específico de Markdown."""
    if ev is None:
        return ""
    value = ev.normalized if ev.normalized is not None else ev.raw
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(ev.raw or "")
        if value.is_integer():
            return str(int(value))
        text = format(Decimal(str(value)), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(ev.raw or "")
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def resolve_session_date(doc: EstadilloDocument) -> Optional[str]:
    """Retorna una fecha ISO inequívoca, aceptando la notación española observada."""
    if doc.header is None or doc.header.fecha is None:
        return None
    if any(w.code == "HEADER_CONFLICT" and w.field_name == "fecha" for w in doc.warnings):
        return None
    raw = evidence_value(doc.header.fecha).strip()
    if _ISO_DATE_RE.fullmatch(raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return None

    match = _SPANISH_DATE_RE.fullmatch(raw)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _yaml_scalar(value: Optional[str]) -> str:
    if value is None or not value.strip():
        return "null"
    return json.dumps(value.strip(), ensure_ascii=False)


def _split_assistants(raw: str) -> list[str]:
    if not raw.strip():
        return []
    if re.search(r"[;,\n]", raw):
        return [part.strip() for part in re.split(r"[;,\n]+", raw) if part.strip()]
    return [raw.strip()]


def _species_descriptions(raw: str) -> dict[str, Optional[str]]:
    result: dict[str, Optional[str]] = {code: None for code in ("P", "H", "R", "M")}
    if not raw.strip():
        return result
    for part in re.split(r"[;\n]+", raw):
        match = re.fullmatch(r"\s*([A-Za-z]{1,2})\s*[:=\-]\s*(.+?)\s*", part)
        if not match:
            continue
        canonical = _SPECIES_CODES.get(match.group(1).lower())
        description = match.group(2).strip()
        if canonical and description:
            result[canonical] = description
    return result


def render_estadillo_notes(doc: EstadilloDocument) -> str:
    """Genera ``notas.md`` con front matter YAML y solo notas realmente observadas."""
    header = doc.header
    objetivo = evidence_value(header.objetivo) if header else ""
    fecha = resolve_session_date(doc)
    asistentes = _split_assistants(evidence_value(header.asistentes) if header else "")
    equipamiento = evidence_value(header.equipamiento) if header else ""
    atmosfera = evidence_value(header.situacion_atmosferica) if header else ""
    species = _species_descriptions(evidence_value(header.especies_declaradas) if header else "")

    lines = [
        "---",
        f"objetivo: {_yaml_scalar(objetivo)}",
        f"fecha: {fecha or 'null'}",
    ]
    if asistentes:
        lines.append("asistentes:")
        lines.extend(f"  - {_yaml_scalar(item)}" for item in asistentes)
    else:
        lines.append("asistentes: []")
    lines.extend(
        [
            f"equipamiento: {_yaml_scalar(equipamiento)}",
            f"situacion_atmosferica: {_yaml_scalar(atmosfera)}",
            "especies:",
            *[f'  - "{code}": {_yaml_scalar(species[code])}' for code in ("P", "H", "R", "M")],
            'datos: "datos.csv"',
            "---",
        ]
    )

    notes = [evidence_value(item) for item in doc.additional_texts]
    notes = [text for text in notes if text]
    if notes:
        lines.extend(["", "# Notas de campo"])
        for item, text in zip((x for x in doc.additional_texts if evidence_value(x)), notes):
            lines.extend(["", f"## Página {item.source_page}", "", text])

    return "\n".join(lines) + "\n"


def render_estadillo_csv(doc: EstadilloDocument) -> str:
    """Genera ``datos.csv`` UTF-8 con quoting RFC 4180 y orden documental."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for page in doc.pages:
        for row in page.rows:
            writer.writerow([evidence_value(getattr(row, column)) for column in CSV_COLUMNS])
    return output.getvalue()
