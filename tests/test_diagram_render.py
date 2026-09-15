import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.fieldnotes.schemas.diagram import (
    Point2D,
    BoundingBox2D,
    GeographicCoordinate,
    DiagramOrientation,
    PointEntity,
    LineEntity,
    AreaEntity,
    LabelEntity,
    RelationEntity,
    DiagramIR,
    DiagramDTO,
    dto_to_diagram_ir,
)
from src.fieldnotes.schemas.warnings import ExtractionWarning
from src.fieldnotes.diagrams.validation import (
    DiagramValidationError,
    DiagramDuplicateIdError,
    DiagramReferenceError,
    DiagramGeoreferenceError,
    DiagramGeometryError,
    validate_diagram_ir,
)
from src.fieldnotes.diagrams.render_mermaid import (
    sanitize_mermaid_label,
    sanitize_mermaid_title,
    render_mermaid,
)
from src.fieldnotes.diagrams.render_svg import (
    escape_xml,
    render_svg,
)
from src.fieldnotes.diagrams.render_markdown import (
    sanitize_markdown_text,
    sanitize_code_span,
    sanitize_alt_text,
    validate_and_sanitize_asset_path,
    get_diagram_type_label,
    render_markdown,
)
from src.fieldnotes.diagrams.rendering import (
    DiagramRollbackError,
    DiagramRenderResult,
    render_diagram,
    publish_rendered_diagram,
)
from src.fieldnotes.pipeline import UnlimitedOCRAgent


