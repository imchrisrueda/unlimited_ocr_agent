import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Any
from unittest.mock import MagicMock, patch

from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.warnings import ExtractionWarning
from src.fieldnotes.schemas.estadillo import EstadilloPageHeader, EstadilloDocHeader, EstadilloRow
from src.fieldnotes.schemas.dto import EstadilloHeaderDTO, EstadilloRowDTO
from src.fieldnotes.schemas.diagram import DiagramDTO, Point2DDTO, PointEntityDTO, dto_to_diagram_ir
from src.fieldnotes.schemas.notebook import (
    NotebookConfig,
    NotebookSectionConfig,
    GenericTableDTO,
    GenericTable,
    NotebookSectionDTO,
    NotebookSection,
    NotebookNoteItemDTO,
    NotebookNoteItem,
    NotebookPageDTO,
    NotebookPage,
    NotebookDocument,
    dto_to_notebook_page,
)
from src.fieldnotes.merge.notebook import merge_notebook_pages
from src.fieldnotes.validation.notebook import validate_notebook_document
from src.fieldnotes.render.notebook import render_notebook_markdown
from src.fieldnotes.profiles.notebook import (
    NotebookProfile,
    NotebookRollbackError,
    NOTEBOOK_PROMPT_TEMPLATE,
    NOTEBOOK_DEFAULT_EXTRA_BODY,
)
from src.fieldnotes.cli import build_parser, main
from src.fieldnotes.artifacts import PageArtifact


