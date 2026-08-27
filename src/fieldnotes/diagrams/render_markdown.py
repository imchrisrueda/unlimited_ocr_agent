import re
from typing import Optional, List
from ..schemas.diagram import DiagramIR
from .validation import validate_diagram_ir
from .render_mermaid import render_mermaid

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def sanitize_markdown_text(text: Optional[str]) -> str:
    """Sanea de forma centralizada y determinista texto libre para Markdown.

    Garantías:
    1. Normaliza controles y saltos de línea a un único espacio.
    2. Escapa entidades HTML (<, >, &) para impedir inyección de tags o scripts.
    3. Escapa caracteres sintácticos de Markdown (\, `, *, _, ~, [, ], (, ), #, |, !)
       evitando que texto libre cree nuevos headings, list items, enlaces o rompa énfasis.
    4. Reemplaza comillas dobles por &quot; para uso seguro en atributos y texto.
    """
    if text is None:
        return ""

    s = str(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    s = _CONTROL_CHARS_RE.sub("", s)

    # 1. Escapar entidades HTML
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 2. Escapar sintaxis de Markdown
    s = s.replace("\\", "\\\\")
    s = s.replace("`", "\\`")
    s = s.replace("*", "\\*")
    s = s.replace("_", "\\_")
    s = s.replace("~", "\\~")
    s = s.replace("[", "\\[")
    s = s.replace("]", "\\]")
    s = s.replace("(", "\\(")
    s = s.replace(")", "\\)")
    s = s.replace("#", "\\#")
    s = s.replace("|", "\\|")
    s = s.replace("!", "\\!")
    s = s.replace('"', "&quot;")

    s = re.sub(r"\s+", " ", s).strip()
    return s


def sanitize_code_span(text: Optional[str]) -> str:
    """Sanea identificadores o literales que se colocan dentro de code spans (`...`)."""
    if text is None:
        return ""
    s = str(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    s = _CONTROL_CHARS_RE.sub("", s)
    s = s.replace("`", "'").replace("\\", "/")
    return s.strip()


def sanitize_alt_text(text: Optional[str]) -> str:
    """Sanea el texto alternativo de una imagen Markdown ![alt](url)."""
    if text is None:
        return ""
    s = str(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    s = _CONTROL_CHARS_RE.sub("", s)
    s = s.replace("[", "(").replace("]", ")").replace("<", "").replace(">", "")
    s = s.replace('"', "'").replace("`", "'").replace("\\", "/")
    return s.strip()


def validate_and_sanitize_asset_path(asset_path: Optional[str], default_path: str) -> str:
    """Valida que una ruta de asset sea una ruta relativa POSIX confinada y segura.

    Rechaza:
    - Esquemas de protocolo (http://, file://, javascript:, data:)
    - Letras de unidad o rutas absolutas (/, C:)
    - Secuencias de Directory Traversal (..)
    - Separadores no POSIX (\)
    - Query strings (?) o fragmentos (#)
    - Espacios, saltos o caracteres que rompan la sintaxis de enlaces Markdown
    """
    if not asset_path:
        return default_path

    p = str(asset_path).strip()
    if not p:
        return default_path

    # 1. Sin esquemas de protocolo ni unidades Windows
    if ":" in p:
        raise ValueError(
            f"asset_relative_path contiene esquemas de protocolo o unidades no permitidas: {p!r}"
        )

    # 2. Sin barras invertidas
    if "\\" in p:
        raise ValueError(
            f"asset_relative_path debe usar exclusivamente separadores POSIX (/), no barras invertidas: {p!r}"
        )

    # 3. Sin rutas absolutas
    if p.startswith("/"):
        raise ValueError(f"asset_relative_path debe ser una ruta relativa, no absoluta: {p!r}")

    # 4. Sin directory traversal
    parts = p.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(
            f"asset_relative_path contiene secuencias de escape de directorio ('..'): {p!r}"
        )

    # 5. Sin query ni fragmentos
    if "?" in p or "#" in p:
        raise ValueError(
            f"asset_relative_path no puede contener query strings o fragmentos ('?', '#'): {p!r}"
        )

    # 6. Sin caracteres de control o sintácticos que rompan el enlace
    if re.search(r"[\s\(\)\[\]\"'<>\\]", p):
        raise ValueError(
            f"asset_relative_path contiene caracteres no permitidos en enlaces Markdown: {p!r}"
        )

    return p


def get_diagram_type_label(diagram_type: str) -> str:
    """Retorna una etiqueta humana en español para el tipo de diagrama."""
    labels = {
        "field_sketch": "Croquis de campo",
        "flowchart": "Diagrama de flujo",
        "gps_sketch": "Esquema GPS",
    }
    return labels.get(diagram_type, f"Diagrama ({diagram_type})")


def render_markdown(
    diagram: DiagramIR,
    asset_relative_path: Optional[str] = None,
) -> str:
    """Genera una descripción Markdown completa, accesible y determinista de DiagramIR.

    Garantías de accesibilidad y fidelidad semántica:
    1. Incluye bloque de código Mermaid (para flowchart) o enlace al recurso SVG (para croquis).
    2. Descripción textual autosuficiente para lectura humana y análisis por LLMs sin visión.
    3. Basada EXCLUSIVAMENTE en campos explícitos de DiagramIR (sin inventar relaciones ni orientación).
    4. Declara de forma clara y explícita la ausencia de GPS exacto cuando georeferenced=False.
    5. Cuando georeferenced=True, lista CRS y coordenadas con su raw_text y procedencia de página.
    6. Salida 100% determinista byte a byte y sanitizada contra inyecciones Markdown/HTML.
    """
    if not isinstance(diagram, DiagramIR):
        raise TypeError(f"Se esperaba una instancia de DiagramIR, recibido: {type(diagram).__name__}")

    # Validar diagrama antes de renderizar
    validate_diagram_ir(diagram, raise_on_error=True)

    lines: list[str] = []
    type_label = get_diagram_type_label(diagram.diagram_type)
    title_raw = diagram.title if diagram.title and diagram.title.strip() else type_label
    title_sanitized = sanitize_markdown_text(title_raw)

    # 1. Cabecera y metadatos
    lines.append(f"### {title_sanitized}")
    lines.append("")
    lines.append(f"- **Tipo de diagrama:** {type_label} (`{sanitize_code_span(diagram.diagram_type)}`)")
    lines.append(f"- **Página fuente:** {diagram.source_page}")
    if diagram.description and diagram.description.strip():
        desc_sanitized = sanitize_markdown_text(diagram.description)
        lines.append(f"- **Descripción original:** {desc_sanitized}")
    lines.append("")

    # 2. Inclusión visual (Mermaid para flowchart, imagen SVG para croquis)
    if diagram.diagram_type == "flowchart":
        mermaid_code = render_mermaid(diagram)
        lines.append("```mermaid")
        lines.append(mermaid_code.rstrip())
        lines.append("```")
        lines.append("")
    else:
        # Enlace a SVG
        default_svg_path = f"assets/sketch_p{diagram.source_page:03d}.svg"
        valid_asset_path = validate_and_sanitize_asset_path(asset_relative_path, default_svg_path)
        alt_label = sanitize_alt_text(diagram.title or type_label)
        lines.append(f"![{alt_label}]({valid_asset_path})")
        lines.append("")

    # 3. Descripción textual estructurada para accesibilidad
    lines.append("#### Descripción textual")
    lines.append("")

    # 3.1. Estado de georreferenciación y GPS
    if not diagram.georeferenced:
        lines.append(
            "- **Georreferenciación:** No georreferenciado. El croquis no contiene coordenadas "
            "GPS exactas (las posiciones visuales observadas son relativas al dibujo [0.0, 1.0])."
        )
    else:
        crs_sanitized = sanitize_markdown_text(diagram.crs or "No especificado")
        lines.append("- **Georreferenciación:** Georreferenciado.")
        lines.append(f"- **Sistema de Coordenadas (CRS):** {crs_sanitized}")
        lines.append(f"- **Coordenadas geográficas observadas ({len(diagram.geographic_coordinates)}):**")
        sorted_geo = sorted(diagram.geographic_coordinates, key=lambda g: g.id)
        for gc in sorted_geo:
            gc_id_code = sanitize_code_span(gc.id)
            raw_text_sanitized = sanitize_markdown_text(gc.raw_text)
            elev_str = f", Elevación: {gc.elevation_m} m" if gc.elevation_m is not None else ""
            pt_str = f", Punto visual asociado: `{sanitize_code_span(gc.associated_point_id)}`" if gc.associated_point_id else ""
            unc_str = ", Marcada dudosa" if gc.uncertain else ""
            alt_str = ""
            if gc.alternatives:
                alt_sanitized = [sanitize_markdown_text(a) for a in gc.alternatives]
                alt_str = f", Alternativas: [{', '.join(alt_sanitized)}]"
            lines.append(
                f'  - `{gc_id_code}`: Latitud {gc.latitude}, Longitud {gc.longitude}{elev_str} | '
                f'Texto original: "{raw_text_sanitized}" (Pág. {gc.source_page}{pt_str}{unc_str}{alt_str})'
            )

    # 3.2. Orientación declarada
    if diagram.orientation is not None and diagram.orientation.direction not in ("unknown", "none"):
        o = diagram.orientation
        dir_sanitized = sanitize_code_span(o.direction)
        deg_str = f" ({o.degrees}°)" if o.degrees is not None else ""
        raw_str = f' | Evidencia textual: "{sanitize_markdown_text(o.raw_text)}"' if o.raw_text else ""
        unc_str = " (Dudosa)" if o.uncertain else ""
        lines.append(f"- **Orientación declarada:** {dir_sanitized}{deg_str}{raw_str}{unc_str}")
    else:
        lines.append("- **Orientación:** No declarada en el documento original.")

    # 3.3. Áreas y zonas
    if diagram.areas:
        sorted_areas = sorted(diagram.areas, key=lambda a: a.id)
        lines.append(f"- **Áreas y bloques delimitados ({len(sorted_areas)}):**")
        for a in sorted_areas:
            a_id_code = sanitize_code_span(a.id)
            lbl = sanitize_markdown_text(a.label or a.raw_text or "Sin etiqueta")
            a_type = f", Tipo: {sanitize_code_span(a.area_type)}" if a.area_type else ""
            if a.bbox is not None:
                geom = f"BBox [{a.bbox.x_min:.2f}, {a.bbox.y_min:.2f}, {a.bbox.x_max:.2f}, {a.bbox.y_max:.2f}]"
            elif a.polygon is not None:
                geom = f"Polígono ({len(a.polygon)} vértices)"
            else:
                geom = "Geometría no especificada"
            unc_str = ", Dudosa" if a.uncertain else ""
            lines.append(f"  - `{a_id_code}`: {lbl} ({geom}{a_type}{unc_str})")

    # 3.4. Puntos e hitos
    if diagram.points:
        sorted_points = sorted(diagram.points, key=lambda p: p.id)
        lines.append(f"- **Puntos e hitos ({len(sorted_points)}):**")
        for p in sorted_points:
            p_id_code = sanitize_code_span(p.id)
            lbl = sanitize_markdown_text(p.label or p.raw_text or "Sin etiqueta")
            p_type = f", Tipo: {sanitize_code_span(p.point_type)}" if p.point_type else ""
            pos = f"pos_relativa: x={p.coordinate.x:.2f}, y={p.coordinate.y:.2f}"
            unc_str = ", Dudoso" if p.uncertain else ""
            lines.append(f"  - `{p_id_code}`: {lbl} ({pos}{p_type}{unc_str})")

    # 3.5. Líneas y conectores
    if diagram.lines:
        sorted_lines = sorted(diagram.lines, key=lambda l: l.id)
        lines.append(f"- **Líneas y conexiones ({len(sorted_lines)}):**")
        for l in sorted_lines:
            l_id_code = sanitize_code_span(l.id)
            lbl = sanitize_markdown_text(l.label or l.raw_text or "Sin etiqueta")
            l_type = f", Tipo: {sanitize_code_span(l.line_type)}" if l.line_type else ""
            dir_str = "dirigida" if l.directed else "no dirigida"
            conn_str = (
                f", conecta `{sanitize_code_span(l.source_point_id)}` -> `{sanitize_code_span(l.target_point_id)}`"
                if l.source_point_id and l.target_point_id
                else f", {len(l.points)} puntos"
            )
            unc_str = ", Dudosa" if l.uncertain else ""
            lines.append(f"  - `{l_id_code}`: {lbl} ({dir_str}{conn_str}{l_type}{unc_str})")

    # 3.6. Etiquetas sueltas
    if diagram.labels:
        sorted_labels = sorted(diagram.labels, key=lambda lb: lb.id)
        lines.append(f"- **Etiquetas y textos legibles ({len(sorted_labels)}):**")
        for lb in sorted_labels:
            lb_id_code = sanitize_code_span(lb.id)
            lb_text_sanitized = sanitize_markdown_text(lb.text)
            att_str = f" (asociada a `{sanitize_code_span(lb.attached_to_id)}`)" if lb.attached_to_id else ""
            pos_str = f" en [x={lb.position.x:.2f}, y={lb.position.y:.2f}]" if lb.position else ""
            unc_str = ", Dudosa" if lb.uncertain else ""
            lines.append(f'  - `{lb_id_code}`: "{lb_text_sanitized}"{att_str}{pos_str}{unc_str}')

    # 3.7. Relaciones explícitas
    if diagram.relations:
        sorted_relations = sorted(diagram.relations, key=lambda r: (r.source_id, r.target_id, r.id))
        lines.append(f"- **Relaciones explícitas ({len(sorted_relations)}):**")
        for r in sorted_relations:
            r_id_code = sanitize_code_span(r.id)
            src_code = sanitize_code_span(r.source_id)
            tgt_code = sanitize_code_span(r.target_id)
            rel_type_sanitized = sanitize_markdown_text(r.relation_type)
            lbl_str = f' ("{sanitize_markdown_text(r.label)}")' if r.label else ""
            dir_str = "->" if r.directed else "--"
            unc_str = " [Dudosa]" if r.uncertain else ""
            lines.append(f"  - `{r_id_code}`: `{src_code}` {dir_str} `{tgt_code}` | Tipo: '{rel_type_sanitized}'{lbl_str}{unc_str}")

    # 3.8. Advertencias de extracción
    if diagram.warnings:
        lines.append("- **Advertencias:**")
        for w in diagram.warnings:
            code_sanitized = sanitize_code_span(w.code)
            msg_sanitized = sanitize_markdown_text(w.message)
            lines.append(f"  - [{code_sanitized}] {msg_sanitized} (Pág. {w.source_page})")

    return "\n".join(lines) + "\n"