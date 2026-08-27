import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

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
    Point2DDTO,
    BoundingBox2DDTO,
    GeographicCoordinateDTO,
    DiagramOrientationDTO,
    PointEntityDTO,
    LineEntityDTO,
    AreaEntityDTO,
    LabelEntityDTO,
    RelationEntityDTO,
    DiagramDTO,
    dto_to_diagram_ir,
)
from src.fieldnotes.schemas.warnings import ExtractionWarning
from src.fieldnotes.artifacts import PageArtifact
from src.fieldnotes.diagrams.validation import (
    DiagramValidationError,
    DiagramDuplicateIdError,
    DiagramReferenceError,
    DiagramGeoreferenceError,
    DiagramGeometryError,
    validate_diagram_ir,
)
from src.fieldnotes.diagrams.extraction import (
    DIAGRAM_PROMPT_TEMPLATE,
    get_diagram_prompt,
    extract_diagram_from_image,
    extract_diagram_from_page,
    persist_diagram_artifacts,
    process_diagram,
)
from src.fieldnotes.pipeline import UnlimitedOCRAgent


class TestDiagramIRSchemas(unittest.TestCase):
    """Pruebas de esquemas Pydantic v2 strict, extra forbid, y roundtrip de serialización."""

    def test_point2d_valid(self):
        p = Point2D(x=0.25, y=0.75)
        self.assertEqual(p.x, 0.25)
        self.assertEqual(p.y, 0.75)

    def test_point2d_strict_extra_forbid(self):
        with self.assertRaises(ValidationError):
            Point2D(x=0.5, y=0.5, z=1.0)  # type: ignore

    def test_point2d_out_of_range(self):
        with self.assertRaises(ValidationError):
            Point2D(x=-0.1, y=0.5)
        with self.assertRaises(ValidationError):
            Point2D(x=0.5, y=1.1)

    def test_point2d_nan_inf_rejected(self):
        with self.assertRaises(ValidationError):
            Point2D(x=float("nan"), y=0.5)
        with self.assertRaises(ValidationError):
            Point2D(x=0.5, y=float("inf"))
        with self.assertRaises(ValidationError):
            Point2D(x=float("-inf"), y=0.5)

    def test_bounding_box2d_valid(self):
        bb = BoundingBox2D(x_min=0.1, y_min=0.2, x_max=0.8, y_max=0.9)
        self.assertEqual(bb.x_min, 0.1)
        self.assertEqual(bb.x_max, 0.8)

    def test_bounding_box2d_degenerate_rejected(self):
        with self.assertRaises(ValidationError):
            BoundingBox2D(x_min=0.5, y_min=0.2, x_max=0.5, y_max=0.9)
        with self.assertRaises(ValidationError):
            BoundingBox2D(x_min=0.8, y_min=0.2, x_max=0.2, y_max=0.9)
        with self.assertRaises(ValidationError):
            BoundingBox2D(x_min=0.1, y_min=0.7, x_max=0.8, y_max=0.3)

    def test_geographic_coordinate_valid(self):
        gc = GeographicCoordinate(
            id="gps_1",
            latitude=40.4168,
            longitude=-3.7038,
            elevation_m=650.0,
            raw_text="40.4168N, 3.7038W",
            source_page=1,
            uncertain=False,
        )
        self.assertEqual(gc.latitude, 40.4168)
        self.assertEqual(gc.longitude, -3.7038)
        self.assertEqual(gc.source_page, 1)
        self.assertEqual(gc.raw_text, "40.4168N, 3.7038W")

    def test_geographic_coordinate_requires_non_empty_raw_text(self):
        # raw_text ausente o vacío
        with self.assertRaises(ValidationError):
            GeographicCoordinate(
                id="g1",
                latitude=40.0,
                longitude=-3.0,
                source_page=1,
            )  # type: ignore
        with self.assertRaises(ValidationError):
            GeographicCoordinate(
                id="g1",
                latitude=40.0,
                longitude=-3.0,
                raw_text="",
                source_page=1,
            )
        with self.assertRaises(ValidationError):
            GeographicCoordinate(
                id="g1",
                latitude=40.0,
                longitude=-3.0,
                raw_text="   ",
                source_page=1,
            )

    def test_geographic_coordinate_out_of_range(self):
        with self.assertRaises(ValidationError):
            GeographicCoordinate(id="g1", latitude=95.0, longitude=0.0, raw_text="95N", source_page=1)
        with self.assertRaises(ValidationError):
            GeographicCoordinate(id="g1", latitude=0.0, longitude=-185.0, raw_text="185W", source_page=1)

    def test_geographic_coordinate_nan_inf_rejected(self):
        with self.assertRaises(ValidationError):
            GeographicCoordinate(id="g1", latitude=float("nan"), longitude=0.0, raw_text="nan", source_page=1)
        with self.assertRaises(ValidationError):
            GeographicCoordinate(id="g1", latitude=0.0, longitude=float("inf"), raw_text="inf", source_page=1)

    def test_geographic_coordinate_alternatives_only_when_uncertain(self):
        with self.assertRaises(ValidationError):
            GeographicCoordinate(
                id="g1",
                latitude=40.0,
                longitude=-3.0,
                raw_text="40.0N, 3.0W",
                source_page=1,
                uncertain=False,
                alternatives=["40.1, -3.1"],
            )

    def test_orientation_rules_strict(self):
        # unknown y none válidos sin degrees ni raw_text
        o_unk = DiagramOrientation(direction="unknown")
        self.assertEqual(o_unk.direction, "unknown")
        self.assertIsNone(o_unk.degrees)

        o_none = DiagramOrientation(direction="none")
        self.assertEqual(o_none.direction, "none")
        self.assertIsNone(o_none.degrees)

        # north_up exige raw_text no vacío y degrees debe ser None
        o_north = DiagramOrientation(direction="north_up", raw_text="Flecha Norte")
        self.assertEqual(o_north.direction, "north_up")
        self.assertIsNone(o_north.degrees)

        # north_up sin raw_text es rechazado
        with self.assertRaises(ValidationError):
            DiagramOrientation(direction="north_up")
        with self.assertRaises(ValidationError):
            DiagramOrientation(direction="north_up", raw_text="   ")

        # north_up con degrees es rechazado
        with self.assertRaises(ValidationError):
            DiagramOrientation(direction="north_up", degrees=0.0, raw_text="N")

        # rotated exige degrees explícito y raw_text no vacío
        o_rot = DiagramOrientation(direction="rotated", degrees=45.0, raw_text="Rotado 45 deg")
        self.assertEqual(o_rot.degrees, 45.0)

        # rotated sin degrees es rechazado
        with self.assertRaises(ValidationError):
            DiagramOrientation(direction="rotated", raw_text="Rotado")

        # rotated sin raw_text es rechazado
        with self.assertRaises(ValidationError):
            DiagramOrientation(direction="rotated", degrees=45.0)

        # unknown con degrees es rechazado
        with self.assertRaises(ValidationError):
            DiagramOrientation(direction="unknown", degrees=90.0)

    def test_orientation_dto_rules_strict(self):
        dto_rot = DiagramOrientationDTO(direction="rotated", degrees=30.0, raw_text="Giro 30")
        self.assertEqual(dto_rot.degrees, 30.0)

        with self.assertRaises(ValidationError):
            DiagramOrientationDTO(direction="rotated")
        with self.assertRaises(ValidationError):
            DiagramOrientationDTO(direction="north_up", degrees=0.0, raw_text="Norte")
        with self.assertRaises(ValidationError):
            DiagramOrientationDTO(direction="north_up")  # falta raw_text

    def test_line_entity_non_degenerate(self):
        p1 = Point2D(x=0.1, y=0.1)
        p2 = Point2D(x=0.5, y=0.5)
        l = LineEntity(id="l1", points=[p1, p2], line_type="stream")
        self.assertEqual(len(l.points), 2)

        # Menos de 2 puntos
        with self.assertRaises(ValidationError):
            LineEntity(id="l2", points=[p1])

        # Puntos idénticos (línea degenerada)
        with self.assertRaises(ValidationError):
            LineEntity(id="l3", points=[p1, Point2D(x=0.1, y=0.1)])

    def test_area_entity_polygon_non_collinear_area(self):
        # Polígono válido con área > 0
        p1 = Point2D(x=0.1, y=0.1)
        p2 = Point2D(x=0.8, y=0.1)
        p3 = Point2D(x=0.5, y=0.8)
        area = AreaEntity(id="a1", polygon=[p1, p2, p3], area_type="plot")
        self.assertIsNotNone(area.polygon)

        # Polígono colineal horizontal (área cero)
        c1 = Point2D(x=0.1, y=0.5)
        c2 = Point2D(x=0.4, y=0.5)
        c3 = Point2D(x=0.8, y=0.5)
        with self.assertRaises(ValidationError):
            AreaEntity(id="a_collinear", polygon=[c1, c2, c3])

        # Polígono colineal diagonal (área cero)
        d1 = Point2D(x=0.1, y=0.1)
        d2 = Point2D(x=0.3, y=0.3)
        d3 = Point2D(x=0.7, y=0.7)
        with self.assertRaises(ValidationError):
            AreaEntity(id="a_collinear_diag", polygon=[d1, d2, d3])

    def test_schema_version_strict_literal_1(self):
        d = DiagramIR(
            schema_version=1,
            diagram_type="field_sketch",
            source_page=1,
            georeferenced=False,
        )
        self.assertEqual(d.schema_version, 1)

        # schema_version != 1 debe ser rechazado
        with self.assertRaises(ValidationError):
            DiagramIR(
                schema_version=2,  # type: ignore
                diagram_type="field_sketch",
                source_page=1,
                georeferenced=False,
            )
        with self.assertRaises(ValidationError):
            DiagramIR(
                schema_version=0,  # type: ignore
                diagram_type="field_sketch",
                source_page=1,
                georeferenced=False,
            )

    def test_georeferenced_invariants(self):
        # georeferenced=False prohíbe CRS
        with self.assertRaises(ValidationError):
            DiagramIR(
                diagram_type="field_sketch",
                source_page=1,
                georeferenced=False,
                crs="EPSG:4326",
            )

        # georeferenced=False prohíbe coordenadas
        gc = GeographicCoordinate(
            id="g1", latitude=40.0, longitude=-3.0, raw_text="40N, 3W", source_page=1
        )
        with self.assertRaises(ValidationError):
            DiagramIR(
                diagram_type="field_sketch",
                source_page=1,
                georeferenced=False,
                geographic_coordinates=[gc],
            )

        # georeferenced=True exige CRS no vacío
        with self.assertRaises(ValidationError):
            DiagramIR(
                diagram_type="field_sketch",
                source_page=1,
                georeferenced=True,
                crs=None,
                geographic_coordinates=[gc],
            )

        # georeferenced=True exige al menos una coordenada
        with self.assertRaises(ValidationError):
            DiagramIR(
                diagram_type="field_sketch",
                source_page=1,
                georeferenced=True,
                crs="EPSG:4326",
                geographic_coordinates=[],
            )

    def test_unique_ids_across_all_collections(self):
        p = PointEntity(id="dup_id", coordinate=Point2D(x=0.1, y=0.1))
        a = AreaEntity(id="dup_id", bbox=BoundingBox2D(x_min=0.2, y_min=0.2, x_max=0.6, y_max=0.6))
        with self.assertRaises(ValidationError):
            DiagramIR(
                diagram_type="field_sketch",
                source_page=1,
                points=[p],
                areas=[a],
            )

    def test_referential_integrity_relations(self):
        p1 = PointEntity(id="p1", coordinate=Point2D(x=0.1, y=0.1))
        r_valid = RelationEntity(id="r1", source_id="p1", target_id="p1", relation_type="loop")
        d_valid = DiagramIR(
            diagram_type="flowchart",
            source_page=1,
            points=[p1],
            relations=[r_valid],
        )
        self.assertEqual(len(d_valid.relations), 1)

        # Relación a ID inexistente
        r_invalid = RelationEntity(id="r2", source_id="p1", target_id="missing_id", relation_type="flows_to")
        with self.assertRaises(ValidationError):
            DiagramIR(
                diagram_type="flowchart",
                source_page=1,
                points=[p1],
                relations=[r_invalid],
            )

    def test_line_endpoints_must_resolve_exclusively_to_point_entity(self):
        p1 = PointEntity(id="pt_1", coordinate=Point2D(x=0.1, y=0.1))
        p2 = PointEntity(id="pt_2", coordinate=Point2D(x=0.5, y=0.5))
        area1 = AreaEntity(id="area_1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4))
        lbl1 = LabelEntity(id="lbl_1", text="Texto de prueba")

        # Línea válida conectando dos PointEntity
        line_valid = LineEntity(
            id="line_ok",
            points=[Point2D(x=0.1, y=0.1), Point2D(x=0.5, y=0.5)],
            source_point_id="pt_1",
            target_point_id="pt_2",
        )
        d_ok = DiagramIR(
            diagram_type="field_sketch",
            source_page=1,
            points=[p1, p2],
            lines=[line_valid],
        )
        self.assertEqual(len(d_ok.lines), 1)

        # Línea referenciando un AreaEntity como source_point_id debe ser rechazada
        line_bad_area = LineEntity(
            id="line_bad1",
            points=[Point2D(x=0.1, y=0.1), Point2D(x=0.5, y=0.5)],
            source_point_id="area_1",
            target_point_id="pt_2",
        )
        with self.assertRaises(ValidationError):
            DiagramIR(
                diagram_type="field_sketch",
                source_page=1,
                points=[p1, p2],
                areas=[area1],
                lines=[line_bad_area],
            )

        # Línea referenciando un LabelEntity como target_point_id debe ser rechazada
        line_bad_label = LineEntity(
            id="line_bad2",
            points=[Point2D(x=0.1, y=0.1), Point2D(x=0.5, y=0.5)],
            source_point_id="pt_1",
            target_point_id="lbl_1",
        )
        with self.assertRaises(ValidationError):
            DiagramIR(
                diagram_type="field_sketch",
                source_page=1,
                points=[p1, p2],
                labels=[lbl1],
                lines=[line_bad_label],
            )


class TestDiagramValidationModule(unittest.TestCase):
    """Pruebas del validador funcional validate_diagram_ir."""

    def test_validate_diagram_ir_valid(self):
        d = DiagramIR(
            diagram_type="field_sketch",
            source_page=1,
            title="Croquis de prueba",
            georeferenced=False,
            points=[
                PointEntity(id="p1", coordinate=Point2D(x=0.2, y=0.3), label="Punto 1")
            ],
        )
        warns = validate_diagram_ir(d, raise_on_error=True)
        self.assertEqual(warns, [])

    def test_validate_diagram_ir_non_diagram_input(self):
        with self.assertRaises(DiagramValidationError):
            validate_diagram_ir("invalid_object", raise_on_error=True)  # type: ignore

        w = validate_diagram_ir("invalid_object", raise_on_error=False)  # type: ignore
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0].code, "INVALID_TYPE")

    def test_validate_diagram_ir_line_endpoint_type_error(self):
        # Crear un DiagramIR y forzar una referencia a área en line para probar validate_diagram_ir
        p1 = PointEntity(id="p1", coordinate=Point2D(x=0.2, y=0.3))
        a1 = AreaEntity(id="a1", bbox=BoundingBox2D(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4))
        # Usamos object.__setattr__ para probar validate_diagram_ir ante inconsistencias
        line = LineEntity(id="l1", points=[Point2D(x=0.1, y=0.1), Point2D(x=0.2, y=0.2)], source_point_id="a1")
        # Directamente construyendo y validando
        with self.assertRaises(ValidationError):
            DiagramIR(diagram_type="field_sketch", source_page=1, points=[p1], areas=[a1], lines=[line])


