import re
from typing import Optional, Dict, List
from ..schemas.diagram import DiagramIR, AreaEntity, PointEntity, LineEntity, LabelEntity, RelationEntity
from .validation import validate_diagram_ir


def sanitize_mermaid_label(text: Optional[str]) -> str:
    """Sanea de forma exhaustiva una etiqueta o texto para su inclusión segura en Mermaid.

    Garantías de seguridad:
    1. Reemplaza saltos de línea (\r\n, \r, \n) por espacios para evitar romper la estructura de líneas de Mermaid.
    2. Neutraliza punto y coma antes de escapar entidades.
    3. Reemplaza comillas dobles (") por comillas simples (') y barras invertidas.
    4. Escapa caracteres especiales de HTML (<, >, &) para prevenir inyecciones.
    5. Reemplaza corchetes ([ ]), llaves ({ }), paréntesis (( )) y pipes (|) por entidades HTML válidas.
    6. Neutraliza palabras clave estructurales (subgraph).
    """
    if text is None:
        return ""

    s = str(text)
    # 1. Normalizar y eliminar saltos de línea y retornos de carro
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

    # 2. Neutralizar punto y coma antes de la codificación de entidades
    s = s.replace(";", ",")

    # 3. Neutralizar comillas dobles y caracteres de escape
    s = s.replace('"', "'").replace("`", "'").replace("\\", "/")

    # 4. Escapar entidades HTML básicas para neutralizar scripts/tags
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 5. Neutralizar delimitadores de nodos de Mermaid con entidades numéricas válidas
    s = s.replace("[", "&#91;").replace("]", "&#93;")
    s = s.replace("{", "&#123;").replace("}", "&#125;")
    s = s.replace("(", "&#40;").replace(")", "&#41;")
    s = s.replace("|", "&#124;")

    # 6. Neutralizar palabras clave estructurales de Mermaid
    s = re.sub(r"(?i)\bsubgraph\b", "sub_graph", s)

    # 7. Colapsar espacios múltiples y recortar
    s = re.sub(r"\s+", " ", s).strip()
    return s