class TestNotebookConfig(unittest.TestCase):
    """Pruebas para la configuración tipada, versionada y estricta del perfil notebook."""

    def test_default_config(self):
        cfg = NotebookConfig()
        self.assertEqual(cfg.schema_version, 1)
        self.assertFalse(cfg.include_empty_sections)
        self.assertTrue(cfg.species_normalization)
        self.assertTrue(cfg.render_diagram_visuals)
        self.assertIn("metadata", cfg.section_order)
        self.assertIn("estadillo_table", cfg.section_order)

    def test_extra_fields_forbidden(self):
        with self.assertRaises(Exception):
            NotebookConfig(unknown_field="invalid")  # type: ignore[call-arg]

    def test_section_order_rejects_duplicates_and_unknowns(self):
        with self.assertRaises(ValueError):
            NotebookConfig(section_order=["metadata", "metadata"])
        with self.assertRaises(ValueError):
            NotebookConfig(section_order=["metadata", "invalid_section"])

    def test_section_config_strict(self):
        sec = NotebookSectionConfig(
            title="Observaciones Climáticas",
            aliases=["clima", "tiempo", "temperatura"],
            include_if_empty=True,
            render_order=10,
        )
        self.assertEqual(sec.title, "Observaciones Climáticas")
        self.assertTrue(sec.include_if_empty)
        self.assertEqual(sec.render_order, 10)

        with self.assertRaises(Exception):
            NotebookSectionConfig(title="Test", bad_extra=123)  # type: ignore[call-arg]

        # Título vacío rechazado
        with self.assertRaises(ValueError):
            NotebookSectionConfig(title="   ")

        # Alias vacío o duplicado en misma sección rechazado
        with self.assertRaises(ValueError):
            NotebookSectionConfig(title="S1", aliases=[""])
        with self.assertRaises(ValueError):
            NotebookSectionConfig(title="S1", aliases=["clima", "clima"])

    def test_section_configs_rejects_ambiguous_titles_aliases_and_render_orders(self):
        # Caso 1: Título duplicado entre diferentes claves
        with self.assertRaises(ValueError):
            NotebookConfig(
                section_configs={
                    "s1": NotebookSectionConfig(title="Suelo"),
                    "s2": NotebookSectionConfig(title="  suelo  "),
                }
            )

        # Caso 2: Alias duplicado/ambiguo entre diferentes claves
        with self.assertRaises(ValueError):
            NotebookConfig(
                section_configs={
                    "s1": NotebookSectionConfig(title="Suelo", aliases=["edafologia"]),
                    "s2": NotebookSectionConfig(title="Tierra", aliases=["Edafologia"]),
                }
            )

        # Caso 3: Token compartido entre alias y clave cruzada
        with self.assertRaises(ValueError):
            NotebookConfig(
                section_configs={
                    "first": NotebookSectionConfig(title="A", aliases=["second"]),
                    "second": NotebookSectionConfig(title="B"),
                }
            )

        # Caso 4: Orden inverso del caso anterior
        with self.assertRaises(ValueError):
            NotebookConfig(
                section_configs={
                    "second": NotebookSectionConfig(title="B"),
                    "first": NotebookSectionConfig(title="A", aliases=["second"]),
                }
            )

        # Caso 5: Colisión alias <-> título cruzada
        with self.assertRaises(ValueError):
            NotebookConfig(
                section_configs={
                    "s1": NotebookSectionConfig(title="Meteo", aliases=["clima"]),
                    "s2": NotebookSectionConfig(title="Clima"),
                }
            )

        # Caso 6: Colisión clave <-> título cruzada
        with self.assertRaises(ValueError):
            NotebookConfig(
                section_configs={
                    "clima": NotebookSectionConfig(title="Tiempo"),
                    "s2": NotebookSectionConfig(title="Clima"),
                }
            )

        # Caso 7: Empate ambiguo en render_order explícito
        with self.assertRaises(ValueError):
            NotebookConfig(
                section_configs={
                    "s1": NotebookSectionConfig(title="Suelo", render_order=1),
                    "s2": NotebookSectionConfig(title="Clima", render_order=1),
                }
            )

        # Caso 8: Dentro de la misma sección, key/title/alias equivalentes se deduplican sin error
        valid_intra = NotebookConfig(
            section_configs={
                "clima": NotebookSectionConfig(title="Clima", aliases=["clima", "tiempo"])
            }
        )
        self.assertEqual(valid_intra.section_configs["clima"].aliases, ["tiempo"])

    def test_header_aliases_validation(self):
        valid_cfg = NotebookConfig(
            header_aliases={
                "objetivo": ["meta", "finalidad"],
                "asistentes": ["participantes"],
            }
        )
        self.assertEqual(len(valid_cfg.header_aliases["objetivo"]), 2)

        # Campo desconocido no permitido
        with self.assertRaises(ValueError):
            NotebookConfig(header_aliases={"campo_inexistente": ["alias"]})

        # Alias duplicado entre diferentes campos de cabecera
        with self.assertRaises(ValueError):
            NotebookConfig(
                header_aliases={
                    "objetivo": ["comun"],
                    "asistentes": ["comun"],
                }
            )

        # Nombres canónicos reservados: un alias no puede ser el nombre canónico de otro campo
        with self.assertRaises(ValueError):
            NotebookConfig(header_aliases={"objetivo": ["fecha"]})

        # Nombres canónicos reservados: comprobación case-insensitive, espacios y acentos
        with self.assertRaises(ValueError):
            NotebookConfig(header_aliases={"objetivo": ["  FECHA  "]})

        with self.assertRaises(ValueError):
            NotebookConfig(header_aliases={"objetivo": ["situación atmosférica"]})

        with self.assertRaises(ValueError):
            NotebookConfig(header_aliases={"objetivo": ["situacion_atmosferica"]})

        # Alias igual al propio campo se deduplica/omite limpiamente sin error
        dedup_cfg = NotebookConfig(
            header_aliases={
                "objetivo": [" Objetivo ", "meta"],
            }
        )
        self.assertEqual(dedup_cfg.header_aliases["objetivo"], ["meta"])

    def test_height_bounds_validation(self):
        # min_height_cm negativo
        with self.assertRaises(ValueError):
            NotebookConfig(min_height_cm=-1.0)

        # max_height_cm <= min_height_cm
        with self.assertRaises(ValueError):
            NotebookConfig(min_height_cm=100.0, max_height_cm=50.0)

        # No finitos
        with self.assertRaises(ValueError):
            NotebookConfig(min_height_cm=float("inf"))

    def test_prompt_guidance_and_contractual_prompt_builder(self):
        from src.fieldnotes.profiles.notebook import build_notebook_page_prompt
        cfg = NotebookConfig(
            header_aliases={"objetivo": ["meta"]},
            section_configs={
                "clima": NotebookSectionConfig(title="Condiciones Climáticas", aliases=["meteo"])
            },
        )
        prompt = build_notebook_page_prompt(
            page_number=1,
            config=cfg,
            user_prompt="Presta especial atención al instrumental utilizado.",
        )
        # El contrato base siempre está presente
        self.assertIn("page_number=1", prompt)
        self.assertIn("NUNCA generes código Mermaid, SVG ni Markdown", prompt)
        # Contiene las guías de configuración
        self.assertIn("Campo 'objetivo': aliases reconocidos = ['meta']", prompt)
        self.assertIn("Condiciones Climáticas", prompt)
        # Contiene la instrucción de usuario en bloque delimitado
        self.assertIn("--- INSTRUCCIÓN ADICIONAL DEL USUARIO ---", prompt)
        self.assertIn("Presta especial atención al instrumental utilizado.", prompt)
        self.assertIn("--- FIN INSTRUCCIÓN ADICIONAL ---", prompt)

    def test_from_file_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_path = Path(tmp_dir) / "config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "include_empty_sections": True,
                        "species_normalization": True,
                        "section_configs": {
                            "clima": {
                                "title": "Condiciones Meteorológicas",
                                "enabled": True,
                                "aliases": ["meteo", "clima"],
                                "include_if_empty": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = NotebookConfig.from_file(cfg_path)
            self.assertEqual(loaded.schema_version, 1)
            self.assertTrue(loaded.include_empty_sections)
            self.assertIn("clima", loaded.section_configs)
            self.assertEqual(loaded.section_configs["clima"].title, "Condiciones Meteorológicas")

    def test_from_file_nonexistent_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            NotebookConfig.from_file("non_existent_config_path_12345.json")


class TestNotebookSchemasAndDTO(unittest.TestCase):
    """Pruebas para DTOs y modelos canónicos de Notebook."""

    def test_dto_to_notebook_page_empty(self):
        dto = NotebookPageDTO(page_number=1)
        page = dto_to_notebook_page(dto, page_number=1)
        self.assertEqual(page.page_number, 1)
        self.assertIsNone(page.header)
        self.assertEqual(len(page.estadillo_rows), 0)
        self.assertEqual(len(page.sections), 0)
        self.assertEqual(len(page.notes), 0)
        self.assertEqual(len(page.tables), 0)
        self.assertEqual(len(page.diagrams), 0)

    def test_dto_page_number_mismatch_raises_error(self):
        dto = NotebookPageDTO(page_number=2)
        with self.assertRaises(ValueError):
            dto_to_notebook_page(dto, page_number=1)

    def test_dto_to_notebook_page_sections_and_notes(self):
        dto = NotebookPageDTO(
            page_number=1,
            sections=[
                NotebookSectionDTO(
                    title="Descripción del Sitio",
                    paragraphs=["Parcela situada en ladera norte.", "Pendiente del 15%."],
                    level=2,
                )
            ],
            notes=[
                NotebookNoteItemDTO(
                    text="Revisar trampa de feromonas el próximo martes.",
                    category="Tarea pendiente",
                )
            ],
        )
        page = dto_to_notebook_page(dto, page_number=1)
        self.assertEqual(len(page.sections), 1)
        sec = page.sections[0]
        self.assertEqual(sec.title, "Descripción del Sitio")
        self.assertEqual(len(sec.paragraphs), 2)
        self.assertEqual(sec.paragraphs[0].source_page, 1)
        self.assertEqual(sec.paragraphs[0].raw, "Parcela situada en ladera norte.")

        self.assertEqual(len(page.notes), 1)
        note = page.notes[0]
        self.assertEqual(note.category, "Tarea pendiente")
        self.assertEqual(note.text.source_page, 1)

    def test_dto_to_notebook_page_generic_table(self):
        dto = NotebookPageDTO(
            page_number=1,
            tables=[
                GenericTableDTO(
                    title="Conteo de Trampas",
                    headers=["Trampa", "Capturas", "Estado"],
                    rows=[["T1", "14", "Activa"], ["T2", "3", "Dañada"]],
                    caption="Revisión semanal",
                    uncertain_cells=[[1, 1]],
                )
            ],
        )
        page = dto_to_notebook_page(dto, page_number=1)
        self.assertEqual(len(page.tables), 1)
        tbl = page.tables[0]
        self.assertEqual(tbl.title, "Conteo de Trampas")
        self.assertEqual(len(tbl.headers), 3)
        self.assertEqual(len(tbl.rows), 2)
        self.assertEqual(tbl.rows[0][0].raw, "T1")
        self.assertFalse(tbl.rows[0][0].uncertain)
        self.assertTrue(tbl.rows[1][1].uncertain)
    def test_uncertain_cells_validation_strict(self):
        # Válido
        tbl = GenericTableDTO(
            rows=[["A", "B"], ["C", "D"]],
            uncertain_cells=[[0, 1], [1, 0]],
        )
        self.assertEqual(len(tbl.uncertain_cells), 2)

        # Formato inválido (3 elementos)
        with self.assertRaises(ValueError):
            GenericTableDTO(rows=[["A"]], uncertain_cells=[[0, 0, 1]])

        # Coordenada negativa
        with self.assertRaises(ValueError):
            GenericTableDTO(rows=[["A"]], uncertain_cells=[[-1, 0]])

        # Coordenada duplicada
        with self.assertRaises(ValueError):
            GenericTableDTO(rows=[["A", "B"]], uncertain_cells=[[0, 0], [0, 0]])

        # Fila fuera de rango
        with self.assertRaises(ValueError):
            GenericTableDTO(rows=[["A"]], uncertain_cells=[[5, 0]])

        # Columna fuera de rango
        with self.assertRaises(ValueError):
            GenericTableDTO(rows=[["A"]], uncertain_cells=[[0, 5]])

    def test_dto_to_notebook_page_preserves_empty_headers_and_cell_positions(self):
        dto = NotebookPageDTO(
            page_number=1,
            tables=[
                GenericTableDTO(
                    headers=["", "Col2", ""],
                    rows=[["", "Val2", "Val3"]],
                    uncertain_cells=[[0, 1]],
                )
            ],
        )
        page = dto_to_notebook_page(dto, page_number=1)
        self.assertEqual(len(page.tables), 1)
        tbl = page.tables[0]
        # Cabeceras preservan posiciones exactas (3 columnas)
        self.assertEqual(len(tbl.headers), 3)
        self.assertEqual(tbl.headers[0].raw, "")
        self.assertEqual(tbl.headers[1].raw, "Col2")
        self.assertEqual(tbl.headers[2].raw, "")
        # Celdas preservan posiciones exactas y duda
        self.assertEqual(len(tbl.rows[0]), 3)
        self.assertEqual(tbl.rows[0][0].raw, "")
        self.assertEqual(tbl.rows[0][1].raw, "Val2")
        self.assertTrue(tbl.rows[0][1].uncertain)
        self.assertEqual(tbl.rows[0][2].raw, "Val3")

    def test_notebook_document_to_document_ir(self):
        page = NotebookPage(
            page_number=1,
            header=EstadilloPageHeader(
                source_page=1,
                objetivo=EvidenceValue(raw="Control", normalized="Control", source_page=1),
            ),
            sections=[
                NotebookSection(
                    title="Notas generales",
                    paragraphs=[EvidenceValue(raw="Texto de prueba", normalized="Texto de prueba", source_page=1)],
                    source_page=1,
                )
            ],
        )
        doc = NotebookDocument(source_file="test.pdf", pages=[page])
        doc_ir = doc.to_document_ir()
        self.assertEqual(doc_ir.source_file, "test.pdf")
        self.assertEqual(len(doc_ir.pages), 1)
        self.assertEqual(doc_ir.pages[0].page_number, 1)
        self.assertGreaterEqual(len(doc_ir.pages[0].blocks), 2)


class TestNotebookMerge(unittest.TestCase):
    """Pruebas para la fusión pura de páginas en NotebookDocument."""

    def test_merge_empty_pages(self):
        doc = merge_notebook_pages([], source_file="doc.pdf")
        self.assertEqual(doc.source_file, "doc.pdf")
        self.assertEqual(len(doc.pages), 0)
        self.assertIsNone(doc.header)

    def test_merge_multi_page_ordering_and_header_reconciliation(self):
        p1 = NotebookPage(
            page_number=1,
            header=EstadilloPageHeader(
                source_page=1,
                objetivo=EvidenceValue(raw="Parcela A", normalized="Parcela A", source_page=1),
                fecha=EvidenceValue(raw="2026-05-10", normalized="2026-05-10", source_page=1),
            ),
            estadillo_rows=[
                EstadilloRow(
                    source_page=1,
                    id=EvidenceValue(raw="1", normalized="1", source_page=1),
                    col=EvidenceValue(raw="1", normalized=1, source_page=1),
                    fil=EvidenceValue(raw="1", normalized=1, source_page=1),
                )
            ],
        )
        p2 = NotebookPage(
            page_number=2,
            header=EstadilloPageHeader(
                source_page=2,
                objetivo=EvidenceValue(raw=" Parcela A ", normalized="Parcela A", source_page=2),
                asistentes=EvidenceValue(raw="Carlos, Ana", normalized="Carlos, Ana", source_page=2),
            ),
            estadillo_rows=[
                EstadilloRow(
                    source_page=2,
                    id=EvidenceValue(raw="2", normalized="2", source_page=2),
                    col=EvidenceValue(raw="1", normalized=1, source_page=2),
                    fil=EvidenceValue(raw="2", normalized=2, source_page=2),
                )
            ],
        )

        doc = merge_notebook_pages([p2, p1], source_file="ensayo.pdf")
        # Páginas ordenadas deterministamente por page_number
        self.assertEqual(doc.pages[0].page_number, 1)
        self.assertEqual(doc.pages[1].page_number, 2)

        # Reconciliación de cabecera sin conflicto
        self.assertIsNotNone(doc.header)
        self.assertEqual(doc.header.objetivo.normalized, "Parcela A")
        self.assertEqual(doc.header.fecha.normalized, "2026-05-10")
        self.assertEqual(doc.header.asistentes.normalized, "Carlos, Ana")

        # Filas agregadas en orden
        self.assertEqual(len(doc.estadillo_rows), 2)
        self.assertEqual(doc.estadillo_rows[0].id.normalized, "1")
        self.assertEqual(doc.estadillo_rows[1].id.normalized, "2")

    def test_merge_header_conflict_emits_warning(self):
        p1 = NotebookPage(
            page_number=1,
            header=EstadilloPageHeader(
                source_page=1,
                objetivo=EvidenceValue(raw="Parcela Norte", normalized="Parcela Norte", source_page=1),
            ),
        )
        p2 = NotebookPage(
            page_number=2,
            header=EstadilloPageHeader(
                source_page=2,
                objetivo=EvidenceValue(raw="Parcela Sur", normalized="Parcela Sur", source_page=2),
            ),
        )
        doc = merge_notebook_pages([p1, p2], source_file="conflicto.pdf")
        self.assertEqual(doc.header.objetivo.raw, "Parcela Norte")
        conflict_warns = [w for w in doc.warnings if w.code == "HEADER_CONFLICT"]
        self.assertEqual(len(conflict_warns), 1)
        self.assertEqual(conflict_warns[0].field_name, "objetivo")

    def test_merge_does_not_mutate_inputs(self):
        p1 = NotebookPage(page_number=1)
        p1_clone = p1.model_copy(deep=True)
        _ = merge_notebook_pages([p1], source_file="inmutabilidad.pdf")
        self.assertEqual(p1, p1_clone)


class TestNotebookValidation(unittest.TestCase):
    """Pruebas para las reglas puras de validación de NotebookDocument."""

    def test_species_normalization_valid(self):
        row1 = EstadilloRow(
            source_page=1,
            especie=EvidenceValue(raw="Ap", normalized=None, source_page=1),
        )
        row2 = EstadilloRow(
            source_page=1,
            especie=EvidenceValue(raw="ah", normalized=None, source_page=1),
        )
        row3 = EstadilloRow(
            source_page=1,
            especie=EvidenceValue(raw="AR", normalized=None, source_page=1),
        )
        row4 = EstadilloRow(
            source_page=1,
            especie=EvidenceValue(raw="mz", normalized=None, source_page=1),
        )
        page = NotebookPage(page_number=1, estadillo_rows=[row1, row2, row3, row4])
        doc = NotebookDocument(source_file="sp.pdf", pages=[page], estadillo_rows=[row1, row2, row3, row4])

        validated = validate_notebook_document(doc)
        self.assertEqual(validated.estadillo_rows[0].especie.normalized, "P")
        self.assertEqual(validated.estadillo_rows[1].especie.normalized, "H")
        self.assertEqual(validated.estadillo_rows[2].especie.normalized, "R")
        self.assertEqual(validated.estadillo_rows[3].especie.normalized, "M")

    def test_unrecognized_species_emits_warning(self):
        row = EstadilloRow(
            source_page=1,
            especie=EvidenceValue(raw="M2", normalized=None, source_page=1),
        )
        page = NotebookPage(page_number=1, estadillo_rows=[row])
        doc = NotebookDocument(source_file="sp.pdf", pages=[page], estadillo_rows=[row])

        validated = validate_notebook_document(doc)
        self.assertIsNone(validated.estadillo_rows[0].especie.normalized)
        self.assertTrue(validated.estadillo_rows[0].especie.uncertain)
        sp_warns = [w for w in validated.warnings if w.code == "UNRECOGNIZED_SPECIES"]
        self.assertEqual(len(sp_warns), 1)

    def test_duplicate_coordinates_detection(self):
        r1 = EstadilloRow(
            source_page=1,
            col=EvidenceValue(raw="1", normalized=1, source_page=1),
            fil=EvidenceValue(raw="5", normalized=5, source_page=1),
        )
        r2 = EstadilloRow(
            source_page=2,
            col=EvidenceValue(raw="1", normalized=1, source_page=2),
            fil=EvidenceValue(raw="5", normalized=5, source_page=2),
        )
        p1 = NotebookPage(page_number=1, estadillo_rows=[r1])
        p2 = NotebookPage(page_number=2, estadillo_rows=[r2])
        doc = NotebookDocument(source_file="dup.pdf", pages=[p1, p2], estadillo_rows=[r1, r2])

        validated = validate_notebook_document(doc)
        dup_warns = [w for w in validated.warnings if w.code == "DUPLICATE_COORDINATES"]
        self.assertEqual(len(dup_warns), 1)

    def test_irregular_table_row_detection(self):
        tbl = GenericTable(
            title="Inventario de Riego",
            headers=[
                EvidenceValue(raw="Sector", normalized="Sector", source_page=1),
                EvidenceValue(raw="Caudal", normalized="Caudal", source_page=1),
                EvidenceValue(raw="Presión", normalized="Presión", source_page=1),
            ],
            rows=[
                [EvidenceValue(raw="S1", normalized="S1", source_page=1), EvidenceValue(raw="10 l/s", normalized="10 l/s", source_page=1)],  # Fila con 2 celdas en vez de 3
            ],
            source_page=1,
        )
        page = NotebookPage(page_number=1, tables=[tbl])
        doc = NotebookDocument(source_file="tbl.pdf", pages=[page], tables=[tbl])

        validated = validate_notebook_document(doc)
        tbl_warns = [w for w in validated.warnings if w.code == "IRREGULAR_TABLE_ROW"]
        self.assertEqual(len(tbl_warns), 1)


class TestNotebookRender(unittest.TestCase):
    """Pruebas para el renderizado determinista de Markdown según AGENTS.md y PR11."""

    def test_initial_tables_exact_agents_structure_empty(self):
        doc = NotebookDocument(source_file="empty.pdf")
        md = render_notebook_markdown(doc)

        expected_initial = (
            "| Objetivo | Fecha | Asistentes |\n"
            "|---|---|---|\n"
            "|  |  |  |\n"
            "| Equipamiento:  | Situación atmosférica:  | Especies: P;H;R;M |\n\n"
            "|id|col|fil|especie|altura_cm|foto|bbch|observaciones|\n"
            "|---|---|---|---|---|---|---|---|\n"
        )
        self.assertTrue(md.startswith(expected_initial))

    def test_initial_tables_with_data(self):
        header = EstadilloDocHeader(
            objetivo=EvidenceValue(raw="Evaluación Fenológica", normalized="Evaluación Fenológica", source_page=1),
            fecha=EvidenceValue(raw="2026-05-15", normalized="2026-05-15", source_page=1),
            asistentes=EvidenceValue(raw="Juan Pérez", normalized="Juan Pérez", source_page=1),
            equipamiento=EvidenceValue(raw="Calibre digital", normalized="Calibre digital", source_page=1),
            situacion_atmosferica=EvidenceValue(raw="Despejado 22°C", normalized="Despejado 22°C", source_page=1),
        )
        row = EstadilloRow(
            source_page=1,
            id=EvidenceValue(raw="001", normalized="001", source_page=1),
            col=EvidenceValue(raw="1", normalized=1, source_page=1),
            fil=EvidenceValue(raw="1", normalized=1, source_page=1),
            especie=EvidenceValue(raw="P", normalized="P", source_page=1),
            altura_cm=EvidenceValue(raw="120.5", normalized=120.5, source_page=1),
            foto=EvidenceValue(raw="DSC001.JPG", normalized="DSC001.JPG", source_page=1),
            bbch=EvidenceValue(raw="65", normalized="65", source_page=1),
            observaciones=EvidenceValue(raw="Floración plena", normalized="Floración plena", source_page=1),
        )
        doc = NotebookDocument(
            source_file="campo.pdf",
            header=header,
            estadillo_rows=[row],
        )
        md = render_notebook_markdown(doc)

        expected_table1 = (
            "| Objetivo | Fecha | Asistentes |\n"
            "|---|---|---|\n"
            "| Evaluación Fenológica | 2026-05-15 | Juan Pérez |\n"
            "| Equipamiento: Calibre digital | Situación atmosférica: Despejado 22°C | Especies: P;H;R;M |\n"
        )
        expected_table2 = (
            "|id|col|fil|especie|altura_cm|foto|bbch|observaciones|\n"
            "|---|---|---|---|---|---|---|---|\n"
            "|001|1|1|P|120.5|DSC001.JPG|65|Floración plena|\n"
        )
        self.assertIn(expected_table1, md)
        self.assertIn(expected_table2, md)

    def test_flexible_sections_no_hallucination_of_absent_sections(self):
        doc = NotebookDocument(source_file="simple.pdf")
        md = render_notebook_markdown(doc)

        # No debe haber encabezados de secciones no observadas
        self.assertNotIn("## Objetivo", md)
        self.assertNotIn("## Condiciones", md)
        self.assertNotIn("## Notas", md)
        self.assertNotIn("## Observaciones", md)
        self.assertNotIn("## Mediciones", md)
        self.assertNotIn("## Croquis", md)

    def test_generic_table_escaping(self):
        tbl = GenericTable(
            title="Lecturas | Sensor A",
            headers=[
                EvidenceValue(raw="Sensor | ID", normalized="Sensor | ID", source_page=1),
                EvidenceValue(raw="Valor", normalized="Valor", source_page=1),
            ],
            rows=[
                [
                    EvidenceValue(raw="S|01", normalized="S|01", source_page=1),
                    EvidenceValue(raw="23.5\n°C", normalized="23.5\n°C", source_page=1),
                ]
            ],
            caption="Sensor | Parcela",
            source_page=1,
        )
        doc = NotebookDocument(source_file="tbl.pdf", tables=[tbl])
        md = render_notebook_markdown(doc)

        # Comprobar que los pipes están correctamente escapados en celdas y texto
        self.assertIn("### Lecturas \\| Sensor A", md)
        self.assertIn("|Sensor \\| ID|Valor|", md)
        self.assertIn("|S\\|01|23.5 °C|", md)
        self.assertIn("*Sensor \\| Parcela*", md)

    def test_render_generic_table_uniform_width_and_padding(self):
        # Caso 1: Sin cabecera explícita, filas de distinto ancho
        tbl1 = GenericTable(
            title="Mediciones Varias",
            headers=[],
            rows=[
                [EvidenceValue(raw="M1", normalized="M1", source_page=1)],
                [
                    EvidenceValue(raw="M2", normalized="M2", source_page=1),
                    EvidenceValue(raw="15.5", normalized="15.5", source_page=1),
                    EvidenceValue(raw="Aceptable", normalized="Aceptable", source_page=1),
                ],
            ],
            source_page=1,
        )
        doc1 = NotebookDocument(source_file="t1.pdf", tables=[tbl1])
        md1 = render_notebook_markdown(doc1)

        # Ancho uniforme = 3 columnas, cabecera neutral col_1|col_2|col_3
        self.assertIn("|col_1|col_2|col_3|", md1)
        self.assertIn("|---|---|---|", md1)
        self.assertIn("|M1|||", md1)
        self.assertIn("|M2|15.5|Aceptable|", md1)

        # Caso 2: Cabecera más larga que filas
        tbl2 = GenericTable(
            title="Inventario",
            headers=[
                EvidenceValue(raw="ID", normalized="ID", source_page=1),
                EvidenceValue(raw="Tipo", normalized="Tipo", source_page=1),
                EvidenceValue(raw="Notas", normalized="Notas", source_page=1),
            ],
            rows=[
                [EvidenceValue(raw="01", normalized="01", source_page=1)],  # 1 celda
            ],
            caption="Resumen de inventario",
            source_page=1,
        )
        doc2 = NotebookDocument(source_file="t2.pdf", tables=[tbl2])
        md2 = render_notebook_markdown(doc2)

        self.assertIn("|ID|Tipo|Notas|", md2)
        self.assertIn("|01|||", md2)
        self.assertIn("*Resumen de inventario*", md2)

    def test_render_determinism(self):
        header = EstadilloDocHeader(
            objetivo=EvidenceValue(raw="Ensayo", normalized="Ensayo", source_page=1)
        )
        row = EstadilloRow(
            source_page=1,
            id=EvidenceValue(raw="1", normalized="1", source_page=1),
            col=EvidenceValue(raw="1", normalized=1, source_page=1),
            fil=EvidenceValue(raw="1", normalized=1, source_page=1),
            especie=EvidenceValue(raw="P", normalized="P", source_page=1),
        )
        sec = NotebookSection(
            title="Conclusiones",
            paragraphs=[EvidenceValue(raw="Resultados positivos.", normalized="Resultados positivos.", source_page=1)],
            source_page=1,
        )
        doc = NotebookDocument(source_file="det.pdf", header=header, estadillo_rows=[row], sections=[sec])

        md1 = render_notebook_markdown(doc)
        md2 = render_notebook_markdown(doc)
        self.assertEqual(md1, md2)


class TestNotebookProfilePersistenceAndRollback(unittest.TestCase):
    """Pruebas de persistencia atómica, confinamiento y rollback para NotebookProfile."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_nb_profile_")
        self.output_base = Path(self.test_dir) / "output_ocr"
        self.output_base.mkdir(parents=True, exist_ok=True)

        # Crear archivo dummy de entrada
        self.input_file = Path(self.test_dir) / "cuaderno_campo.png"
        self.input_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_successful_profile_run_persists_canonical_layout(self):
        agent = MagicMock()
        agent.base_output_dir = str(self.output_base)
        agent.output_dir = str(Path(self.test_dir) / "agent_temp")
        Path(agent.output_dir).mkdir(parents=True, exist_ok=True)

        # Mock PageArtifact
        art = PageArtifact(
            page_number=1,
            image_path=self.input_file,
            raw_ocr="Texto OCR preliminar",
        )
        agent.page_artifacts = [art]

        # Mock VLM structured response
        dto_response = NotebookPageDTO(
            page_number=1,
            header=EstadilloHeaderDTO(objetivo="Muestreo 2026"),
            sections=[NotebookSectionDTO(title="Notas", paragraphs=["Día soleado."])],
        )
        agent.ask_page_vision_structured.return_value = dto_response

        profile = NotebookProfile(agent=agent, output_base_dir=self.output_base)
        md_res, doc_res = profile.run(self.input_file)

        canonical_dir = self.output_base / "cuaderno_campo"
        self.assertTrue(canonical_dir.is_dir())
        self.assertTrue((canonical_dir / "notebook.md").is_file())
        self.assertTrue((canonical_dir / "document.json").is_file())
        self.assertTrue((canonical_dir / "pages" / "page_001.png").is_file())
        self.assertTrue((canonical_dir / "raw" / "page_001.md").is_file())
        self.assertTrue((canonical_dir / "review" / "issues.json").is_file())

        # Verificar contenido de notebook.md
        saved_md = (canonical_dir / "notebook.md").read_text(encoding="utf-8")
        self.assertTrue(saved_md.startswith("| Objetivo | Fecha | Asistentes |"))
        self.assertIn("Muestreo 2026", saved_md)
        self.assertIn("Día soleado.", saved_md)

    def test_pre_swap_staging_failure_leaves_previous_canonical_intact_and_cleans_staging(self):
        canonical_dir = self.output_base / "cuaderno_campo"
        canonical_dir.mkdir(parents=True, exist_ok=True)
        sentinel_file = canonical_dir / "original_data.txt"
        sentinel_bytes = b"DATOS_ORIGINALES_PREVIOS_EXACTOS_12345"
        sentinel_file.write_bytes(sentinel_bytes)

        agent = MagicMock()
        agent.base_output_dir = str(self.output_base)
        agent.output_dir = str(Path(self.test_dir) / "agent_temp")
        Path(agent.output_dir).mkdir(parents=True, exist_ok=True)

        art = PageArtifact(page_number=1, image_path=self.input_file, raw_ocr="OCR")
        agent.page_artifacts = [art]
        agent.ask_page_vision_structured.return_value = NotebookPageDTO(page_number=1)

        profile = NotebookProfile(agent=agent, output_base_dir=self.output_base)

        # Simular fallo en fase staging (antes del swap/backup, ej. en write_atomic_file)
        with patch("src.fieldnotes.profiles.notebook.write_atomic_file", side_effect=OSError("Fallo de escritura en staging")):
            with self.assertRaises(OSError):
                profile.run(self.input_file)

        # 1. El directorio canónico previo existe y está byte a byte idéntico
        self.assertTrue(canonical_dir.is_dir())
        self.assertTrue(sentinel_file.is_file())
        self.assertEqual(sentinel_file.read_bytes(), sentinel_bytes)

        # 2. No quedan directorios de staging ni de backup huérfanos en output_base
        remaining_dirs = [d.name for d in self.output_base.iterdir() if d.is_dir()]
        self.assertEqual(remaining_dirs, ["cuaderno_campo"])

    def test_rollback_on_swap_failure_restores_existing_canonical(self):
        canonical_dir = self.output_base / "cuaderno_campo"
        canonical_dir.mkdir(parents=True, exist_ok=True)
        sentinel_file = canonical_dir / "original_data.txt"
        sentinel_file.write_text("DATOS_ORIGINALES_PREVIOS", encoding="utf-8")

        agent = MagicMock()
        agent.base_output_dir = str(self.output_base)
        agent.output_dir = str(Path(self.test_dir) / "agent_temp")
        Path(agent.output_dir).mkdir(parents=True, exist_ok=True)

        art = PageArtifact(page_number=1, image_path=self.input_file, raw_ocr="OCR")
        agent.page_artifacts = [art]
        agent.ask_page_vision_structured.return_value = NotebookPageDTO(page_number=1)

        profile = NotebookProfile(agent=agent, output_base_dir=self.output_base)

        real_move = shutil.move
        move_call_count = [0]

        def failing_swap_moves(src, dst):
            move_call_count[0] += 1
            if move_call_count[0] == 1:
                # 1er move: canonical -> backup (éxito)
                return real_move(src, dst)
            elif move_call_count[0] == 2:
                # 2do move: staging -> canonical (falla)
                raise OSError("Fallo simulado en swap a canonical")
            else:
                # 3er move: backup -> canonical (restauración en rollback)
                return real_move(src, dst)

        with patch("shutil.move", side_effect=failing_swap_moves):
            with self.assertRaises(OSError):
                profile.run(self.input_file)

        # La versión original debe haber sido restaurada
        self.assertTrue(canonical_dir.is_dir())
        self.assertTrue(sentinel_file.is_file())
        self.assertEqual(sentinel_file.read_text(encoding="utf-8"), "DATOS_ORIGINALES_PREVIOS")

    def test_failed_rollback_raises_notebook_rollback_error_preserving_backup(self):
        canonical_dir = self.output_base / "cuaderno_campo"
        canonical_dir.mkdir(parents=True, exist_ok=True)
        sentinel_file = canonical_dir / "original_data.txt"
        sentinel_file.write_text("DATOS_ORIGINALES_PREVIOS", encoding="utf-8")

        agent = MagicMock()
        agent.base_output_dir = str(self.output_base)
        agent.output_dir = str(Path(self.test_dir) / "agent_temp")
        Path(agent.output_dir).mkdir(parents=True, exist_ok=True)

        art = PageArtifact(page_number=1, image_path=self.input_file, raw_ocr="OCR")
        agent.page_artifacts = [art]
        agent.ask_page_vision_structured.return_value = NotebookPageDTO(page_number=1)

        profile = NotebookProfile(agent=agent, output_base_dir=self.output_base)

        real_move = shutil.move
        move_call_count = [0]

        def failing_moves(src, dst):
            move_call_count[0] += 1
            if move_call_count[0] == 1:
                # canonical -> backup
                return real_move(src, dst)
            elif move_call_count[0] == 2:
                # staging -> canonical
                raise OSError("Fallo en swap")
            else:
                # backup -> canonical en rollback (también falla)
                raise OSError("Fallo catastrófico en restauración de backup")

        with patch("shutil.move", side_effect=failing_moves):
            with self.assertRaises(NotebookRollbackError) as ctx:
                profile.run(self.input_file)

            # El error conserva el backup intacto para recuperación forense
            self.assertIsNotNone(ctx.exception.backup_dir)
            self.assertTrue(ctx.exception.backup_dir.exists())


class TestNotebookCLI(unittest.TestCase):
    """Pruebas de validación temprana e invocación en CLI para --profile notebook."""

    def test_cli_parser_options(self):
        parser = build_parser()
        args = parser.parse_args(["imagen.png", "--profile", "notebook", "--vision-model", "qwen-vl"])
        self.assertEqual(args.profile, "notebook")
        self.assertEqual(args.vision_model, "qwen-vl")

    def test_cli_incompatible_options_rejected(self):
        incompatible_flags = [
            ["doc.png", "--profile", "notebook", "--raw", "--vision-model", "vlm"],
            ["doc.png", "--profile", "notebook", "--ask-vision", "--vision-model", "vlm"],
            ["doc.png", "--profile", "notebook", "--chunk-size", "1000", "--vision-model", "vlm"],
            ["doc.png", "--profile", "notebook", "--export-md", "out.md", "--vision-model", "vlm"],
            ["doc.png", "--profile", "notebook", "--export-pdf", "out.pdf", "--vision-model", "vlm"],
        ]
        for flag_list in incompatible_flags:
            with self.subTest(flags=flag_list):
                with self.assertRaises(SystemExit) as ctx:
                    with patch("sys.stderr"):
                        main(flag_list)
                self.assertNotEqual(ctx.exception.code, 0)

    def test_cli_missing_vision_model_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch.dict(os.environ, {}, clear=True):
                with patch("sys.stderr"):
                    main(["doc.png", "--profile", "notebook"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_cli_config_only_with_notebook_profile(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr"):
                main(["doc.png", "--profile", "estadillo", "--config", "cfg.json", "--vision-model", "vlm"])
        self.assertNotEqual(ctx.exception.code, 0)


class TestNotebookDiagramIntegration(unittest.TestCase):
    """Pruebas de integración de DiagramIR (Mermaid y SVG) en el perfil Notebook."""

    def test_multipage_diagram_assets_and_accessibility(self):
        with tempfile.TemporaryDirectory() as tmp_base:
            output_base = Path(tmp_base)
            input_file = output_base / "ensayo_croquis.png"
            input_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

            # Mock agent con 2 páginas
            agent = MagicMock()
            agent.base_output_dir = str(output_base)
            agent.output_dir = str(output_base / "temp_run")
            Path(agent.output_dir).mkdir(parents=True, exist_ok=True)

            art1 = PageArtifact(page_number=1, image_path=input_file, raw_ocr="P1")
            art2 = PageArtifact(page_number=2, image_path=input_file, raw_ocr="P2")
            agent.page_artifacts = [art1, art2]

            # 2 diagramas en Página 1 (1 sketch, 1 flowchart)
            diag_p1_0 = DiagramDTO(
                source_page=1,
                diagram_type="field_sketch",
                title="Croquis Parcela 1",
                description="Zonas de muestreo",
                points=[PointEntityDTO(id="pt1", coordinate=Point2DDTO(x=0.1, y=0.2), label="Punto 1")],
            )
            diag_p1_1 = DiagramDTO(
                source_page=1,
                diagram_type="flowchart",
                title="Protocolo P1",
                points=[PointEntityDTO(id="f1", coordinate=Point2DDTO(x=0.5, y=0.5), label="Paso 1")],
            )
            # 2 diagramas en Página 2 (1 sketch con mismo título pero en p2, 1 flowchart)
            diag_p2_0 = DiagramDTO(
                source_page=2,
                diagram_type="field_sketch",
                title="Croquis Parcela 1",  # Mismo título
                description="Replanteo de la misma parcela en p2",
                points=[PointEntityDTO(id="pt1", coordinate=Point2DDTO(x=0.3, y=0.4), label="Punto 1 en P2")],
            )
            diag_p2_1 = DiagramDTO(
                source_page=2,
                diagram_type="flowchart",
                title="Protocolo P2",
                points=[PointEntityDTO(id="f2", coordinate=Point2DDTO(x=0.5, y=0.8), label="Paso 2")],
            )

            p1_dto = NotebookPageDTO(page_number=1, diagrams=[diag_p1_0, diag_p1_1])
            p2_dto = NotebookPageDTO(page_number=2, diagrams=[diag_p2_0, diag_p2_1])
            agent.ask_page_vision_structured.side_effect = [p1_dto, p2_dto]

            profile = NotebookProfile(agent=agent, output_base_dir=output_base)
            md_res, doc_res = profile.run(input_file)

            canonical_dir = output_base / "ensayo_croquis"
            self.assertTrue(canonical_dir.is_dir())
            assets_dir = canonical_dir / "assets"

            # Verificar que los archivos existen físicamente con nombres de página e índice exactos
            self.assertTrue((assets_dir / "sketch_p001_00.svg").is_file())
            self.assertTrue((assets_dir / "diagram_p001_01.mmd").is_file())
            self.assertTrue((assets_dir / "sketch_p002_00.svg").is_file())
            self.assertTrue((assets_dir / "diagram_p002_01.mmd").is_file())

            # Comprobar enlaces en Markdown
            self.assertIn("![Croquis Parcela 1](assets/sketch_p001_00.svg)", md_res)
            self.assertIn("![Croquis Parcela 1](assets/sketch_p002_00.svg)", md_res)

            # Comprobar que los flowcharts NUNCA producen enlaces falsos a SVG
            self.assertNotIn("diagram_p001_01.svg", md_res)
            self.assertNotIn("diagram_p002_01.svg", md_res)
            # Y que incrustan el bloque Mermaid
            self.assertIn("```mermaid", md_res)
            self.assertIn("Paso 1", md_res)
            self.assertIn("Paso 2", md_res)

    def test_render_diagram_visuals_false_omits_image_links_but_keeps_accessible_text(self):
        diag = DiagramDTO(
            source_page=1,
            diagram_type="field_sketch",
            title="Croquis Sin SVG",
            points=[PointEntityDTO(id="pt1", coordinate=Point2DDTO(x=0.2, y=0.3), label="Hito A")],
        )
        canonical_diag = dto_to_diagram_ir(diag, source_page=1)
        doc = NotebookDocument(source_file="d.pdf", diagrams=[canonical_diag])

        cfg = NotebookConfig(render_diagram_visuals=False)
        md = render_notebook_markdown(doc, config=cfg)

        # NO debe haber enlaces a imágenes
        self.assertNotIn("![", md)
        self.assertNotIn(".svg", md)
        # SÍ debe haber descripción textual accesible
        self.assertIn("### Croquis Sin SVG", md)
        self.assertIn("#### Descripción textual", md)
        self.assertIn("Hito A", md)


class TestNotebookReviewExtraction(unittest.TestCase):
    """Pruebas para la extracción de incidencias de revisión en NotebookDocument."""

    def test_extract_review_issues_all_entities(self):
        from src.fieldnotes.review.issues import extract_review_issues_from_document

        page = NotebookPage(
            page_number=1,
            header=EstadilloPageHeader(
                source_page=1,
                objetivo=EvidenceValue(raw="Incierto?", normalized=None, source_page=1, uncertain=True, alternatives=["Parcela 1", "Parcela 2"]),
            ),
            sections=[
                NotebookSection(
                    title="Sección Dudosa",
                    paragraphs=[EvidenceValue(raw="Párrafo dudoso", normalized="Párrafo dudoso", source_page=1, uncertain=True)],
                    source_page=1,
                    uncertain=True,
                )
            ],
            notes=[
                NotebookNoteItem(
                    text=EvidenceValue(raw="Nota dudosa", normalized="Nota dudosa", source_page=1, uncertain=True),
                    source_page=1,
                    uncertain=True,
                )
            ],
            tables=[
                GenericTable(
                    title="Tabla Dudosa",
                    headers=[EvidenceValue(raw="Col1", normalized="Col1", source_page=1)],
                    rows=[[EvidenceValue(raw="Val?", normalized=None, source_page=1, uncertain=True, alternatives=["ValA", "ValB"])]],
                    source_page=1,
                    uncertain=True,
                )
            ],
        )
        doc = NotebookDocument(
            source_file="rev.pdf",
            pages=[page],
            header=EstadilloDocHeader(
                objetivo=EvidenceValue(raw="Incierto?", normalized=None, source_page=1, uncertain=True, alternatives=["Parcela 1", "Parcela 2"]),
            ),
            sections=page.sections,
            notes=page.notes,
            tables=page.tables,
            warnings=[ExtractionWarning(code="TEST_WARNING", message="Advertencia global de test", severity="warning")],
        )

        issues = extract_review_issues_from_document(doc)
        self.assertGreaterEqual(len(issues), 5)

        codes = {it.warning_code for it in issues}
        self.assertIn("UNCERTAIN_EVIDENCE", codes)
        self.assertIn("UNCERTAIN_SECTION", codes)
        self.assertIn("UNCERTAIN_PARAGRAPH", codes)
        self.assertIn("UNCERTAIN_NOTE", codes)
        self.assertIn("UNCERTAIN_TABLE", codes)
        self.assertIn("UNCERTAIN_CELL", codes)
        self.assertIn("TEST_WARNING", codes)


class TestNotebookDeduplicationAndCustomSections(unittest.TestCase):
    """Pruebas para deduplicación de secciones y configuración personalizada."""

    def test_section_configs_controls_selection_renaming_and_ordering(self):
        cfg = NotebookConfig(
            section_configs={
                "meteo": NotebookSectionConfig(
                    title="Condiciones Climáticas",
                    aliases=["clima", "tiempo"],
                    render_order=1,
                ),
                "suelo": NotebookSectionConfig(
                    title="Análisis del Suelo",
                    aliases=["edafologia"],
                    render_order=2,
                ),
                "descartada": NotebookSectionConfig(
                    title="Borrador",
                    aliases=["draft"],
                    enabled=False,  # Debe ser excluida de Markdown sin borrarla de NotebookDocument
                ),
            }
        )
        s_borrador = NotebookSection(title="Draft", content="Texto borrador descartable", source_page=1)
        s_suelo = NotebookSection(title="Edafologia", content="Suelo arcilloso", source_page=1)
        s_clima = NotebookSection(title="Tiempo", content="Viento moderado 15 km/h", source_page=1)

        doc = NotebookDocument(
            source_file="sections.pdf",
            sections=[s_borrador, s_suelo, s_clima],
        )

        md = render_notebook_markdown(doc, config=cfg)

        # La sección deshabilitada NO debe aparecer en Markdown
        self.assertNotIn("Borrador", md)
        self.assertNotIn("Texto borrador descartable", md)

        # Las secciones se renombran a su título canónico y se ordenan por render_order (Clima antes de Suelo)
        pos_clima = md.find("## Condiciones Climáticas")
        pos_suelo = md.find("## Análisis del Suelo")
        self.assertNotEqual(pos_clima, -1)
        self.assertNotEqual(pos_suelo, -1)
        self.assertLess(pos_clima, pos_suelo, "Condiciones Climáticas (order=1) debe preceder a Análisis del Suelo (order=2)")

        # La evidencia canónica en doc.sections NO muta
        self.assertEqual(len(doc.sections), 3)
        self.assertEqual(doc.sections[0].title, "Draft")

    def test_header_deduplication_exact_and_labeled(self):
        header = EstadilloDocHeader(
            objetivo=EvidenceValue(raw="Control fenológico", normalized="Control fenológico", source_page=1),
            fecha=EvidenceValue(raw="2026-05-15", normalized="2026-05-15", source_page=1),
        )
        # Sección con contenido idéntico al objetivo
        s_dup1 = NotebookSection(title="Objetivo", content="Control fenológico", source_page=1)
        # Sección con variante etiquetada "Objetivo: Control fenológico"
        s_dup2 = NotebookSection(title="Resumen", content="Objetivo: Control fenológico", source_page=1)
        # Sección con texto parcialmente distinto que SÍ debe conservarse
        s_keep = NotebookSection(title="Objetivo Detallado", content="Control fenológico en parcelas de ensayo norte", source_page=1)

        doc = NotebookDocument(source_file="dedup.pdf", header=header, sections=[s_dup1, s_dup2, s_keep])
        md = render_notebook_markdown(doc)

        # La tabla 1 tiene el objetivo
        self.assertIn("| Control fenológico | 2026-05-15 |", md)
        # Las secciones duplicadas no se re-emiten
        self.assertNotIn("## Resumen", md)
        # La sección con información distinta sí se emite
        self.assertIn("## Objetivo Detallado", md)
        self.assertIn("Control fenológico en parcelas de ensayo norte", md)

    def test_include_empty_sections_when_configured(self):
        cfg = NotebookConfig(
            include_empty_sections=True,
            section_configs={
                "clima": NotebookSectionConfig(
                    title="Condiciones Climáticas",
                    enabled=True,
                    include_if_empty=True,
                )
            },
        )
        doc = NotebookDocument(source_file="empty_sec.pdf")
        md = render_notebook_markdown(doc, config=cfg)

        self.assertIn("## Condiciones Climáticas", md)
        self.assertIn("*(Sin contenido observado [configuración])*", md)


class TestRealNotebookProfileIntegration(unittest.TestCase):
    """Prueba opt-in de integración real E2E del perfil notebook (desactivada por defecto)."""

    def test_real_notebook_e2e_on_sample(self):
        flag = os.environ.get("RUN_NOTEBOOK_INTEGRATION")
        if flag != "1":
            self.skipTest(
                "Prueba E2E real del perfil notebook desactivada por defecto; "
                "requiere RUN_NOTEBOOK_INTEGRATION=1"
            )

        model_name = os.environ.get("LM_STUDIO_VISION_MODEL", "qwen/qwen3.5-9b")
        pdf_path = Path("26-05-06.pdf")
        if not pdf_path.is_file():
            self.fail(f"Archivo '{pdf_path}' no encontrado para la prueba de integración.")

        temp_output = tempfile.mkdtemp(prefix="test_e2e_notebook_")
        try:
            from src.fieldnotes.pipeline import UnlimitedOCRAgent
            agent = UnlimitedOCRAgent(
                vision_model=model_name,
                output_dir=temp_output,
                ocr_mode="worker",
            )

            md_res, doc_res = agent.process_notebook(
                file_path=pdf_path,
                output_dir=temp_output,
                max_tokens=4096,
            )

            self.assertIsInstance(doc_res, NotebookDocument)
            self.assertEqual(len(doc_res.pages), 6)

            safe_dir = Path(temp_output) / "26-05-06"
            self.assertTrue(safe_dir.is_dir())
            self.assertTrue((safe_dir / "notebook.md").is_file())
            self.assertTrue((safe_dir / "document.json").is_file())

            # Inicio exacto con dos tablas de AGENTS.md
            lines = md_res.splitlines()
            self.assertEqual(lines[0], "| Objetivo | Fecha | Asistentes |")
            self.assertIn("Especies: P;H;R;M", lines[3])
            self.assertIn("|id|col|fil|especie|altura_cm|foto|bbch|observaciones|", lines[5])
        finally:
            shutil.rmtree(temp_output, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()