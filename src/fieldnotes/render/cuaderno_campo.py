"""Renderizado secuencial, visual y sin contrato de estadillo."""
from typing import Optional

from ..schemas.notebook import NotebookConfig, NotebookDocument
from ..diagrams.render_markdown import render_markdown as render_diagram_markdown, sanitize_markdown_text
from .markdown import format_evidence_cell


def render_cuaderno_campo_markdown(
    doc: NotebookDocument,
    config: Optional[NotebookConfig] = None,
    assets_map: Optional[dict[str, str]] = None,
) -> str:
    """Genera un cuaderno por páginas, omitiendo por completo los datos de estadillo."""
    cfg = config or NotebookConfig()
    assets = assets_map or {}
    lines = ["# Cuaderno de campo", ""]
    for page in sorted(doc.pages, key=lambda item: item.page_number):
        lines.extend([f"## Página {page.page_number}", ""])
        seen_text: set[str] = set()
        if page.header:
            for label in ("objetivo", "fecha", "asistentes", "equipamiento", "situacion_atmosferica"):
                value = format_evidence_cell(getattr(page.header, label, None))
                if value:
                    lines.extend([f"**{label.replace('_', ' ').capitalize()}:** {sanitize_markdown_text(value)}", ""])
        for section in page.sections:
            if section.title:
                lines.extend([f"### {sanitize_markdown_text(section.title)}", ""])
            if section.content and not section.paragraphs:
                clean_content = sanitize_markdown_text(section.content)
                if clean_content and clean_content not in seen_text:
                    lines.extend([clean_content, ""])
                    seen_text.add(clean_content)
            for paragraph in section.paragraphs:
                value = format_evidence_cell(paragraph)
                if value:
                    clean_value = sanitize_markdown_text(value)
                    if clean_value not in seen_text:
                        lines.extend([clean_value, ""])
                        seen_text.add(clean_value)
        for table in page.tables:
            if table.title:
                lines.extend([f"### {sanitize_markdown_text(table.title)}", ""])
            width = max(len(table.headers), max((len(row) for row in table.rows), default=0))
            if width:
                headers = [format_evidence_cell(cell) for cell in table.headers] or [f"col_{i + 1}" for i in range(width)]
                headers += [""] * (width - len(headers))
                lines.extend(["|" + "|".join(headers) + "|", "|" + "|".join("---" for _ in range(width)) + "|"])
                for row in table.rows:
                    cells = [format_evidence_cell(cell) for cell in row] + [""] * (width - len(row))
                    lines.append("|" + "|".join(cells) + "|")
                lines.append("")
        for note in page.notes:
            value = format_evidence_cell(note.text)
            if value:
                clean_value = sanitize_markdown_text(value)
                if clean_value not in seen_text:
                    lines.extend([f"- {clean_value}", ""])
                    seen_text.add(clean_value)
        for index, diagram in enumerate(page.diagrams):
            key = f"p{diagram.source_page}_d{index}"
            lines.extend(["### Reconstrucción VLM", ""])
            asset = assets.get(key) if cfg.render_diagram_visuals else None
            lines.extend([render_diagram_markdown(diagram, asset_relative_path=asset, include_visual=cfg.render_diagram_visuals).rstrip(), ""])
        if page.additional_text:
            lines.extend([sanitize_markdown_text(page.additional_text), ""])
        lines.extend(["### Imagen original de la página", "", f"![Página {page.page_number}](pages/page_{page.page_number:03d}.png)", ""])
    return "\n".join(lines).rstrip() + "\n"