def sanitize_mermaid_title(title: Optional[str]) -> str:
    """Sanea el título para la cabecera YAML/Frontmatter de Mermaid."""
    if not title:
        return ""
    t = str(title).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    t = t.replace('"', "'").replace(":", " -")
    t = re.sub(r"(?i)\bsubgraph\b", "sub_graph", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def render_mermaid(diagram: DiagramIR) -> str:
    """Renderiza de forma pura, determinista y segura un DiagramIR de tipo 'flowchart' a código Mermaid.

    Reglas y restricciones:
    - Exclusivo para diagram_type == 'flowchart'. Rechaza otros tipos con ValueError.
    - Genera identificadores de nodo internos deterministas e independientes de texto del documento (n0, n1, ...).
    - Neutraliza todas las etiquetas contra inyecciones de sintaxis de Mermaid y scripts.
    - Resultado 100% determinista byte a byte (orden estable, saltos \n uniformes).
    """
    if not isinstance(diagram, DiagramIR):
        raise TypeError(f"Se esperaba una instancia de DiagramIR, recibido: {type(diagram).__name__}")

    if diagram.diagram_type != "flowchart":
        raise ValueError(
            f"render_mermaid solo admite diagramas de tipo 'flowchart', recibido: '{diagram.diagram_type}'"
        )

    # Validar diagrama antes de renderizar
    validate_diagram_ir(diagram, raise_on_error=True)

    # 1. Mapeo determinista de entidades a identificadores internos seguros (n0, n1, n2, ...)
    node_id_map: dict[str, str] = {}
    node_entities: list[tuple[str, str, str, str]] = []  # (canonical_id, internal_id, shape_type, label)

    # Recolectar áreas (bloques de proceso/decisión)
    sorted_areas = sorted(diagram.areas, key=lambda a: a.id)
    for area in sorted_areas:
        internal_id = f"n{len(node_id_map)}"
        node_id_map[area.id] = internal_id

        # Determinar forma según area_type
        a_type = (area.area_type or "").lower()
        if a_type in ("decision", "condition", "branch"):
            shape = "decision"
        elif a_type in ("start", "end", "terminal"):
            shape = "stadium"
        elif a_type in ("event", "node", "circle"):
            shape = "rounded"
        else:
            shape = "rect"

        raw_lbl = area.label if area.label is not None and area.label.strip() else (area.raw_text or area.id)
        lbl = sanitize_mermaid_label(raw_lbl)
        if not lbl:
            lbl = f"Area {area.id}"
        node_entities.append((area.id, internal_id, shape, lbl))

    # Recolectar puntos (nodos/vértices/eventos)
    sorted_points = sorted(diagram.points, key=lambda p: p.id)
    for point in sorted_points:
        if point.id in node_id_map:
            continue
        internal_id = f"n{len(node_id_map)}"
        node_id_map[point.id] = internal_id

        p_type = (point.point_type or "").lower()
        if p_type in ("decision", "condition", "branch"):
            shape = "decision"
        elif p_type in ("start", "end", "terminal", "start_point", "end_point"):
            shape = "stadium"
        elif p_type in ("event", "node", "vertex", "circle"):
            shape = "rounded"
        else:
            shape = "rect"

        raw_lbl = point.label if point.label is not None and point.label.strip() else (point.raw_text or point.id)
        lbl = sanitize_mermaid_label(raw_lbl)
        if not lbl:
            lbl = f"Point {point.id}"
        node_entities.append((point.id, internal_id, shape, lbl))

    # Recolectar etiquetas si alguna relación las usa como origen o destino
    for rel in diagram.relations:
        for ent_id in (rel.source_id, rel.target_id):
            if ent_id not in node_id_map:
                internal_id = f"n{len(node_id_map)}"
                node_id_map[ent_id] = internal_id
                node_entities.append((ent_id, internal_id, "rect", sanitize_mermaid_label(ent_id)))

    lines: list[str] = []

    # 2. Frontmatter de título si existe
    clean_title = sanitize_mermaid_title(diagram.title)
    if clean_title:
        lines.append("---")
        lines.append(f"title: {clean_title}")
        lines.append("---")

    # 3. Declaración de diagrama de flujo
    lines.append("flowchart TD")

    # 4. Declaración de nodos con sus formas y etiquetas saneadas
    for _, internal_id, shape, lbl in node_entities:
        if shape == "decision":
            node_decl = f'    {internal_id}{{"{lbl}"}}'
        elif shape == "stadium":
            node_decl = f'    {internal_id}(["{lbl}"])'
        elif shape == "rounded":
            node_decl = f'    {internal_id}("{lbl}")'
        else:
            node_decl = f'    {internal_id}["{lbl}"]'
        lines.append(node_decl)

    # 5. Declaración de relaciones (edges)
    sorted_relations = sorted(diagram.relations, key=lambda r: (r.source_id, r.target_id, r.id))
    for rel in sorted_relations:
        src = node_id_map.get(rel.source_id)
        tgt = node_id_map.get(rel.target_id)
        if not src or not tgt:
            continue

        rel_lbl = rel.label if rel.label is not None and rel.label.strip() else rel.raw_text
        sanitized_rel_lbl = sanitize_mermaid_label(rel_lbl)

        if rel.directed:
            if sanitized_rel_lbl:
                edge = f"    {src} -->|{sanitized_rel_lbl}| {tgt}"
            else:
                edge = f"    {src} --> {tgt}"
        else:
            if sanitized_rel_lbl:
                edge = f"    {src} ---|{sanitized_rel_lbl}| {tgt}"
            else:
                edge = f"    {src} --- {tgt}"
        lines.append(edge)

    # 6. Conexiones explícitas desde LineEntity si conectan puntos
    sorted_lines = sorted(diagram.lines, key=lambda l: (l.source_point_id or "", l.target_point_id or "", l.id))
    for line_ent in sorted_lines:
        if line_ent.source_point_id and line_ent.target_point_id:
            src = node_id_map.get(line_ent.source_point_id)
            tgt = node_id_map.get(line_ent.target_point_id)
            if src and tgt:
                line_lbl = line_ent.label if line_ent.label is not None and line_ent.label.strip() else line_ent.raw_text
                sanitized_line_lbl = sanitize_mermaid_label(line_lbl)
                if line_ent.directed:
                    if sanitized_line_lbl:
                        edge = f"    {src} -->|{sanitized_line_lbl}| {tgt}"
                    else:
                        edge = f"    {src} --> {tgt}"
                else:
                    if sanitized_line_lbl:
                        edge = f"    {src} ---|{sanitized_line_lbl}| {tgt}"
                    else:
                        edge = f"    {src} --- {tgt}"
                lines.append(edge)

    return "\n".join(lines) + "\n"