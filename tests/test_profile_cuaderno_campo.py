import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.fieldnotes.artifacts import PageArtifact
from src.fieldnotes.profiles.notebook import (
    CuadernoCampoProfile,
    VisualInventoryDTO,
    VisualItemDTO,
)
from src.fieldnotes.schemas.diagram import DiagramDTO, LineEntityDTO, Point2DDTO, PointEntityDTO
from src.fieldnotes.schemas.notebook import NotebookPageDTO, NotebookSectionDTO


class TestCuadernoCampoProfile(unittest.TestCase):
    def test_separate_vlm_visual_pass_and_no_estadillo_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            image = base / "pagina.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            agent = MagicMock()
            agent.base_output_dir = str(base)
            agent.output_dir = str(base / "run")
            Path(agent.output_dir).mkdir()
            agent.page_artifacts = [PageArtifact(page_number=1, image_path=image, raw_ocr="Inicio Fin")]
            page = NotebookPageDTO(
                page_number=1,
                sections=[NotebookSectionDTO(title="Proceso", content="Inicio y fin")],
            )
            inventory = VisualInventoryDTO(
                items=[VisualItemDTO(visual_type="flowchart", description="Inicio a fin")]
            )
            diagram = DiagramDTO(
                diagram_type="flowchart",
                source_page=1,
                points=[
                    PointEntityDTO(id="p1", coordinate=Point2DDTO(x=0.2, y=0.2), label="Inicio"),
                    PointEntityDTO(id="p2", coordinate=Point2DDTO(x=0.8, y=0.8), label="Fin"),
                ],
            )
            agent.ask_page_vision_structured.side_effect = [page, inventory, diagram, diagram]

            markdown, document = CuadernoCampoProfile(agent, base).run(image)

            self.assertEqual(agent.ask_page_vision_structured.call_count, 4)
            self.assertEqual(len(document.diagrams), 1)
            self.assertIn("```mermaid", markdown)
            self.assertIn("### Imagen original de la página", markdown)
            self.assertIn("pages/page_001.png", markdown)
            self.assertNotIn("|id|col|fil|especie", markdown)
            self.assertNotIn("| Objetivo | Fecha | Asistentes |", markdown)
            self.assertTrue((base / "pagina" / "cuaderno_campo.md").is_file())


if __name__ == "__main__":
    unittest.main()
