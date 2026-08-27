import re
import xml.etree.ElementTree as ET
from typing import Optional, List
from xml.sax.saxutils import escape as xml_escape

from ..schemas.diagram import DiagramIR
from .validation import validate_diagram_ir

# Control characters removal for XML (excluding newline/tab/carriage return)
_XML_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def escape_xml(text: Optional[str]) -> str:
    """Escapa de forma estricta texto y valores de atributos para XML/SVG."""
    if text is None:
        return ""
    s = _XML_CONTROL_CHARS_RE.sub("", str(text))
    # Escapar entidades estándar (&, <, >, ", ')
    return xml_escape(s, entities={'"': "&quot;", "'": "&apos;"})


def render_svg(diagram: DiagramIR) -> str:
    """Renderiza de forma pura, determinista y segura un DiagramIR de tipo 'field_sketch' o 'gps_sketch' a SVG.

    Garantías arquitectónicas y de seguridad:
    1. Exclusivo para diagram_type in ('field_sketch', 'gps_sketch'). Rechaza otros tipos con ValueError.
    2. Salida XML válida, estricta y parseable por xml.etree.ElementTree.
    3. viewBox fijo '0 0 1000 1000' con escalado lineal de coordenadas relativas [0.0, 1.0] a [0, 1000].
    4. Prohibición estricta de contenido activo: NUNCA emite <script>, <foreignObject>, <a>, <image>, xlink:href ni URLs.
    5. Identificadores de elementos SVG internos, deterministas y seguros (area_0, point_0, line_0, label_0).
    6. Indicador de orientación (rosa de los vientos) fiel: solo se dibuja si existe orientación explícita declarada.
    7. Salida 100% determinista byte a byte.
    """
    if not isinstance(diagram, DiagramIR):
        raise TypeError(f"Se esperaba una instancia de DiagramIR, recibido: {type(diagram).__name__}")

    if diagram.diagram_type not in ("field_sketch", "gps_sketch"):
        raise ValueError(
            f"render_svg solo admite diagramas de tipo 'field_sketch' o 'gps_sketch', recibido: '{diagram.diagram_type}'"
        )

    # Validar diagrama antes de renderizar
    validate_diagram_ir(diagram, raise_on_error=True)

    lines: list[str] = []

    # 1. Cabecera SVG con viewBox fijo de 1000x1000
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" width="1000" height="1000">')

    # 2. Definiciones de estilos y marcadores (flechas para líneas dirigidas)
    lines.append("  <defs>")
    lines.append("    <style>")
    lines.append("      .bg { fill: #fcfcfc; }")
    lines.append("      .grid { stroke: #eaeaea; stroke-width: 1; stroke-dasharray: 4,4; }")
    lines.append("      .border { fill: none; stroke: #b0b0b0; stroke-width: 2; }")
    lines.append("      .area { fill: #e8f4f8; stroke: #2b7a78; stroke-width: 2; fill-opacity: 0.6; }")
    lines.append("      .area-polygon { fill: #f0f4e8; stroke: #5b8a2b; stroke-width: 2; fill-opacity: 0.6; }")
    lines.append("      .line { fill: none; stroke: #17252a; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }")
    lines.append("      .line-stream { fill: none; stroke: #3a86ff; stroke-width: 2.5; stroke-dasharray: 6,3; }")
    lines.append("      .line-boundary { fill: none; stroke: #8338ec; stroke-width: 2; stroke-dasharray: 4,2; }")
    lines.append("      .point-sample { fill: #e63946; stroke: #ffffff; stroke-width: 2; }")
    lines.append("      .point-tree { fill: #2a9d8f; stroke: #ffffff; stroke-width: 2; }")
    lines.append("      .point-vertex { fill: #7209b7; stroke: #ffffff; stroke-width: 2; }")
    lines.append("      .point-default { fill: #f4a261; stroke: #ffffff; stroke-width: 2; }")
    lines.append("      .text-title { font-family: sans-serif; font-size: 20px; font-weight: bold; fill: #1f2937; }")
    lines.append("      .text-meta { font-family: sans-serif; font-size: 13px; fill: #6b7280; }")
    lines.append("      .text-entity { font-family: sans-serif; font-size: 13px; font-weight: 500; fill: #111827; }")
    lines.append("      .text-area { font-family: sans-serif; font-size: 14px; font-weight: bold; fill: #2b7a78; text-anchor: middle; }")
    lines.append("      .compass-needle-n { fill: #e63946; stroke: #b71c1c; stroke-width: 1; }")
    lines.append("      .compass-needle-s { fill: #9e9e9e; stroke: #616161; stroke-width: 1; }")
    lines.append("      .compass-text { font-family: sans-serif; font-size: 14px; font-weight: bold; fill: #111827; text-anchor: middle; }")
    lines.append("    </style>")
    lines.append('    <marker id="marker_arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">')
    lines.append('      <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#17252a"/>')
    lines.append("    </marker>")
    lines.append('    <marker id="marker_arrow_stream" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">')
    lines.append('      <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#3a86ff"/>')
    lines.append("    </marker>")
    lines.append("  </defs>")

    # 3. Fondo, cuadrícula de referencia tenue y borde exterior
    lines.append('  <rect width="1000" height="1000" class="bg"/>')
    for g in range(100, 1000, 100):
        lines.append(f'  <line x1="{g}" y1="0" x2="{g}" y2="1000" class="grid"/>')
        lines.append(f'  <line x1="0" y1="{g}" x2="1000" y2="{g}" class="grid"/>')
    lines.append('  <rect x="5" y="5" width="990" height="990" class="border"/>')

    # 4. Metadatos visuales (Título, página y tipo)
    clean_title = escape_xml(diagram.title) if diagram.title else ("Croquis de campo" if diagram.diagram_type == "field_sketch" else "Esquema GPS")
    lines.append(f'  <text x="25" y="40" class="text-title">{clean_title}</text>')
    meta_info = f"Pág. {diagram.source_page} | {diagram.diagram_type}"
    if diagram.georeferenced and diagram.crs:
        meta_info += f" | Georreferenciado | CRS: {escape_xml(diagram.crs)}"
    lines.append(f'  <text x="25" y="62" class="text-meta">{meta_info}</text>')

    # 5. Indicador de orientación (Rosa de los vientos) si está declarada
    if diagram.orientation is not None and diagram.orientation.direction not in ("unknown", "none"):
        orient = diagram.orientation
        rot_deg = 0.0
        if orient.direction == "north_up":
            rot_deg = 0.0
        elif orient.direction == "south_up":
            rot_deg = 180.0
        elif orient.direction == "east_up":
            rot_deg = 90.0
        elif orient.direction == "west_up":
            rot_deg = 270.0
        elif orient.direction == "rotated" and orient.degrees is not None:
            rot_deg = float(orient.degrees)

        lines.append('  <!-- Indicador de orientación (Rosa de los Vientos) -->')
        lines.append('  <g id="orientation_indicator" transform="translate(930, 70)">')
        lines.append(f'    <g transform="rotate({rot_deg:.1f})">')
        lines.append('      <polygon points="0,-35 8,0 0,-5" class="compass-needle-n"/>')
        lines.append('      <polygon points="0,35 8,0 0,5" class="compass-needle-s"/>')
        lines.append('      <polygon points="0,-35 -8,0 0,-5" class="compass-needle-n"/>')
        lines.append('      <polygon points="0,35 -8,0 0,5" class="compass-needle-s"/>')
        lines.append("    </g>")
        lines.append('    <circle cx="0" cy="0" r="3" fill="#111827"/>')
        lines.append('    <text x="0" y="-40" class="compass-text">N</text>')
        lines.append("  </g>")

    # 6. Áreas (Rectángulos BBox y Polígonos)
    sorted_areas = sorted(diagram.areas, key=lambda a: a.id)
    for idx, area in enumerate(sorted_areas):
        elem_id = f"area_{idx}"
        lbl = escape_xml(area.label or area.raw_text or area.id)
        if area.bbox is not None:
            x = area.bbox.x_min * 1000.0
            y = area.bbox.y_min * 1000.0
            w = (area.bbox.x_max - area.bbox.x_min) * 1000.0
            h = (area.bbox.y_max - area.bbox.y_min) * 1000.0
            lines.append(
                f'  <rect id="{elem_id}" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" class="area"/>'
            )
            # Etiqueta en el centro del área
            cx = x + (w / 2.0)
            cy = y + (h / 2.0)
            lines.append(f'  <text x="{cx:.2f}" y="{cy:.2f}" class="text-area">{lbl}</text>')
        elif area.polygon is not None:
            pts_str = " ".join([f"{p.x * 1000.0:.2f},{p.y * 1000.0:.2f}" for p in area.polygon])
            lines.append(f'  <polygon id="{elem_id}" points="{pts_str}" class="area-polygon"/>')
            # Centroide aproximado
            cx = (sum(p.x for p in area.polygon) / len(area.polygon)) * 1000.0
            cy = (sum(p.y for p in area.polygon) / len(area.polygon)) * 1000.0
            lines.append(f'  <text x="{cx:.2f}" y="{cy:.2f}" class="text-area">{lbl}</text>')

    # 7. Líneas y polilíneas
    sorted_lines = sorted(diagram.lines, key=lambda l: l.id)
    for idx, line_ent in enumerate(sorted_lines):
        elem_id = f"line_{idx}"
        l_type = (line_ent.line_type or "").lower()
        if l_type in ("stream", "water", "river", "flow"):
            css_class = "line-stream"
            marker = ' marker-end="url(#marker_arrow_stream)"' if line_ent.directed else ""
        elif l_type in ("boundary", "fence", "limit"):
            css_class = "line-boundary"
            marker = ' marker-end="url(#marker_arrow)"' if line_ent.directed else ""
        else:
            css_class = "line"
            marker = ' marker-end="url(#marker_arrow)"' if line_ent.directed else ""

        if len(line_ent.points) == 2:
            p1, p2 = line_ent.points[0], line_ent.points[1]
            lines.append(
                f'  <line id="{elem_id}" x1="{p1.x * 1000.0:.2f}" y1="{p1.y * 1000.0:.2f}" '
                f'x2="{p2.x * 1000.0:.2f}" y2="{p2.y * 1000.0:.2f}" class="{css_class}"{marker}/>'
            )
        else:
            pts_str = " ".join([f"{p.x * 1000.0:.2f},{p.y * 1000.0:.2f}" for p in line_ent.points])
            lines.append(f'  <polyline id="{elem_id}" points="{pts_str}" class="{css_class}"{marker}/>')

        if line_ent.label:
            mid_p = line_ent.points[len(line_ent.points) // 2]
            lbl = escape_xml(line_ent.label)
            lines.append(f'  <text x="{mid_p.x * 1000.0 + 5:.2f}" y="{mid_p.y * 1000.0 - 5:.2f}" class="text-entity">{lbl}</text>')

    # 8. Puntos e hitos
    sorted_points = sorted(diagram.points, key=lambda p: p.id)
    for idx, pt in enumerate(sorted_points):
        elem_id = f"point_{idx}"
        px = pt.coordinate.x * 1000.0
        py = pt.coordinate.y * 1000.0

        p_type = (pt.point_type or "").lower()
        if p_type in ("sample", "sample_point", "muestra"):
            css_class = "point-sample"
            r = 7.0
        elif p_type in ("tree", "arbol", "planta", "vegetation"):
            css_class = "point-tree"
            r = 8.0
        elif p_type in ("vertex", "vertice", "corner", "hito"):
            css_class = "point-vertex"
            r = 6.0
        else:
            css_class = "point-default"
            r = 6.0

        lines.append(f'  <circle id="{elem_id}" cx="{px:.2f}" cy="{py:.2f}" r="{r:.1f}" class="{css_class}"/>')

        lbl = escape_xml(pt.label or pt.raw_text or pt.id)
        lines.append(f'  <text x="{px + 10:.2f}" y="{py + 4:.2f}" class="text-entity">{lbl}</text>')

    # 9. Etiquetas sueltas
    sorted_labels = sorted(diagram.labels, key=lambda lb: lb.id)
    for idx, lb in enumerate(sorted_labels):
        elem_id = f"label_{idx}"
        if lb.position is not None:
            lx = lb.position.x * 1000.0
            ly = lb.position.y * 1000.0
        else:
            lx = 50.0 + (idx * 20.0)
            ly = 900.0
        lbl = escape_xml(lb.text)
        lines.append(f'  <text id="{elem_id}" x="{lx:.2f}" y="{ly:.2f}" class="text-entity">{lbl}</text>')

    # 10. Conexiones visuales de relaciones explícitas (si no tienen línea directa)
    sorted_relations = sorted(diagram.relations, key=lambda r: (r.source_id, r.target_id, r.id))
    pt_map = {p.id: p.coordinate for p in diagram.points}
    for rel in sorted_relations:
        if rel.source_id in pt_map and rel.target_id in pt_map:
            p1 = pt_map[rel.source_id]
            p2 = pt_map[rel.target_id]
            marker = ' marker-end="url(#marker_arrow)"' if rel.directed else ""
            lines.append(
                f'  <line x1="{p1.x * 1000.0:.2f}" y1="{p1.y * 1000.0:.2f}" '
                f'x2="{p2.x * 1000.0:.2f}" y2="{p2.y * 1000.0:.2f}" '
                f'stroke="#4b5563" stroke-width="1.5" stroke-dasharray="3,3"{marker}/>'
            )

    lines.append("</svg>")
    svg_output = "\n".join(lines) + "\n"

    # 11. Validación estricta de conformidad XML
    try:
        ET.fromstring(svg_output)
    except ET.ParseError as exc:
        raise ValueError(f"Fallo de generación interna: el SVG generado no es XML válido: {exc}") from exc

    return svg_output