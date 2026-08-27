import re
import unicodedata
from typing import Optional, Dict, List, Any, Tuple

from ..schemas.evidence import EvidenceValue
from ..schemas.notebook import (
    NotebookConfig,
    NotebookSectionConfig,
    NotebookDocument,
    NotebookSection,
    NotebookNoteItem,
    GenericTable,
)
from ..schemas.diagram import DiagramIR
from .markdown import escape_markdown_cell, format_evidence_cell


def _normalize_name(name: str) -> str:
    """Normaliza un nombre o alias para comparación no ambigua (sin distinción de acentos, mayúsculas, espacios o guiones bajos)."""
    if not isinstance(name, str):
        name = str(name)
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[\s_]+", " ", s).strip().lower()


def _find_section_config(
    title: Optional[str],
    cfg: NotebookConfig,
) -> Tuple[Optional[str], Optional[NotebookSectionConfig]]:
    """Busca la configuración de sección correspondiente por clave, título o alias."""
    if not title or not cfg.section_configs:
        return None, None
    norm = _normalize_name(title)
    for key, sc in cfg.section_configs.items():
        if norm == _normalize_name(key) or norm == _normalize_name(sc.title):
            return key, sc
        for alias in sc.aliases:
            if norm == _normalize_name(alias):
                return key, sc
    return None, None


def _is_text_subsumed_in_header(
    sec_title: Optional[str],
    sec_text: Optional[str],
    header: Any,
) -> bool:
    """Comprueba de forma conservadora si un texto ya fue incorporado íntegramente en la cabecera (Tabla 1)."""
    if not header or not sec_text:
        return False
    clean_text = _normalize_name(sec_text)
    if not clean_text:
        return False

    for f in ("objetivo", "fecha", "asistentes", "equipamiento", "situacion_atmosferica", "especies_declaradas"):
        ev = getattr(header, f, None)
        if ev is not None:
            ev_str = _normalize_name(ev.normalized or ev.raw or "")
            if ev_str:
                # Caso 1: coincidencia exacta
                if clean_text == ev_str:
                    return True
                # Caso 2: coincidencia con etiqueta (ej. "Objetivo: X", "Fecha: 2026-05-15")
                label_candidates = [
                    f"{f}: {ev_str}",
                    f"{f}:{ev_str}",
                ]
                if f == "situacion_atmosferica":
                    label_candidates.extend([
                        f"situacion atmosferica: {ev_str}",
                        f"situación atmosférica: {ev_str}",
                        f"clima: {ev_str}",
                    ])
                for lc in label_candidates:
                    if clean_text == lc:
                        return True
    return False