def create_sample_png_bytes() -> bytes:
    """Crea una cabecera PNG válida de 1x1 píxel para pruebas sin dependencias externas."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class TestDiagramRenderMermaid(unittest.TestCase):
    """Pruebas exhaustivas para el renderizador Mermaid de flowcharts."""

    def setUp(self):
        self.flowchart = DiagramIR(
            schema_version=1,
            diagram_type="flowchart",
            source_page=1,
            title="Protocolo de Muestreo",
            description="Flujo de toma de decisiones en campo",
            areas=[
                AreaEntity(
                    id="step_1",
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.3),
                    area_type="start",
                    label="Inicio de muestreo",
                ),
                AreaEntity(
                    id="step_2",
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.4, x_max=0.4, y_max=0.6),
                    area_type="decision",
                    label="¿Presencia de plaga?",
                ),
                AreaEntity(
                    id="step_3",
                    bbox=BoundingBox2D(x_min=0.5, y_min=0.4, x_max=0.8, y_max=0.6),
                    area_type="process",
                    label="Aplicar tratamiento A",
                ),
                AreaEntity(
                    id="step_4",
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.7, x_max=0.4, y_max=0.9),
                    area_type="end",
                    label="Fin de protocolo",
                ),
            ],
            relations=[
                RelationEntity(
                    id="rel_1",
                    source_id="step_1",
                    target_id="step_2",
                    relation_type="flows_to",
                    directed=True,
                ),
                RelationEntity(
                    id="rel_2",
                    source_id="step_2",
                    target_id="step_3",
                    relation_type="flows_to",
                    directed=True,
                    label="Sí",
                ),
                RelationEntity(
                    id="rel_3",
                    source_id="step_2",
                    target_id="step_4",
                    relation_type="flows_to",
                    directed=True,
                    label="No",
                ),
            ],
        )

    def test_routing_rejects_non_flowchart(self):
        sketch = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            areas=[AreaEntity(id="a1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5))],
        )
        with self.assertRaises(ValueError) as ctx:
            render_mermaid(sketch)
        self.assertIn("solo admite diagramas de tipo 'flowchart'", str(ctx.exception))

    def test_routing_rejects_non_diagram_ir(self):
        with self.assertRaises(TypeError):
            render_mermaid("no es un diagrama")  # type: ignore

    def test_render_mermaid_valid_structure(self):
        mermaid_out = render_mermaid(self.flowchart)
        self.assertIn("---", mermaid_out)
        self.assertIn("title: Protocolo de Muestreo", mermaid_out)
        self.assertIn("flowchart TD", mermaid_out)
        self.assertIn('(["Inicio de muestreo"])', mermaid_out)
        self.assertIn('{"¿Presencia de plaga?"}', mermaid_out)
        self.assertIn('["Aplicar tratamiento A"]', mermaid_out)
        self.assertIn('(["Fin de protocolo"])', mermaid_out)
        self.assertIn("-->", mermaid_out)
        self.assertIn("-->|Sí|", mermaid_out)
        self.assertIn("-->|No|", mermaid_out)

    def test_mermaid_deterministic_byte_for_byte(self):
        out1 = render_mermaid(self.flowchart)
        out2 = render_mermaid(self.flowchart)
        out3 = render_mermaid(self.flowchart)
        self.assertEqual(out1, out2)
        self.assertEqual(out2, out3)
        self.assertEqual(out1.encode("utf-8"), out2.encode("utf-8"))

    def test_mermaid_hostile_labels_and_injections(self):
        hostile_diagram = DiagramIR(
            schema_version=1,
            diagram_type="flowchart",
            source_page=1,
            title='Título" con : caracteres `raros`\ny salto',
            areas=[
                AreaEntity(
                    id="bad_1",
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.3),
                    label='Nodo A"]\nnode_injected["Injected Node',
                ),
                AreaEntity(
                    id="bad_2",
                    bbox=BoundingBox2D(x_min=0.5, y_min=0.1, x_max=0.8, y_max=0.3),
                    label='<script>alert("XSS")</script>',
                ),
                AreaEntity(
                    id="bad_3",
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.5, x_max=0.4, y_max=0.8),
                    label='Node C; subgraph Evil; D["Hacked"]; end',
                ),
            ],
            relations=[
                RelationEntity(
                    id="rel_bad",
                    source_id="bad_1",
                    target_id="bad_2",
                    relation_type="flows_to",
                    label='edge|label|with; pipes\nand "quotes"',
                ),
            ],
        )

        rendered = render_mermaid(hostile_diagram)
        for line in rendered.splitlines():
            if line.startswith("    n") and "-->" not in line and "---" not in line:
                self.assertTrue(line.endswith('"]') or line.endswith('")') or line.endswith('}') or line.endswith(')'))
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn('node_injected["', rendered)
        self.assertNotIn("subgraph Evil", rendered)

    def test_mermaid_undirected_relations_and_lines(self):
        fc = DiagramIR(
            schema_version=1,
            diagram_type="flowchart",
            source_page=1,
            points=[
                PointEntity(id="p1", coordinate=Point2D(x=0.1, y=0.1), label="Punto 1", point_type="circle"),
                PointEntity(id="p2", coordinate=Point2D(x=0.9, y=0.9), label="Punto 2", point_type="vertex"),
            ],
            lines=[
                LineEntity(
                    id="l1",
                    points=[Point2D(x=0.1, y=0.1), Point2D(x=0.9, y=0.9)],
                    source_point_id="p1",
                    target_point_id="p2",
                    directed=False,
                    label="Conector no dirigido",
                ),
            ],
            relations=[
                RelationEntity(
                    id="r_undir",
                    source_id="p1",
                    target_id="p2",
                    relation_type="adjacent_to",
                    directed=False,
                    label="Adyacente",
                ),
            ],
        )
        rendered = render_mermaid(fc)
        self.assertIn("---|Conector no dirigido|", rendered)
        self.assertIn("---|Adyacente|", rendered)

    def test_mermaid_fallback_to_raw_text_or_id_when_label_none(self):
        fc = DiagramIR(
            schema_version=1,
            diagram_type="flowchart",
            source_page=1,
            areas=[
                AreaEntity(id="a_raw", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4), raw_text="Texto Raw"),
                AreaEntity(id="a_none", bbox=BoundingBox2D(x_min=0.6, y_min=0.1, x_max=0.9, y_max=0.4)),
            ],
        )
        rendered = render_mermaid(fc)
        self.assertIn('["Texto Raw"]', rendered)
        self.assertIn('["a_none"]', rendered)


class TestDiagramRenderSVG(unittest.TestCase):
    """Pruebas exhaustivas para el renderizador SVG de croquis de campo y esquemas GPS."""

    def setUp(self):
        self.field_sketch = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=2,
            title="Croquis de la Parcela Norte",
            description="Distribución de subparcelas y puntos de muestreo",
            orientation=DiagramOrientation(direction="north_up", raw_text="Norte hacia arriba"),
            areas=[
                AreaEntity(
                    id="plot_A",
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.45, y_max=0.45),
                    area_type="plot",
                    label="Subparcela A",
                ),
                AreaEntity(
                    id="plot_B",
                    polygon=[
                        Point2D(x=0.55, y=0.1),
                        Point2D(x=0.9, y=0.1),
                        Point2D(x=0.9, y=0.45),
                        Point2D(x=0.55, y=0.45),
                    ],
                    area_type="plot",
                    label="Subparcela B (Polígono)",
                ),
            ],
            points=[
                PointEntity(
                    id="P1",
                    coordinate=Point2D(x=0.25, y=0.25),
                    point_type="sample_point",
                    label="Muestra P1",
                ),
                PointEntity(
                    id="T1",
                    coordinate=Point2D(x=0.75, y=0.25),
                    point_type="tree",
                    label="Árbol T1",
                ),
                PointEntity(
                    id="V1",
                    coordinate=Point2D(x=0.5, y=0.8),
                    point_type="vertex",
                    label="Vértice V1",
                ),
            ],
            lines=[
                LineEntity(
                    id="L1",
                    points=[Point2D(x=0.1, y=0.5), Point2D(x=0.9, y=0.5)],
                    line_type="boundary",
                    label="Camino central",
                ),
                LineEntity(
                    id="L2",
                    points=[Point2D(x=0.25, y=0.25), Point2D(x=0.5, y=0.35), Point2D(x=0.75, y=0.25)],
                    line_type="stream",
                    directed=True,
                    label="Flujo de riego",
                ),
            ],
            labels=[
                LabelEntity(
                    id="lbl_1",
                    text="Zona de control",
                    position=Point2D(x=0.5, y=0.95),
                ),
            ],
        )

        self.gps_sketch = DiagramIR(
            schema_version=1,
            diagram_type="gps_sketch",
            source_page=3,
            title="Esquema de Vértices GPS",
            georeferenced=True,
            crs="EPSG:4326",
            geographic_coordinates=[
                GeographicCoordinate(
                    id="gps_1",
                    latitude=40.4168,
                    longitude=-3.7038,
                    raw_text="40.4168N, 3.7038W",
                    source_page=3,
                    associated_point_id="GPS_P1",
                ),
            ],
            points=[
                PointEntity(
                    id="GPS_P1",
                    coordinate=Point2D(x=0.3, y=0.3),
                    point_type="vertex",
                    label="Vértice Principal GPS",
                ),
            ],
            orientation=DiagramOrientation(
                direction="rotated",
                degrees=45.0,
                raw_text="Rotación 45 deg",
            ),
        )

    def test_routing_rejects_flowchart(self):
        fc = DiagramIR(
            schema_version=1,
            diagram_type="flowchart",
            source_page=1,
            areas=[AreaEntity(id="a1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5))],
        )
        with self.assertRaises(ValueError) as ctx:
            render_svg(fc)
        self.assertIn("solo admite diagramas de tipo 'field_sketch' o 'gps_sketch'", str(ctx.exception))

    def test_routing_rejects_non_diagram_ir(self):
        with self.assertRaises(TypeError):
            render_svg(123)  # type: ignore

    def test_svg_is_valid_xml(self):
        svg_text = render_svg(self.field_sketch)
        root = ET.fromstring(svg_text)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(root.attrib["viewBox"], "0 0 1000 1000")
        self.assertEqual(root.attrib["width"], "1000")
        self.assertEqual(root.attrib["height"], "1000")

    def test_svg_deterministic_byte_for_byte(self):
        out1 = render_svg(self.field_sketch)
        out2 = render_svg(self.field_sketch)
        self.assertEqual(out1, out2)
        self.assertEqual(out1.encode("utf-8"), out2.encode("utf-8"))

    def test_svg_geometry_scaling(self):
        svg_text = render_svg(self.field_sketch)
        self.assertIn('x="100.00"', svg_text)
        self.assertIn('y="100.00"', svg_text)
        self.assertIn('width="350.00"', svg_text)
        self.assertIn('height="350.00"', svg_text)

        self.assertIn("550.00,100.00", svg_text)
        self.assertIn("900.00,100.00", svg_text)

        self.assertIn('cx="250.00" cy="250.00"', svg_text)

    def test_svg_security_hostile_xml_escaping(self):
        hostile = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            title='Croquis <script>alert("XSS")</script> & "Malicioso"',
            areas=[
                AreaEntity(
                    id="a1",
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5),
                    label='</text><foreignObject><script>evil()</script></foreignObject><text>',
                ),
            ],
            points=[
                PointEntity(
                    id="p1",
                    coordinate=Point2D(x=0.2, y=0.2),
                    label='Punto <a href="javascript:alert(1)">Click</a> & \'quotes\'',
                ),
            ],
        )

        svg_out = render_svg(hostile)
        root = ET.fromstring(svg_out)
        self.assertIsNotNone(root)

        self.assertNotIn("<script>", svg_out)
        self.assertNotIn("<foreignObject>", svg_out)
        self.assertNotIn("<a href=", svg_out)
        self.assertIn("&lt;script&gt;", svg_out)
        self.assertIn("&lt;foreignObject&gt;", svg_out)

    def test_svg_internal_ids_immune_to_hostile_source_ids(self):
        hostile_ids = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            areas=[
                AreaEntity(
                    id='bad_id_1" bad_attr="injected" x="0',
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4),
                    label="Area con ID hostil",
                ),
            ],
            points=[
                PointEntity(
                    id='bad_pt_1<script>',
                    coordinate=Point2D(x=0.5, y=0.5),
                    label="Punto con ID hostil",
                ),
            ],
            lines=[
                LineEntity(
                    id='bad_line" > <foreignObject>',
                    points=[Point2D(x=0.1, y=0.1), Point2D(x=0.9, y=0.9)],
                ),
            ],
            labels=[
                LabelEntity(
                    id='bad_lbl 123',
                    text="Etiqueta con ID hostil",
                ),
            ],
        )

        svg_out = render_svg(hostile_ids)
        root = ET.fromstring(svg_out)
        self.assertIsNotNone(root)

        self.assertIn('id="area_0"', svg_out)
        self.assertIn('id="point_0"', svg_out)
        self.assertIn('id="line_0"', svg_out)
        self.assertIn('id="label_0"', svg_out)
        self.assertNotIn('bad_attr="injected"', svg_out)

    def test_svg_orientation_variations(self):
        directions = [
            ("north_up", "rotate(0.0)"),
            ("south_up", "rotate(180.0)"),
            ("east_up", "rotate(90.0)"),
            ("west_up", "rotate(270.0)"),
        ]
        for dir_name, expected_rotate in directions:
            diag = DiagramIR(
                schema_version=1,
                diagram_type="field_sketch",
                source_page=1,
                areas=[AreaEntity(id="a1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5))],
                orientation=DiagramOrientation(direction=dir_name, raw_text=f"Orient {dir_name}"),
            )
            svg_text = render_svg(diag)
            self.assertIn(expected_rotate, svg_text)
            self.assertIn("orientation_indicator", svg_text)

    def test_svg_no_orientation_when_unknown_or_none(self):
        for dir_val in ("unknown", "none"):
            diag = DiagramIR(
                schema_version=1,
                diagram_type="field_sketch",
                source_page=1,
                areas=[AreaEntity(id="a1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5))],
                orientation=DiagramOrientation(direction=dir_val),
            )
            svg_text = render_svg(diag)
            self.assertNotIn("orientation_indicator", svg_text)


class TestDiagramRenderMarkdown(unittest.TestCase):
    """Pruebas para la generación de Markdown accesible, neutralización contextual y validación de assets."""

    def test_flowchart_markdown_contains_mermaid_block(self):
        fc = DiagramIR(
            schema_version=1,
            diagram_type="flowchart",
            source_page=1,
            title="Diagrama de Flujo de Operaciones",
            areas=[
                AreaEntity(id="n1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4), label="Inicio"),
                AreaEntity(id="n2", bbox=BoundingBox2D(x_min=0.6, y_min=0.1, x_max=0.9, y_max=0.4), label="Fin"),
            ],
            relations=[
                RelationEntity(id="r1", source_id="n1", target_id="n2", relation_type="flows_to", label="Avanza"),
            ],
            textual_reconstruction="┌────────┐     ┌─────┐\n│ Inicio │ ──> │ Fin │\n└────────┘     └─────┘",
            spatial_summary="La caja Inicio se conecta mediante una flecha con la caja Fin.",
            reconstruction_uncertainties=["No se distingue una etiqueta secundaria."],
        )

        md = render_markdown(fc)
        self.assertIn("### Diagrama de Flujo de Operaciones", md)
        self.assertIn("```mermaid", md)
        self.assertIn("flowchart TD", md)
        self.assertIn("#### Descripción textual", md)
        self.assertIn("#### Interpretación espacial propuesta por el VLM", md)
        self.assertIn("```text", md)
        self.assertIn("La caja Inicio se conecta", md)
        self.assertIn("No se distingue una etiqueta secundaria", md)
        self.assertIn("- **Tipo de diagrama:** Diagrama de flujo (`flowchart`)", md)
        self.assertIn("- **Página fuente:** 1", md)
        self.assertIn("`n1` -> `n2`", md)

    def test_sketch_markdown_non_georeferenced_notice(self):
        sketch = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=5,
            title="Croquis de Parcela",
            georeferenced=False,
            areas=[
                AreaEntity(id="A1", bbox=BoundingBox2D(x_min=0.2, y_min=0.2, x_max=0.6, y_max=0.6), label="Zona 1"),
            ],
            points=[
                PointEntity(id="P1", coordinate=Point2D(x=0.3, y=0.3), label="Muestra 1"),
            ],
        )

        md = render_markdown(sketch, asset_relative_path="assets/croquis_p005.svg")
        self.assertIn("![Croquis de Parcela](assets/croquis_p005.svg)", md)
        self.assertIn("El croquis no contiene coordenadas GPS exactas", md)
        self.assertIn("No declarada en el documento original", md)
        self.assertIn("pos_relativa: x=0.30, y=0.30", md)

    def test_sketch_markdown_georeferenced_details(self):
        gps_sketch = DiagramIR(
            schema_version=1,
            diagram_type="gps_sketch",
            source_page=2,
            title="Esquema GPS de Referencia",
            georeferenced=True,
            crs="ETRS89 / UTM zone 30N",
            geographic_coordinates=[
                GeographicCoordinate(
                    id="geo_1",
                    latitude=37.3891,
                    longitude=-5.9845,
                    elevation_m=12.5,
                    raw_text="37.3891N, 5.9845W, 12.5m",
                    source_page=2,
                    associated_point_id="pt_gps",
                    uncertain=True,
                    alternatives=["37.3892N, -5.9846W"],
                ),
            ],
            points=[
                PointEntity(id="pt_gps", coordinate=Point2D(x=0.5, y=0.5), label="Hito Central"),
            ],
        )

        md = render_markdown(gps_sketch)
        self.assertIn("- **Georreferenciación:** Georreferenciado.", md)
        self.assertIn("- **Sistema de Coordenadas (CRS):** ETRS89 / UTM zone 30N", md)
        self.assertIn("Latitud 37.3891, Longitud -5.9845, Elevación: 12.5 m", md)
        self.assertIn('Texto original: "37.3891N, 5.9845W, 12.5m"', md)
        self.assertIn("Punto visual asociado: `pt_gps`", md)
        self.assertIn("Marcada dudosa", md)

    def test_markdown_hostile_injections_neutralization(self):
        hostile_diag = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            title="# Injected H1\n## Injected H2\n```mermaid\nBad block",
            description="- Injected bullet item\n<script>alert(1)</script>\n[Injected Link](http://evil.com)",
            areas=[
                AreaEntity(
                    id="a`1",
                    bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4),
                    label="**Bold unclosed and [link](http://bad.org) | pipe",
                    area_type="plot`type",
                ),
            ],
            points=[
                PointEntity(
                    id="p`1",
                    coordinate=Point2D(x=0.2, y=0.2),
                    label='<img src="x" onerror="evil()"/>',
                    point_type="sample`type",
                ),
            ],
            lines=[
                LineEntity(
                    id="l`1",
                    points=[Point2D(x=0.1, y=0.1), Point2D(x=0.9, y=0.9)],
                    label="Line [label](http://evil.com)",
                    line_type="stream`type",
                ),
            ],
            labels=[
                LabelEntity(
                    id="lbl`1",
                    text="<a href='http://xss.com'>Click me</a>",
                ),
            ],
            relations=[
                RelationEntity(
                    id="r`1",
                    source_id="p`1",
                    target_id="p`1",
                    relation_type="flows`to",
                    label="Relation [label](url)",
                ),
            ],
            warnings=[
                ExtractionWarning(
                    code="WARN`1",
                    message="<script>evil()</script>\n# Injected Warn Heading",
                    source_page=1,
                ),
            ],
        )

        md = render_markdown(hostile_diag)

        self.assertNotIn("<script>", md)
        self.assertNotIn("<img ", md)
        self.assertNotIn("<a href", md)

        heading_lines = [line for line in md.splitlines() if line.startswith("#")]
        self.assertEqual(len(heading_lines), 2)
        self.assertTrue(heading_lines[0].startswith("### "))
        self.assertEqual(heading_lines[1], "#### Descripción textual")

        self.assertNotIn("[Injected Link](http://evil.com)", md)
        self.assertIn("\[Injected Link\]\(http://evil.com\)", md)

        self.assertNotIn("```", md)

        self.assertIn("`a'1`", md)
        self.assertIn("`p'1`", md)

    def test_asset_relative_path_validation_and_rejection(self):
        sketch = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            areas=[AreaEntity(id="a1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5))],
        )

        for valid_path in ("assets/sketch.svg", "output/sub/sketch_p001.svg", "sketch.svg"):
            md = render_markdown(sketch, asset_relative_path=valid_path)
            self.assertIn(f"![Croquis de campo]({valid_path})", md)

        invalid_paths = [
            "http://evil.com/sketch.svg",
            "https://evil.com/sketch.svg",
            "javascript:alert(1)",
            "file:///C:/bad/sketch.svg",
            "/absolute/sketch.svg",
            "C:/windows/sketch.svg",
            "../outside.svg",
            "assets/../../outside.svg",
            "assets\\sketch.svg",
            "assets/sketch.svg?query=1",
            "assets/sketch.svg#fragment",
            "assets/sketch (1).svg",
            "assets/sketch\nname.svg",
        ]
        for bad_path in invalid_paths:
            with self.assertRaises(ValueError, msg=f"Debió rechazar {bad_path!r}"):
                render_markdown(sketch, asset_relative_path=bad_path)

    def test_markdown_deterministic_byte_for_byte(self):
        sketch = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            areas=[AreaEntity(id="A1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5))],
        )
        md1 = render_markdown(sketch)
        md2 = render_markdown(sketch)
        self.assertEqual(md1, md2)
        self.assertEqual(md1.encode("utf-8"), md2.encode("utf-8"))


class TestDiagramRenderingOrchestrationAndPublication(unittest.TestCase):
    """Pruebas para render_diagram y publish_rendered_diagram con staging, atomicidad y rollback exhaustivo."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_diagram_render_")
        self.img_path = Path(self.test_dir) / "source_diagram.png"
        self.img_path.write_bytes(create_sample_png_bytes())

        self.flowchart = DiagramIR(
            schema_version=1,
            diagram_type="flowchart",
            source_page=1,
            title="Diagrama Flujo Test",
            areas=[
                AreaEntity(id="b1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4), label="Inicio"),
                AreaEntity(id="b2", bbox=BoundingBox2D(x_min=0.6, y_min=0.1, x_max=0.9, y_max=0.4), label="Fin"),
            ],
            relations=[
                RelationEntity(id="r1", source_id="b1", target_id="b2", relation_type="flows_to"),
            ],
        )

        self.sketch = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            title="Croquis Test",
            areas=[
                AreaEntity(id="a1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5), label="Sector 1"),
            ],
            points=[
                PointEntity(id="p1", coordinate=Point2D(x=0.3, y=0.3), label="Punto 1"),
            ],
        )

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_render_diagram_flowchart(self):
        res = render_diagram(self.flowchart)
        self.assertIsInstance(res, DiagramRenderResult)
        self.assertEqual(res.diagram_type, "flowchart")
        self.assertIsNotNone(res.mermaid_code)
        self.assertIsNone(res.svg_content)
        self.assertIn("flowchart TD", res.mermaid_code)
        self.assertIn("```mermaid", res.markdown_content)

    def test_render_diagram_sketch(self):
        res = render_diagram(self.sketch)
        self.assertIsInstance(res, DiagramRenderResult)
        self.assertEqual(res.diagram_type, "field_sketch")
        self.assertIsNone(res.mermaid_code)
        self.assertIsNotNone(res.svg_content)
        self.assertIn("<svg", res.svg_content)
        self.assertIn("![Croquis Test]", res.markdown_content)

    def test_publish_flowchart_creates_canonical_structure(self):
        out_base = Path(self.test_dir) / "output"
        published = publish_rendered_diagram(
            diagram=self.flowchart,
            source_image_path=self.img_path,
            output_base_dir=out_base,
            document_name="flowchart_doc",
        )

        canonical_dir = published["canonical_dir"]
        self.assertTrue(canonical_dir.is_dir())
        self.assertTrue(published["diagram_json"].is_file())
        self.assertTrue(published["original_image"].is_file())
        self.assertTrue(published["markdown"].is_file())
        self.assertTrue(published["mermaid_file"].is_file())
        self.assertIsNone(published["svg_file"])

        with open(published["diagram_json"], "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["diagram_type"], "flowchart")
        self.assertEqual(data["title"], "Diagrama Flujo Test")

        self.assertEqual(published["original_image"].read_bytes(), self.img_path.read_bytes())

    def test_publish_sketch_creates_canonical_structure(self):
        out_base = Path(self.test_dir) / "output"
        published = publish_rendered_diagram(
            diagram=self.sketch,
            source_image_path=self.img_path,
            output_base_dir=out_base,
            document_name="sketch_doc",
        )

        canonical_dir = published["canonical_dir"]
        self.assertTrue(canonical_dir.is_dir())
        self.assertTrue(published["diagram_json"].is_file())
        self.assertTrue(published["original_image"].is_file())
        self.assertTrue(published["markdown"].is_file())
        self.assertTrue(published["svg_file"].is_file())
        self.assertIsNone(published["mermaid_file"])

        svg_content = published["svg_file"].read_text(encoding="utf-8")
        ET.fromstring(svg_content)

    def test_publish_directory_traversal_rejection(self):
        out_base = Path(self.test_dir) / "output"
        for bad_name in ("../../escape", "/root/doc", "C:\\bad_path", "..\\bad"):
            with self.assertRaises(ValueError) as ctx:
                publish_rendered_diagram(
                    diagram=self.sketch,
                    source_image_path=self.img_path,
                    output_base_dir=out_base,
                    document_name=bad_name,
                )
            self.assertIn("separadores de ruta o secuencias de escape no permitidas", str(ctx.exception))

    def test_publish_atomic_rollback_on_pre_swap_failure(self):
        """Fallo durante staging (antes de que canonical se mueva al backup)."""
        out_base = Path(self.test_dir) / "output"

        pub1 = publish_rendered_diagram(
            diagram=self.sketch,
            source_image_path=self.img_path,
            output_base_dir=out_base,
            document_name="test_pre_swap_rollback",
        )
        canonical_dir = pub1["canonical_dir"]
        self.assertTrue(canonical_dir.is_dir())
        initial_json = pub1["diagram_json"].read_text(encoding="utf-8")

        with patch("src.fieldnotes.diagrams.rendering.write_atomic_file", side_effect=IOError("Simulated disk error in staging")):
            with self.assertRaises(IOError):
                publish_rendered_diagram(
                    diagram=self.flowchart,
                    source_image_path=self.img_path,
                    output_base_dir=out_base,
                    document_name="test_pre_swap_rollback",
                )

        self.assertTrue(canonical_dir.is_dir())
        restored_json = (canonical_dir / "diagram.json").read_text(encoding="utf-8")
        self.assertEqual(initial_json, restored_json)

        for item in out_base.iterdir():
            self.assertFalse(item.name.startswith(".staging_diagram_render_"))
            self.assertFalse(item.name.startswith(".backup_diagram_render_"))

    def test_publish_atomic_rollback_on_swap_failure(self):
        """Fallo durante el movimiento de staging a canonical (después de haber movido canonical al backup)."""
        out_base = Path(self.test_dir) / "output"

        pub1 = publish_rendered_diagram(
            diagram=self.sketch,
            source_image_path=self.img_path,
            output_base_dir=out_base,
            document_name="test_swap_rollback",
        )
        canonical_dir = pub1["canonical_dir"]
        self.assertTrue(canonical_dir.is_dir())
        initial_json_bytes = pub1["diagram_json"].read_bytes()
        initial_img_bytes = pub1["original_image"].read_bytes()
        initial_svg_bytes = pub1["svg_file"].read_bytes()

        original_move = shutil.move

        def selective_move(src, dst):
            src_str = str(src)
            if ".staging_diagram_render_" in src_str and str(dst) == str(canonical_dir):
                raise PermissionError("Simulated permission error moving staging to canonical")
            return original_move(src, dst)

        with patch("shutil.move", side_effect=selective_move):
            with self.assertRaises(PermissionError):
                publish_rendered_diagram(
                    diagram=self.flowchart,
                    source_image_path=self.img_path,
                    output_base_dir=out_base,
                    document_name="test_swap_rollback",
                )

        self.assertTrue(canonical_dir.is_dir())
        self.assertEqual(pub1["diagram_json"].read_bytes(), initial_json_bytes)
        self.assertEqual(pub1["original_image"].read_bytes(), initial_img_bytes)
        self.assertEqual(pub1["svg_file"].read_bytes(), initial_svg_bytes)

        for item in out_base.iterdir():
            self.assertFalse(item.name.startswith(".staging_diagram_render_"))
            self.assertFalse(item.name.startswith(".backup_diagram_render_"))

    def test_publish_failure_during_rollback_preserves_backup_and_raises_error(self):
        """Fallo durante la restauración del backup: NUNCA se borra el backup y se lanza DiagramRollbackError."""
        out_base = Path(self.test_dir) / "output"

        pub1 = publish_rendered_diagram(
            diagram=self.sketch,
            source_image_path=self.img_path,
            output_base_dir=out_base,
            document_name="test_critical_rollback_fail",
        )
        canonical_dir = pub1["canonical_dir"]
        self.assertTrue(canonical_dir.is_dir())

        original_move = shutil.move

        def double_fail_move(src, dst):
            src_str = str(src)
            if ".staging_diagram_render_" in src_str and str(dst) == str(canonical_dir):
                raise OSError("Primary failure: staging to canonical")
            if ".backup_diagram_render_" in src_str and str(dst) == str(canonical_dir):
                raise OSError("Secondary failure: backup restoration to canonical")
            return original_move(src, dst)

        with patch("shutil.move", side_effect=double_fail_move):
            with self.assertRaises(DiagramRollbackError) as ctx:
                publish_rendered_diagram(
                    diagram=self.flowchart,
                    source_image_path=self.img_path,
                    output_base_dir=out_base,
                    document_name="test_critical_rollback_fail",
                )

        rollback_err = ctx.exception
        self.assertIn("Fallo crítico durante el swap de publicación", str(rollback_err))
        self.assertTrue(rollback_err.backup_dir.exists())
        self.assertTrue((rollback_err.backup_dir / "diagram.json").is_file())


class TestPipelineAgentDiagramIntegration(unittest.TestCase):
    """Pruebas de métodos de conveniencia de DiagramIR en UnlimitedOCRAgent."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_agent_diagram_")
        self.img_path = Path(self.test_dir) / "sample_flowchart.png"
        self.img_path.write_bytes(create_sample_png_bytes())

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_agent_render_and_publish_helpers(self):
        agent = UnlimitedOCRAgent(output_dir=self.test_dir)

        diagram = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            title="Croquis Agente",
            areas=[AreaEntity(id="a1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5))],
        )

        res = agent.render_diagram(diagram)
        self.assertIsInstance(res, DiagramRenderResult)
        self.assertIsNotNone(res.svg_content)

        out_base = Path(self.test_dir) / "pub_agent"
        published = agent.publish_rendered_diagram(
            diagram=diagram,
            source_image_path=self.img_path,
            output_dir=out_base,
            document_name="agent_doc",
        )
        self.assertTrue(published["canonical_dir"].is_dir())
        self.assertTrue(published["svg_file"].is_file())


if __name__ == "__main__":
    unittest.main()
