from decimal import Decimal
import math
from typing import Optional, Any
from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.estadillo import (
    EstadilloDocument,
    EstadilloDocHeader,
    EstadilloHeader,
    EstadilloRow,
)


def escape_markdown_cell(text: Optional[str]) -> str:
    """Escapa caracteres pipe y unifica saltos de línea para celdas de tabla Markdown."""
    if text is None:
        return ""
    # Reemplazar saltos de línea por espacios simples
    escaped = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    # Escapar caracteres pipe
    escaped = escaped.replace("|", r"\|")
    return escaped.strip()


def format_evidence_cell(ev: Optional[EvidenceValue[Any]]) -> str:
    """Formatea el valor de una celda con prioridad: normalized > raw > empty string.

    Reglas:
    - Si existe normalized, se formatea según su tipo:
      - Booleano: 'True' o 'False' (escapado)
      - Entero o flotante con valor entero (ej. 145.0): '145'
      - Flotante decimal finito: representación exacta sin pérdida de precisión arbitraria,
        sin notación científica y sin comas locales (ej. 145.1234567 -> '145.1234567', 1e-7 -> '0.0000001')
      - Decimal finito: formateado en punto fijo sin notación científica
      - Texto u otro objeto: 'string' (escapado)
    - Si normalized es None pero raw existe: se escapa y emite raw verbatim.
    - Si ambos son None o ev es None: ''
    - Nunca se descarta texto crudo extraído ni se usa notación científica o comas locales.
    """
    if ev is None:
        return ""

    if ev.normalized is not None:
        val = ev.normalized
        if isinstance(val, bool):
            return escape_markdown_cell(str(val))
        if isinstance(val, int):
            return str(val)
        if isinstance(val, float):
            if not math.isfinite(val):
                return escape_markdown_cell(str(ev.raw or val))
            if val.is_integer():
                return str(int(val))
            # Flotante decimal finito limpio sin pérdida de precisión ni notación científica
            s = str(val)
            if "e" in s.lower():
                d = Decimal(s)
                formatted = format(d, "f")
            else:
                formatted = s
            if "." in formatted:
                formatted = formatted.rstrip("0").rstrip(".")
            return formatted
        if isinstance(val, Decimal):
            if not val.is_finite():
                return escape_markdown_cell(str(ev.raw or val))
            formatted = format(val, "f")
            if "." in formatted:
                formatted = formatted.rstrip("0").rstrip(".")
            return formatted
        return escape_markdown_cell(str(val))

    if ev.raw is not None:
        return escape_markdown_cell(str(ev.raw))

    return ""


def render_estadillo_markdown(doc: EstadilloDocument) -> str:
    """Renderiza un EstadilloDocument en la estructura exacta de dos tablas de AGENTS.md.

    Estructura obligatoria:
    1. Primera tabla (3 columnas x 2 filas de datos):
       | Objetivo | Fecha | Asistentes |
       |---|---|---|
       | {objetivo} | {fecha} | {asistentes} |
       | Equipamiento: {equipamiento} | Situación atmosférica: {situacion_atmosferica} | Especies: P;H;R;M |

    2. Segunda tabla (8 columnas):
       |id|col|fil|especie|altura_cm|foto|bbch|observaciones|
       |---|---|---|---|---|---|---|---|
       |{id}|{col}|{fil}|{especie}|{altura_cm}|{foto}|{bbch}|{observaciones}|

    3. Sección opcional '## Información adicional' (si existen entradas suplementarias).
    """
    lines: list[str] = []

    # --- PRIMERA TABLA: Metadatos de Cabecera ---
    header = doc.header
    obj_str = format_evidence_cell(header.objetivo) if header else ""
    fec_str = format_evidence_cell(header.fecha) if header else ""
    asis_str = format_evidence_cell(header.asistentes) if header else ""

    eq_val = format_evidence_cell(header.equipamiento) if header else ""
    eq_str = f"Equipamiento: {eq_val}" if eq_val else "Equipamiento: "

    atm_val = format_evidence_cell(header.situacion_atmosferica) if header else ""
    atm_str = f"Situación atmosférica: {atm_val}" if atm_val else "Situación atmosférica: "

    # Celda fija (2,2) según AGENTS.md
    esp_fija = "Especies: P;H;R;M"

    lines.append("| Objetivo | Fecha | Asistentes |")
    lines.append("|---|---|---|")
    lines.append(f"| {obj_str} | {fec_str} | {asis_str} |")
    lines.append(f"| {eq_str} | {atm_str} | {esp_fija} |")
    lines.append("")

    # --- SEGUNDA TABLA: Registros de Campo ---
    lines.append("|id|col|fil|especie|altura_cm|foto|bbch|observaciones|")
    lines.append("|---|---|---|---|---|---|---|---|")

    for page in doc.pages:
        for row in page.rows:
            id_c = format_evidence_cell(row.id)
            col_c = format_evidence_cell(row.col)
            fil_c = format_evidence_cell(row.fil)
            esp_c = format_evidence_cell(row.especie)
            alt_c = format_evidence_cell(row.altura_cm)
            foto_c = format_evidence_cell(row.foto)
            bbch_c = format_evidence_cell(row.bbch)
            obs_c = format_evidence_cell(row.observaciones)

            lines.append(f"|{id_c}|{col_c}|{fil_c}|{esp_c}|{alt_c}|{foto_c}|{bbch_c}|{obs_c}|")

    # --- SECCIÓN ADICIONAL: Información Fuera de Tabla ---
    if doc.additional_texts:
        lines.append("")
        lines.append("## Información adicional")
        for item in doc.additional_texts:
            text_val = (item.normalized or item.raw or "").strip()
            if text_val:
                lines.append("")
                lines.append(f"### Página {item.source_page}")
                lines.append("")
                lines.append(text_val)

    return "\n".join(lines) + "\n"