class TestSyntheticFixturesRoundtrip(unittest.TestCase):
    """Carga y validación exhaustiva de los fixtures sintéticos anonimizados."""

    def setUp(self):
        self.fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "diagrams"

    def test_field_sketch_fixture(self):
        path = self.fixtures_dir / "field_sketch.json"
        self.assertTrue(path.is_file(), f"Fixture no encontrado: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        diagram = DiagramIR.model_validate(data)
        self.assertEqual(diagram.diagram_type, "field_sketch")
        self.assertEqual(diagram.schema_version, 1)
        self.assertEqual(len(diagram.areas), 2)
        self.assertEqual(len(diagram.points), 3)
        self.assertEqual(len(diagram.lines), 2)
        self.assertFalse(diagram.georeferenced)

        # Roundtrip JSON
        dumped = json.loads(diagram.model_dump_json())
        reloaded = DiagramIR.model_validate(dumped)
        self.assertEqual(diagram, reloaded)

    def test_flowchart_fixture(self):
        path = self.fixtures_dir / "flowchart.json"
        self.assertTrue(path.is_file(), f"Fixture no encontrado: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        diagram = DiagramIR.model_validate(data)
        self.assertEqual(diagram.diagram_type, "flowchart")
        self.assertEqual(diagram.schema_version, 1)
        self.assertEqual(len(diagram.areas), 5)
        self.assertEqual(len(diagram.relations), 3)

        dumped = json.loads(diagram.model_dump_json())
        reloaded = DiagramIR.model_validate(dumped)
        self.assertEqual(diagram, reloaded)

    def test_gps_sketch_fixture(self):
        path = self.fixtures_dir / "gps_sketch.json"
        self.assertTrue(path.is_file(), f"Fixture no encontrado: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        diagram = DiagramIR.model_validate(data)
        self.assertEqual(diagram.diagram_type, "gps_sketch")
        self.assertEqual(diagram.schema_version, 1)
        self.assertTrue(diagram.georeferenced)
        self.assertEqual(diagram.crs, "EPSG:4326")
        self.assertEqual(len(diagram.geographic_coordinates), 3)

        dumped = json.loads(diagram.model_dump_json())
        reloaded = DiagramIR.model_validate(dumped)
        self.assertEqual(diagram, reloaded)


class TestDTOConversion(unittest.TestCase):
    """Pruebas de conversión pura DTO -> DiagramIR."""

    def test_dto_to_canonical_pure_conversion(self):
        dto = DiagramDTO(
            diagram_type="field_sketch",
            source_page=1,
            title="Croquis DTO",
            georeferenced=False,
            points=[
                PointEntityDTO(
                    id="p1",
                    coordinate=Point2DDTO(x=0.1, y=0.2),
                    label="Punto 1",
                )
            ],
            areas=[
                AreaEntityDTO(
                    id="a1",
                    bbox=BoundingBox2DDTO(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5),
                    label="Zona A",
                )
            ],
            warnings=["Texto semi-borrado en esquina"],
        )

        canonical = dto_to_diagram_ir(dto, source_page=1)
        self.assertIsInstance(canonical, DiagramIR)
        self.assertEqual(canonical.title, "Croquis DTO")
        self.assertEqual(canonical.source_page, 1)
        self.assertEqual(len(canonical.points), 1)
        self.assertEqual(len(canonical.areas), 1)
        self.assertEqual(len(canonical.warnings), 1)
        self.assertEqual(canonical.warnings[0].source_page, 1)

    def test_dto_to_canonical_with_diagram_ir_input_returns_deep_copy(self):
        original_ir = DiagramIR(
            diagram_type="field_sketch",
            source_page=1,
            title="Original",
            georeferenced=False,
            points=[PointEntity(id="p1", coordinate=Point2D(x=0.1, y=0.1))],
        )

        copied = dto_to_diagram_ir(original_ir, source_page=1)
        self.assertIsInstance(copied, DiagramIR)
        self.assertEqual(copied.title, "Original")

        # Verificar identidad distinta (no devuelve el mismo objeto mutable)
        self.assertIsNot(copied, original_ir)
        self.assertIsNot(copied.points, original_ir.points)

        # Discrepancia de página lanza ValueError
        with self.assertRaises(ValueError):
            dto_to_diagram_ir(original_ir, source_page=2)

    def test_dto_source_page_mismatch_rejected(self):
        dto = DiagramDTO(
            diagram_type="field_sketch",
            source_page=1,
            georeferenced=False,
        )
        with self.assertRaises(ValueError):
            dto_to_diagram_ir(dto, source_page=2)


class TestContractualPrompt(unittest.TestCase):
    """Verificación de que el prompt cumple todas las restricciones contractuales."""

    def test_prompt_rules_and_diagram_type_mandatory(self):
        # diagram_type válido
        prompt = get_diagram_prompt(diagram_type="field_sketch", page_number=2)
        self.assertIn("field_sketch", prompt)
        self.assertIn("NO CÓDIGO GRÁFICO", prompt)
        self.assertIn("NO generes SVG, Mermaid", prompt)
        self.assertIn("COORDENADAS VISUALES [0.0, 1.0]", prompt)
        self.assertIn("NUNCA infieras coordenadas GPS", prompt)
        self.assertIn("source_page debe ser exactamente 2", prompt)

        # diagram_type None o inválido debe lanzar ValueError descriptivo (no inventar tipo)
        with self.assertRaises(ValueError):
            get_diagram_prompt(diagram_type=None)  # type: ignore
        with self.assertRaises(ValueError):
            get_diagram_prompt(diagram_type="invalid_type")  # type: ignore
        with self.assertRaises(ValueError):
            get_diagram_prompt(diagram_type="")  # type: ignore


class TestMockedExtractionAndPersistence(unittest.TestCase):
    """Pruebas de extracción con VLM mockeado y persistencia atómica con staging/rollback."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_diagram_")
        self.img_path = Path(self.test_dir) / "sample_croquis.png"
        with open(self.img_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4fakeimagebytes")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_diagram_from_image_diagram_type_validation(self):
        mock_client = MagicMock()
        # diagram_type inválido o None lanza ValueError antes de llamar a VLM
        with self.assertRaises(ValueError):
            extract_diagram_from_image(
                image_path=self.img_path,
                client=mock_client,
                diagram_type=None,  # type: ignore
            )
        with self.assertRaises(ValueError):
            extract_diagram_from_image(
                image_path=self.img_path,
                client=mock_client,
                diagram_type="unknown_type",  # type: ignore
            )
        mock_client.ask_vision_structured.assert_not_called()

    def test_mocked_extract_and_persist_success(self):
        mock_client = MagicMock()
        mock_dto = DiagramDTO(
            diagram_type="field_sketch",
            source_page=1,
            title="Croquis Mockeado",
            description="Descripción mockeada",
            orientation=DiagramOrientationDTO(direction="north_up", degrees=None, raw_text="Norte"),
            georeferenced=False,
            areas=[
                AreaEntityDTO(
                    id="area_1",
                    bbox=BoundingBox2DDTO(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5),
                    label="Bancal Norte",
                )
            ],
            points=[
                PointEntityDTO(
                    id="pt_1",
                    coordinate=Point2DDTO(x=0.3, y=0.3),
                    label="Muestra A",
                )
            ],
            relations=[
                RelationEntityDTO(
                    id="rel_1",
                    source_id="area_1",
                    target_id="pt_1",
                    relation_type="contains",
                )
            ],
        )
        mock_client.ask_vision_structured.return_value = mock_dto

        out_base = Path(self.test_dir) / "output"
        diagram, artifacts = process_diagram(
            image_path=self.img_path,
            client=mock_client,
            diagram_type="field_sketch",
            output_base_dir=out_base,
            page_number=1,
        )

        self.assertIsInstance(diagram, DiagramIR)
        self.assertEqual(diagram.title, "Croquis Mockeado")
        self.assertIsNotNone(artifacts)

        # Verificar layout canónico
        canonical_dir = artifacts["canonical_dir"]
        diagram_json_path = artifacts["diagram_json"]
        original_img_path = artifacts["original_image"]

        self.assertTrue(canonical_dir.is_dir())
        self.assertTrue(diagram_json_path.is_file())
        self.assertTrue(original_img_path.is_file())

        # Verificar que el JSON es válido
        loaded_json = json.loads(diagram_json_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded_json["diagram_type"], "field_sketch")
        self.assertEqual(loaded_json["title"], "Croquis Mockeado")
        self.assertEqual(loaded_json["schema_version"], 1)

        # Verificar copia exacta de bytes de imagen
        original_bytes = self.img_path.read_bytes()
        persisted_bytes = original_img_path.read_bytes()
        self.assertEqual(original_bytes, persisted_bytes)

        # Verificar que NO se crearon archivos gráficos .svg/.mmd/.mermaid
        generated_exts = {p.suffix.lower() for p in canonical_dir.rglob("*") if p.is_file()}
        self.assertNotIn(".svg", generated_exts)
        self.assertNotIn(".mmd", generated_exts)
        self.assertNotIn(".mermaid", generated_exts)

    def test_document_name_sanitization_and_rejection(self):
        diagram = DiagramIR(
            diagram_type="field_sketch",
            source_page=1,
            georeferenced=False,
        )
        out_base = Path(self.test_dir) / "output_doc_name"

        # Traversal ../.. rechazado
        with self.assertRaises(ValueError):
            persist_diagram_artifacts(
                diagram=diagram,
                source_image_path=self.img_path,
                output_base_dir=out_base,
                document_name="../../escaped",
            )

        # Separadores de ruta / o \ rechazados
        with self.assertRaises(ValueError):
            persist_diagram_artifacts(
                diagram=diagram,
                source_image_path=self.img_path,
                output_base_dir=out_base,
                document_name="foo/bar",
            )
        with self.assertRaises(ValueError):
            persist_diagram_artifacts(
                diagram=diagram,
                source_image_path=self.img_path,
                output_base_dir=out_base,
                document_name="foo\\bar",
            )

        # Vacío o solo espacios rechazado
        with self.assertRaises(ValueError):
            persist_diagram_artifacts(
                diagram=diagram,
                source_image_path=self.img_path,
                output_base_dir=out_base,
                document_name="   ",
            )

        # Nombre válido personalizado funciona
        art = persist_diagram_artifacts(
            diagram=diagram,
            source_image_path=self.img_path,
            output_base_dir=out_base,
            document_name="mi_croquis_personalizado",
        )
        self.assertEqual(art["canonical_dir"].name, "mi_croquis_personalizado")
        self.assertTrue(art["diagram_json"].is_file())

    def test_republishing_over_existing_directory(self):
        mock_client = MagicMock()
        mock_dto = DiagramDTO(
            diagram_type="flowchart",
            source_page=1,
            title="Version 1",
            georeferenced=False,
        )
        mock_client.ask_vision_structured.return_value = mock_dto

        out_base = Path(self.test_dir) / "output"
        diagram1, art1 = process_diagram(
            image_path=self.img_path,
            client=mock_client,
            diagram_type="flowchart",
            output_base_dir=out_base,
        )
        self.assertEqual(diagram1.title, "Version 1")

        # Segunda publicación sobre el mismo stem
        mock_dto2 = DiagramDTO(
            diagram_type="flowchart",
            source_page=1,
            title="Version 2",
            georeferenced=False,
        )
        mock_client.ask_vision_structured.return_value = mock_dto2
        diagram2, art2 = process_diagram(
            image_path=self.img_path,
            client=mock_client,
            diagram_type="flowchart",
            output_base_dir=out_base,
        )

        self.assertEqual(diagram2.title, "Version 2")
        json_content = json.loads(art2["diagram_json"].read_text(encoding="utf-8"))
        self.assertEqual(json_content["title"], "Version 2")

        # Verificar que no quedaron carpetas de backup ni staging
        all_dirs = [p.name for p in out_base.iterdir() if p.is_dir()]
        self.assertEqual(len(all_dirs), 1)
        self.assertFalse(any(d.startswith(".staging") or d.startswith(".backup") for d in all_dirs))

    def test_rollback_on_persistence_failure_preserves_original_and_cleans_partial(self):
        # 1. Publicar versión inicial
        mock_client = MagicMock()
        mock_dto1 = DiagramDTO(
            diagram_type="field_sketch",
            source_page=1,
            title="Version Estable",
            georeferenced=False,
        )
        mock_client.ask_vision_structured.return_value = mock_dto1

        out_base = Path(self.test_dir) / "output"
        diagram1, art1 = process_diagram(
            image_path=self.img_path,
            client=mock_client,
            diagram_type="field_sketch",
            output_base_dir=out_base,
        )
        self.assertEqual(diagram1.title, "Version Estable")
        original_json_bytes = art1["diagram_json"].read_bytes()

        # 2. Simular fallo tras iniciar reemplazo que deja destino parcial
        def fake_move_fail(src, dst):
            src_path = Path(src)
            if src_path.name.startswith(".staging_diagram"):
                dst_path = Path(dst)
                dst_path.mkdir(parents=True, exist_ok=True)
                (dst_path / "corrupt_partial.txt").write_text("corrupt content", encoding="utf-8")
                raise IOError("Simulated move disk failure during publication")
            return shutil._orig_move(src, dst)  # type: ignore

        shutil._orig_move = shutil.move  # type: ignore
        with patch("shutil.move", side_effect=fake_move_fail):
            mock_dto2 = DiagramDTO(
                diagram_type="field_sketch",
                source_page=1,
                title="Version Fallida",
                georeferenced=False,
            )
            mock_client.ask_vision_structured.return_value = mock_dto2

            with self.assertRaises(IOError):
                process_diagram(
                    image_path=self.img_path,
                    client=mock_client,
                    diagram_type="field_sketch",
                    output_base_dir=out_base,
                )

        # 3. Verificar que la versión previa se restauró íntegra y exacta byte a byte
        canonical_json = art1["diagram_json"]
        self.assertTrue(canonical_json.is_file())
        self.assertEqual(canonical_json.read_bytes(), original_json_bytes)
        json_data = json.loads(canonical_json.read_text(encoding="utf-8"))
        self.assertEqual(json_data["title"], "Version Estable")

        # 4. Verificar que no quedó el archivo corrupto ni carpetas de staging/backup
        self.assertFalse((art1["canonical_dir"] / "corrupt_partial.txt").exists())
        all_subdirs = [p.name for p in out_base.iterdir() if p.is_dir()]
        self.assertEqual(len(all_subdirs), 1)
        self.assertFalse(any(d.startswith(".staging") or d.startswith(".backup") for d in all_subdirs))

    def test_agent_process_diagram_delegation(self):
        agent = UnlimitedOCRAgent(
            model_name="baidu/Unlimited-OCR",
            output_dir=os.path.join(self.test_dir, "agent_out"),
            ocr_mode="worker",
            validate_model=False,
        )
        mock_dto = DiagramDTO(
            diagram_type="field_sketch",
            source_page=1,
            title="Agent Delegated",
            georeferenced=False,
        )
        agent.vlm.ask_vision_structured = MagicMock(return_value=mock_dto)

        out_base = Path(self.test_dir) / "agent_diagrams"
        diagram, art = agent.process_diagram(
            image_path=self.img_path,
            diagram_type="field_sketch",
            output_dir=out_base,
        )
        self.assertEqual(diagram.title, "Agent Delegated")
        self.assertIsNotNone(art)
        self.assertTrue(art["diagram_json"].is_file())

    def test_extract_diagram_from_page_artifact(self):
        mock_client = MagicMock()
        mock_dto = DiagramDTO(
            diagram_type="field_sketch",
            source_page=2,
            title="Page Artifact Test",
            georeferenced=False,
        )
        mock_client.ask_vision_structured.return_value = mock_dto

        artifact = PageArtifact(
            page_number=2,
            image_path=self.img_path,
            raw_ocr="Texto OCR de apoyo",
        )

        diagram = extract_diagram_from_page(
            page_artifact=artifact,
            client=mock_client,
            diagram_type="field_sketch",
        )
        self.assertEqual(diagram.source_page, 2)
        self.assertEqual(diagram.title, "Page Artifact Test")

    def test_persist_diagram_artifacts_missing_image_raises_error(self):
        diagram = DiagramIR(
            diagram_type="field_sketch",
            source_page=1,
            georeferenced=False,
        )
        non_existent = Path(self.test_dir) / "does_not_exist.png"
        out_base = Path(self.test_dir) / "output_err"

        with self.assertRaises(FileNotFoundError):
            persist_diagram_artifacts(
                diagram=diagram,
                source_image_path=non_existent,
                output_base_dir=out_base,
            )


class TestRealDiagramVisionIntegrationOptIn(unittest.TestCase):
    """Prueba de integración real con VLM y LM Studio activo (opt-in explícito)."""

    def test_real_diagram_extraction_opt_in(self):
        if not os.getenv("RUN_DIAGRAM_REAL_INTEGRATION"):
            self.skipTest(
                "Prueba de integración real desactivada por defecto. "
                "Para ejecutar con LM Studio y GPU real, define RUN_DIAGRAM_REAL_INTEGRATION=1"
            )


if __name__ == "__main__":
    unittest.main()