def render_notebook_markdown(
    doc: NotebookDocument,
    config: Optional[NotebookConfig] = None,
    assets_map: Optional[dict[str, str]] = None,
) -> str:
    """Renderiza deterministamente un NotebookDocument cumpliendo AGENTS.md y PR11.

    Estructura garantizada:
    1. INICIO OBLIGATORIO: Exactamente las dos tablas iniciales exigidas por AGENTS.md:
       - Primera tabla (3x2):
         | Objetivo | Fecha | Asistentes |
         |---|---|---|
         | {objetivo} | {fecha} | {asistentes} |
         | Equipamiento: {equipamiento} | Situación atmosférica: {situacion_atmosferica} | Especies: P;H;R;M |
       - Segunda tabla (8 columnas):
         |id|col|fil|especie|altura_cm|foto|bbch|observaciones|
         |---|---|---|---|---|---|---|---|
         |{id}|{col}|{fil}|{especie}|{altura_cm}|{foto}|{bbch}|{observaciones}|
    2. SECCIONES FLEXIBLES (después de las dos tablas obligatorias):
       - No presupone secciones fijas ni fabrica bloques ausentes.
       - Renderiza únicamente lo observado o habilitado según config.section_order.
       - Aplica section_configs (enabled, title, render_order) sin alterar la evidencia canónica de NotebookDocument.
       - Elimina duplicados de texto ya trasladados a las dos primeras tablas.
       - Renderiza tablas genéricas con ancho uniforme max_cols y escape seguro de celdas.
       - Renderiza diagramas (Mermaid / SVG) con descripciones textuales accesibles.
       - '## Información adicional' para textos suplementarios.
    """
    cfg = config or NotebookConfig()
    assets = assets_map or {}
    lines: list[str] = []

    from ..diagrams.render_markdown import (
        render_markdown as render_diagram_markdown,
        sanitize_markdown_text,
    )

    # =========================================================================
    # 1. PRIMERA TABLA: Metadatos de Cabecera (Obligatoria 3x2 según AGENTS.md)
    # =========================================================================
    header = doc.header
    obj_str = format_evidence_cell(header.objetivo) if header else ""
    fec_str = format_evidence_cell(header.fecha) if header else ""
    asis_str = format_evidence_cell(header.asistentes) if header else ""

    eq_val = format_evidence_cell(header.equipamiento) if header else ""
    eq_str = f"Equipamiento: {eq_val}" if eq_val else "Equipamiento: "

    atm_val = format_evidence_cell(header.situacion_atmosferica) if header else ""
    atm_str = f"Situación atmosférica: {atm_val}" if atm_val else "Situación atmosférica: "

    esp_fija = "Especies: P;H;R;M"

    lines.append("| Objetivo | Fecha | Asistentes |")
    lines.append("|---|---|---|")
    lines.append(f"| {obj_str} | {fec_str} | {asis_str} |")
    lines.append(f"| {eq_str} | {atm_str} | {esp_fija} |")
    lines.append("")

    # =========================================================================
    # 2. SEGUNDA TABLA: Registros de Campo / Estadillo (Obligatoria 8 cols)
    # =========================================================================
    lines.append("|id|col|fil|especie|altura_cm|foto|bbch|observaciones|")
    lines.append("|---|---|---|---|---|---|---|---|")

    for row in doc.estadillo_rows:
        id_c = format_evidence_cell(row.id)
        col_c = format_evidence_cell(row.col)
        fil_c = format_evidence_cell(row.fil)
        esp_c = format_evidence_cell(row.especie)
        alt_c = format_evidence_cell(row.altura_cm)
        foto_c = format_evidence_cell(row.foto)
        bbch_c = format_evidence_cell(row.bbch)
        obs_c = format_evidence_cell(row.observaciones)

        lines.append(f"|{id_c}|{col_c}|{fil_c}|{esp_c}|{alt_c}|{foto_c}|{bbch_c}|{obs_c}|")

    lines.append("")

    # =========================================================================
    # 3. SECCIONES FLEXIBLES (según orden configurado y evidencia observada)
    # =========================================================================
    rendered_section_keys: set[str] = set()

    for block_key in cfg.section_order:
        if block_key in ("metadata", "estadillo_table"):
            # Ya renderizadas al inicio obligatoriamente
            continue

        elif block_key == "sections":
            prepared_sections: list[Tuple[Tuple[int, int, int], str, NotebookSection]] = []
            for sec_idx, sec in enumerate(doc.sections):
                sec_text = (sec.content or " ".join(p.normalized or p.raw or "" for p in sec.paragraphs)).strip()
                if _is_text_subsumed_in_header(sec.title, sec_text, doc.header):
                    continue

                sec_key, sec_cfg = _find_section_config(sec.title, cfg)
                if sec_cfg is not None:
                    if not sec_cfg.enabled:
                        # Omitir sección deshabilitada por configuración
                        continue
                    display_title = sec_cfg.title
                    order_tuple = (0, sec_cfg.render_order if sec_cfg.render_order is not None else 999999, sec_idx)
                else:
                    display_title = sec.title or "Sección"
                    order_tuple = (1, 999999, sec_idx)

                prepared_sections.append((order_tuple, display_title, sec))

            prepared_sections.sort(key=lambda item: item[0])

            for _, display_title, sec in prepared_sections:
                lvl = max(1, min(6, sec.level))
                heading_hashes = "#" * lvl

                if display_title:
                    title_san = sanitize_markdown_text(display_title)
                    lines.append(f"{heading_hashes} {title_san}")
                    lines.append("")
                    rendered_section_keys.add(_normalize_name(display_title))

                if sec.content and sec.content.strip():
                    lines.append(sanitize_markdown_text(sec.content))
                    lines.append("")

                for p in sec.paragraphs:
                    p_val = format_evidence_cell(p)
                    if p_val:
                        lines.append(sanitize_markdown_text(p_val))
                        lines.append("")

        elif block_key == "notes":
            if doc.notes:
                lines.append("## Notas")
                lines.append("")
                for note in doc.notes:
                    text_val = format_evidence_cell(note.text)
                    if text_val:
                        text_san = sanitize_markdown_text(text_val)
                        if note.category and note.category.strip():
                            cat_san = sanitize_markdown_text(note.category)
                            lines.append(f"- **{cat_san}:** {text_san}")
                        else:
                            lines.append(f"- {text_san}")
                lines.append("")
                rendered_section_keys.add("notas")

        elif block_key == "tables":
            for tbl in doc.tables:
                if tbl.title and tbl.title.strip():
                    lines.append(f"### {sanitize_markdown_text(tbl.title)}")
                    lines.append("")

                max_cols = max(len(tbl.headers), max((len(r) for r in tbl.rows), default=0))
                if max_cols > 0:
                    if tbl.headers:
                        h_cells = [format_evidence_cell(h) for h in tbl.headers]
                        if len(h_cells) < max_cols:
                            h_cells.extend([""] * (max_cols - len(h_cells)))
                    else:
                        h_cells = [f"col_{i+1}" for i in range(max_cols)]

                    lines.append("|" + "|".join(h_cells) + "|")
                    lines.append("|" + "|".join("---" for _ in range(max_cols)) + "|")

                    for row in tbl.rows:
                        r_cells = [format_evidence_cell(c) for c in row]
                        if len(r_cells) < max_cols:
                            r_cells.extend([""] * (max_cols - len(r_cells)))
                        lines.append("|" + "|".join(r_cells) + "|")

                    lines.append("")

                if tbl.caption and tbl.caption.strip():
                    lines.append(f"*{sanitize_markdown_text(tbl.caption)}*")
                    lines.append("")

        elif block_key == "diagrams":
            page_diag_counts: dict[int, int] = {}
            for diag in doc.diagrams:
                p_num = diag.source_page
                d_idx = page_diag_counts.get(p_num, 0)
                page_diag_counts[p_num] = d_idx + 1
                diag_key = f"p{p_num}_d{d_idx}"
                asset_path = assets.get(diag_key)

                if diag.diagram_type == "flowchart":
                    # Flowcharts incrustan Mermaid directamente sin enlace a SVG
                    diag_md = render_diagram_markdown(
                        diag,
                        asset_relative_path=None,
                        include_visual=cfg.render_diagram_visuals,
                    )
                    lines.append(diag_md.rstrip())
                    lines.append("")
                else:
                    # Croquis de campo / esquema GPS
                    effective_asset_path = asset_path if cfg.render_diagram_visuals else None
                    diag_md = render_diagram_markdown(
                        diag,
                        asset_relative_path=effective_asset_path,
                        include_visual=cfg.render_diagram_visuals,
                    )
                    lines.append(diag_md.rstrip())
                    lines.append("")

        elif block_key == "additional_text":
            if doc.additional_texts:
                lines.append("## Información adicional")
                for item in doc.additional_texts:
                    text_val = format_evidence_cell(item)
                    if text_val:
                        lines.append("")
                        lines.append(f"### Página {item.source_page}")
                        lines.append("")
                        lines.append(sanitize_markdown_text(text_val))
                lines.append("")
                rendered_section_keys.add("informacion adicional")

    # =========================================================================
    # 4. SECCIONES VACÍAS CONFIGURADAS (solo si include_empty_sections=True)
    # =========================================================================
    if cfg.include_empty_sections:
        for sec_key, sec_cfg in cfg.section_configs.items():
            if sec_cfg.enabled and sec_cfg.include_if_empty:
                norm_t = _normalize_name(sec_cfg.title)
                if norm_t not in rendered_section_keys:
                    title_san = sanitize_markdown_text(sec_cfg.title)
                    lines.append(f"## {title_san}")
                    lines.append("")
                    lines.append("*(Sin contenido observado [configuración])*")
                    lines.append("")

    return "\n".join(lines).rstrip() + "\n"