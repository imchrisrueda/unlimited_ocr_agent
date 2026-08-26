import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.estadillo import (
    EstadilloPageHeader,
    EstadilloDocHeader,
    EstadilloHeader,
    EstadilloRow,
    EstadilloPage,
    EstadilloDocument,
)
from src.fieldnotes.schemas.dto import (
    EstadilloPageDTO,
    EstadilloRowDTO,
    EstadilloHeaderDTO,
)
from src.fieldnotes.artifacts import PageArtifact
from src.fieldnotes.profiles.estadillo import (
    EstadilloProfile,
    ESTADILLO_PROMPT_TEMPLATE,
    safe_document_stem,
    write_atomic_file,
)
from src.fieldnotes.pipeline import UnlimitedOCRAgent
from src.fieldnotes.cli import main as cli_main
from src.fieldnotes.vlm.errors import (
    LMStudioEmptyResponseError,
    LMStudioResponseTruncatedError,
    LMStudioConnectionError,
    LMStudioModelNotFoundError,
    StructuredOutputValidationError,
    StructuredOutputParseError,
)


class TestEstadilloProfileUnit(unittest.TestCase):
    def test_prompt_contract_core_clauses(self):
        """Verifica que el prompt contractual incluya todas las cláusulas requeridas."""
        self.assertIn("imagen es la fuente primaria", ESTADILLO_PROMPT_TEMPLATE)
        self.assertIn("OCR suministrado es una hipótesis auxiliar", ESTADILLO_PROMPT_TEMPLATE)
        self.assertIn("EstadilloPageDTO", ESTADILLO_PROMPT_TEMPLATE)
        self.assertIn("page_number={page_number}", ESTADILLO_PROMPT_TEMPLATE)
        self.assertIn("No inventes datos", ESTADILLO_PROMPT_TEMPLATE)
        self.assertIn("uncertain_fields", ESTADILLO_PROMPT_TEMPLATE)
        self.assertIn("additional_text", ESTADILLO_PROMPT_TEMPLATE)

    def test_safe_document_stem_normalization_and_reserved_names(self):
        self.assertEqual(safe_document_stem("sample.pdf"), "sample")
        self.assertEqual(safe_document_stem("26-05-06.pdf"), "26-05-06")
        self.assertEqual(safe_document_stem("folder/sub/my file 2026.png"), "my_file_2026")
        self.assertEqual(safe_document_stem("..."), "document")
        self.assertEqual(safe_document_stem("   "), "document")
        self.assertEqual(safe_document_stem("doc<test>:2026?.pdf"), "doc_test_2026")

        # Nombres reservados de Windows
        self.assertEqual(safe_document_stem("CON.pdf"), "doc_CON")
        self.assertEqual(safe_document_stem("prn.txt"), "doc_prn")
        self.assertEqual(safe_document_stem("NUL"), "doc_NUL")
        self.assertEqual(safe_document_stem("lpt1.pdf"), "doc_lpt1")
        self.assertEqual(safe_document_stem("com9.pdf"), "doc_com9")

        # Límite de longitud máxima acotada a <= 100 caracteres
        long_name = "a" * 150 + ".pdf"
        stem = safe_document_stem(long_name)
        self.assertLessEqual(len(stem), 100)
        self.assertTrue(stem.startswith("a"))

    def test_write_atomic_file_success_and_failure_cleanup(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="test_atomic_"))
        try:
            target = temp_dir / "test.txt"
            write_atomic_file(target, "contenido atómico inicial")
            self.assertEqual(target.read_text(encoding="utf-8"), "contenido atómico inicial")

            # Fallo provocado en reemplazo limpia archivo temporal y no deja basura
            with patch("os.replace", side_effect=OSError("Disk write error")):
                with self.assertRaises(OSError):
                    write_atomic_file(target, "contenido fallido")

            # Archivo original intacto
            self.assertEqual(target.read_text(encoding="utf-8"), "contenido atómico inicial")
            # Sin archivos temporales .tmp_*
            tmp_files = list(temp_dir.glob(".tmp_*"))
            self.assertEqual(len(tmp_files), 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_staging_failure_leaves_no_canonical_directory(self):
        temp_base = Path(tempfile.mkdtemp(prefix="test_staging_fail_"))
        temp_input = temp_base / "nuevo_doc.pdf"
        temp_input.write_text("fake pdf content", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")

        img1 = temp_base / "p1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR p1")
        art2 = PageArtifact(page_number=2, image_path=img1, raw_ocr="OCR p2")
        mock_agent.page_artifacts = [art1, art2]

        # Simular fallo en la segunda página
        mock_agent.ask_page_vision_structured.side_effect = [
            EstadilloPage(page_number=1),
            RuntimeError("VLM connection failure on page 2"),
        ]

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            with self.assertRaises(RuntimeError):
                profile.run(temp_input)

            # No debe haberse creado el directorio canónico 'nuevo_doc'
            canonical_dir = temp_base / "nuevo_doc"
            self.assertFalse(canonical_dir.exists())

            # No debe quedar ningún directorio de staging
            staging_dirs = list(temp_base.glob(".staging_*"))
            self.assertEqual(len(staging_dirs), 0)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_staging_failure_during_rerun_preserves_previous_canonical_bytes(self):
        temp_base = Path(tempfile.mkdtemp(prefix="test_rerun_fail_"))
        temp_input = temp_base / "doc_existente.pdf"
        temp_input.write_text("fake pdf content", encoding="utf-8")

        canonical_dir = temp_base / "doc_existente"
        canonical_dir.mkdir(parents=True)
        notebook_path = canonical_dir / "notebook.md"
        notebook_path.write_text("VERSION CANONICA ANTERIOR", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = str(temp_base)
        mock_agent.output_dir = str(temp_base / "ocr_run")

        img1 = temp_base / "p1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR p1")
        mock_agent.page_artifacts = [art1]

        # Simular fallo en VLM
        mock_agent.ask_page_vision_structured.side_effect = RuntimeError("VLM Error")

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            with self.assertRaises(RuntimeError):
                profile.run(temp_input)

            # El directorio canónico previo y sus bytes deben permanecer intactos
            self.assertTrue(canonical_dir.exists())
            self.assertEqual(notebook_path.read_text(encoding="utf-8"), "VERSION CANONICA ANTERIOR")

            # Sin directorios de staging ni backups huérfanos
            self.assertEqual(len(list(temp_base.glob(".staging_*"))), 0)
            self.assertEqual(len(list(temp_base.glob(".backup_*"))), 0)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_mocked_estadillo_profile_run_and_canonical_persistence(self):
        temp_base = tempfile.mkdtemp(prefix="test_profile_")
        temp_input = Path(temp_base) / "estadillo_sample.pdf"
        temp_input.write_text("fake pdf content", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = temp_base
        mock_agent.output_dir = os.path.join(temp_base, "ocr_run")
        os.makedirs(os.path.join(mock_agent.output_dir, "raw"), exist_ok=True)

        img1 = Path(temp_base) / "page_001.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR p1")
        mock_agent.page_artifacts = [art1]

        mock_page1 = EstadilloPage(
            page_number=1,
            header=EstadilloPageHeader(
                objetivo=EvidenceValue[str](raw="Ensayo", source_page=1),
                source_page=1,
            ),
            rows=[
                EstadilloRow(
                    col=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                    fil=EvidenceValue[int](raw="1", normalized=1, source_page=1),
                    especie=EvidenceValue[str](raw="Ap", source_page=1),
                    altura_cm=EvidenceValue[float](raw="140", normalized=140.0, source_page=1),
                    source_page=1,
                )
            ],
        )
        mock_agent.ask_page_vision_structured.return_value = mock_page1

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            md_res, doc_res = profile.run(temp_input)

            # Verificar normalización aplicada
            self.assertEqual(doc_res.pages[0].rows[0].especie.normalized, "P")

            # Layout canónico
            doc_dir = Path(temp_base) / "estadillo_sample"
            self.assertTrue(doc_dir.is_dir())
            self.assertTrue((doc_dir / "notebook.md").is_file())
            self.assertTrue((doc_dir / "document.json").is_file())
            self.assertTrue((doc_dir / "pages").is_dir())
            self.assertTrue((doc_dir / "raw").is_dir())

            # Roundtrip de document.json
            loaded_doc = EstadilloDocument.model_validate_json((doc_dir / "document.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded_doc.source_file, str(temp_input.resolve()))
            self.assertEqual(loaded_doc.header.objetivo.raw, "Ensayo")

            # notebook.md comienza con las 2 tablas obligatorias
            lines = (doc_dir / "notebook.md").read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "| Objetivo | Fecha | Asistentes |")
            self.assertIn("|id|col|fil|especie|altura_cm|foto|bbch|observaciones|", lines[5])
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_cli_incompatible_flags_early_rejection(self):
        """Verifica que combinaciones incompatibles con --profile estadillo sean rechazadas inmediatamente."""
        incompatible_args = [
            ["sample.pdf", "--profile", "estadillo", "--raw", "--vision-model", "test-m"],
            ["sample.pdf", "--profile", "estadillo", "--ask-vision", "--vision-model", "test-m"],
            ["sample.pdf", "--profile", "estadillo", "--chunk-size", "1000", "--vision-model", "test-m"],
            ["sample.pdf", "--profile", "estadillo", "--export-md", "out.md", "--vision-model", "test-m"],
            ["sample.pdf", "--profile", "estadillo", "--export-pdf", "out.pdf", "--vision-model", "test-m"],
            ["sample.pdf", "--profile", "estadillo"],  # Sin modelo de visión configurado
        ]

        with patch.dict(os.environ, {}, clear=True):
            for arg_list in incompatible_args:
                with self.assertRaises(SystemExit):
                    with patch("sys.stderr"):
                        cli_main(arg_list)

    def test_effective_arguments_and_user_kwargs_forwarding(self):
        """Verifica los argumentos efectivos por defecto (extra_body thinking deshabilitado, reasoning_effort=none, max_tokens=4096, schema=EstadilloPageDTO) y el reenvío de kwargs explícitos."""
        temp_base = tempfile.mkdtemp(prefix="test_kwargs_")
        temp_input = Path(temp_base) / "doc.pdf"
        temp_input.write_text("fake pdf content", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = temp_base
        mock_agent.output_dir = os.path.join(temp_base, "ocr_run")
        os.makedirs(os.path.join(mock_agent.output_dir, "raw"), exist_ok=True)

        img1 = Path(temp_base) / "p1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        mock_page_dto = EstadilloPageDTO(
            page_number=1,
            header=EstadilloHeaderDTO(objetivo="Test"),
            rows=[],
        )
        mock_agent.ask_page_vision_structured.return_value = mock_page_dto

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)

            # 1. Sin kwargs explícitos: extra_body con thinking deshabilitado, reasoning_effort="none", schema=EstadilloPageDTO y max_tokens=4096 por defecto
            profile.run(temp_input)
            mock_agent.ask_page_vision_structured.assert_called_once()
            call_kwargs = mock_agent.ask_page_vision_structured.call_args.kwargs
            self.assertEqual(call_kwargs["reasoning_effort"], "none")
            self.assertEqual(call_kwargs["max_tokens"], 4096)
            self.assertEqual(call_kwargs["schema"], EstadilloPageDTO)
            self.assertIn("extra_body", call_kwargs)
            self.assertEqual(call_kwargs["extra_body"]["enable_thinking"], False)
            self.assertEqual(call_kwargs["extra_body"]["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual(call_kwargs["extra_body"]["reasoning_effort"], "none")

            mock_agent.ask_page_vision_structured.reset_mock()

            # 2. Con kwargs explícitos del usuario: se preservan fielmente
            profile.run(
                temp_input,
                max_tokens=2048,
                reasoning_effort="high",
                temperature=0.7,
            )
            mock_agent.ask_page_vision_structured.assert_called_once()
            call_kwargs_explicit = mock_agent.ask_page_vision_structured.call_args.kwargs
            self.assertEqual(call_kwargs_explicit["reasoning_effort"], "high")
            self.assertEqual(call_kwargs_explicit["max_tokens"], 2048)
            self.assertEqual(call_kwargs_explicit["temperature"], 0.7)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_cli_max_tokens_default_and_explicit_override(self):
        """Verifica que la CLI asigne max_tokens=4096 por defecto para estadillo y preserve valores explícitos."""
        with patch("src.fieldnotes.cli.UnlimitedOCRAgent") as mock_agent_cls:
            mock_inst = MagicMock()
            mock_inst.process_estadillo.return_value = ("# Markdown", MagicMock(pages=[], warnings=[]))
            mock_agent_cls.return_value = mock_inst

            # 1. Sin --max-tokens: asigna 4096
            cli_main(["sample.pdf", "--profile", "estadillo", "--vision-model", "qwen/qwen3.5-9b"])
            self.assertEqual(mock_inst.process_estadillo.call_args.kwargs["max_tokens"], 4096)

            # 2. Con --max-tokens explícito (ej. 8192): preserva 8192
            cli_main(["sample.pdf", "--profile", "estadillo", "--vision-model", "qwen/qwen3.5-9b", "--max-tokens", "8192"])
            self.assertEqual(mock_inst.process_estadillo.call_args.kwargs["max_tokens"], 8192)

    def test_extra_body_safe_merge_and_no_mutation(self):
        """Verifica que el extra_body del usuario se fusione de forma segura con los defaults sin mutar el dict original."""
        temp_base = tempfile.mkdtemp(prefix="test_eb_merge_")
        temp_input = Path(temp_base) / "doc.pdf"
        temp_input.write_text("fake pdf content", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = temp_base
        mock_agent.output_dir = os.path.join(temp_base, "ocr_run")
        os.makedirs(os.path.join(mock_agent.output_dir, "raw"), exist_ok=True)

        img1 = Path(temp_base) / "p1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        mock_page = EstadilloPageDTO(
            page_number=1,
            header=EstadilloHeaderDTO(objetivo="Test"),
            rows=[],
        )
        mock_agent.ask_page_vision_structured.return_value = mock_page

        user_extra_body = {
            "custom_header": "test_val",
            "chat_template_kwargs": {"user_param": 123},
        }
        # Copia de control para verificar no mutación
        import copy
        user_eb_original = copy.deepcopy(user_extra_body)

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            profile.run(temp_input, extra_body=user_extra_body)

            # Verificar que user_extra_body original NO ha sido mutado
            self.assertEqual(user_extra_body, user_eb_original)

            # Verificar el payload efectivo enviado
            mock_agent.ask_page_vision_structured.assert_called_once()
            call_kwargs = mock_agent.ask_page_vision_structured.call_args.kwargs
            sent_eb = call_kwargs["extra_body"]
            self.assertEqual(sent_eb["custom_header"], "test_val")
            self.assertEqual(sent_eb["enable_thinking"], False)
            self.assertEqual(sent_eb["chat_template_kwargs"]["enable_thinking"], False)
            self.assertEqual(sent_eb["chat_template_kwargs"]["user_param"], 123)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_immediate_error_propagation_without_duplicate_retries(self):
        """Verifica que si ocurre LMStudioEmptyResponseError se propague inmediatamente al primer intento sin reintentos duplicados idénticos."""
        temp_base = tempfile.mkdtemp(prefix="test_no_duplicate_")
        temp_input = Path(temp_base) / "doc.pdf"
        temp_input.write_text("fake pdf content", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = temp_base
        mock_agent.output_dir = os.path.join(temp_base, "ocr_run")
        os.makedirs(os.path.join(mock_agent.output_dir, "raw"), exist_ok=True)

        img1 = Path(temp_base) / "p1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        mock_agent.ask_page_vision_structured.side_effect = LMStudioEmptyResponseError("Empty response")

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            with self.assertRaises(LMStudioEmptyResponseError) as ctx:
                profile.run(temp_input)

            self.assertIn("Empty response", str(ctx.exception))
            # Se ejecuta exactamente 1 llamada (sin reintento duplicado idéntico)
            self.assertEqual(mock_agent.ask_page_vision_structured.call_count, 1)
            # Limpieza completa de staging
            self.assertFalse((Path(temp_base) / "doc").exists())
            self.assertEqual(len(list(Path(temp_base).glob(".staging_*"))), 0)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_unrecoverable_errors_propagate_immediately_without_retry(self):
        """Verifica que errores de conexión, modelo o validación de esquema no se reintenten y se propaguen al primer fallo."""
        temp_base = tempfile.mkdtemp(prefix="test_unrecoverable_")
        temp_input = Path(temp_base) / "doc.pdf"
        temp_input.write_text("fake pdf content", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = temp_base
        mock_agent.output_dir = os.path.join(temp_base, "ocr_run")
        os.makedirs(os.path.join(mock_agent.output_dir, "raw"), exist_ok=True)

        img1 = Path(temp_base) / "p1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR")
        mock_agent.page_artifacts = [art1]

        unrecoverable_errors = [
            LMStudioConnectionError("Conexión perdida con el servidor"),
            LMStudioModelNotFoundError("Modelo vision_qwen no cargado"),
            StructuredOutputValidationError("Esquema inválido: campo requerido ausente"),
            StructuredOutputParseError("JSON sintácticamente corrupto"),
        ]

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            for err in unrecoverable_errors:
                mock_agent.ask_page_vision_structured.reset_mock()
                mock_agent.ask_page_vision_structured.side_effect = err

                with self.assertRaises(type(err)):
                    profile.run(temp_input)

                # Debe fallar inmediatamente sin reintentos
                self.assertEqual(mock_agent.ask_page_vision_structured.call_count, 1)
                self.assertEqual(len(list(Path(temp_base).glob(".staging_*"))), 0)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)

    def test_prompt_template_contractual_clauses(self):
        """Verifica que ESTADILLO_PROMPT_TEMPLATE incluya cláusulas explícitas para cabecera, filas y EstadilloPageDTO."""
        from src.fieldnotes.profiles.estadillo import ESTADILLO_PROMPT_TEMPLATE

        prompt = ESTADILLO_PROMPT_TEMPLATE.format(page_number=3)
        self.assertIn("page_number=3", prompt)
        self.assertIn("'header'", prompt)
        self.assertIn("'rows'", prompt)
        self.assertIn("EstadilloPageDTO", prompt)
        self.assertIn("'objetivo'", prompt)
        self.assertIn("'fecha'", prompt)
        self.assertIn("'asistentes'", prompt)
        self.assertIn("'especies_declaradas'", prompt)
        self.assertIn("TODAS las filas", prompt)

    def test_profile_empty_and_populated_pages_handling(self):
        """Verifica que el pipeline procese de forma fiel y determinista páginas sin filas junto a páginas con registros usando DTO."""
        temp_base = tempfile.mkdtemp(prefix="test_mixed_pages_")
        temp_input = Path(temp_base) / "doc.pdf"
        temp_input.write_text("fake pdf content", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.base_output_dir = temp_base
        mock_agent.output_dir = os.path.join(temp_base, "ocr_run")
        os.makedirs(os.path.join(mock_agent.output_dir, "raw"), exist_ok=True)

        img1 = Path(temp_base) / "p1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n")
        art1 = PageArtifact(page_number=1, image_path=img1, raw_ocr="OCR1")

        img2 = Path(temp_base) / "p2.png"
        img2.write_bytes(b"\x89PNG\r\n\x1a\n")
        art2 = PageArtifact(page_number=2, image_path=img2, raw_ocr="OCR2")

        mock_agent.page_artifacts = [art1, art2]

        row1_dto = EstadilloRowDTO(
            col=1,
            fil=26,
            especie="Ah",
            altura_cm=5.5,
            bbch="22",
        )
        mock_page1 = EstadilloPageDTO(
            page_number=1,
            header=EstadilloHeaderDTO(objetivo="Test"),
            rows=[row1_dto],
        )
        # Página 2 sin registros (legítimamente vacía)
        mock_page2 = EstadilloPageDTO(
            page_number=2,
            header=None,
            rows=[],
        )

        mock_agent.ask_page_vision_structured.side_effect = [mock_page1, mock_page2]

        try:
            profile = EstadilloProfile(agent=mock_agent, output_base_dir=temp_base)
            md_res, doc_res = profile.run(temp_input)

            self.assertEqual(len(doc_res.pages), 2)
            self.assertEqual(doc_res.total_records, 1)
            self.assertEqual(doc_res.pages[0].total_records, 1)
            self.assertEqual(doc_res.pages[1].total_records, 0)
            self.assertIn("| Test |", md_res)
            self.assertIn("||1|26|H|5.5||22||", md_res)
        finally:
            shutil.rmtree(temp_base, ignore_errors=True)


class TestRealEstadilloProfileIntegration(unittest.TestCase):
    def test_real_estadillo_e2e_on_pdf(self):
        """Prueba opt-in de integración real E2E procesando 26-05-06.pdf completo."""
        flag = os.environ.get("RUN_ESTADILLO_INTEGRATION")
        if flag != "1":
            self.skipTest(
                "Prueba E2E real del perfil estadillo desactivada por defecto; "
                "requiere RUN_ESTADILLO_INTEGRATION=1"
            )

        model_name = os.environ.get("LM_STUDIO_VISION_MODEL", "qwen/qwen3.5-9b")
        pdf_path = Path("26-05-06.pdf")
        if not pdf_path.is_file():
            self.fail(f"Archivo de muestra '{pdf_path}' no encontrado para la prueba de integración.")

        temp_output = tempfile.mkdtemp(prefix="test_e2e_estadillo_")
        try:
            agent = UnlimitedOCRAgent(
                vision_model=model_name,
                output_dir=temp_output,
                ocr_mode="worker",
            )

            md_res, doc_res = agent.process_estadillo(
                file_path=pdf_path,
                output_dir=temp_output,
                max_tokens=4096,
            )

            self.assertIsInstance(doc_res, EstadilloDocument)
            self.assertEqual(len(doc_res.pages), 6, "El PDF 26-05-06.pdf debe generar 6 páginas")

            # Layout canónico
            safe_dir = Path(temp_output) / "26-05-06"
            self.assertTrue(safe_dir.is_dir())
            self.assertTrue((safe_dir / "notebook.md").is_file())
            self.assertTrue((safe_dir / "document.json").is_file())
            self.assertTrue((safe_dir / "pages").is_dir())
            self.assertTrue((safe_dir / "raw").is_dir())

            # Verificar 6 páginas rasterizadas
            page_images = list((safe_dir / "pages").glob("*.png"))
            self.assertEqual(len(page_images), 6)

            # Roundtrip de document.json
            loaded = EstadilloDocument.model_validate_json((safe_dir / "document.json").read_text(encoding="utf-8"))
            self.assertEqual(len(loaded.pages), 6)

            # Comprobar inicio con dos tablas de AGENTS.md
            lines = md_res.splitlines()
            self.assertEqual(lines[0], "| Objetivo | Fecha | Asistentes |")
            self.assertEqual(lines[1], "|---|---|---|")
            self.assertIn("Especies: P;H;R;M", lines[3])
            self.assertIn("|id|col|fil|especie|altura_cm|foto|bbch|observaciones|", lines[5])

            # Verificación endurecida de registros no vacíos
            total_records = doc_res.total_records
            self.assertGreater(
                total_records, 0,
                f"Falso positivo detectado: 26-05-06.pdf tiene registros pero se extrajeron {total_records}"
            )
            self.assertGreaterEqual(
                total_records, 50,
                f"Expectativa mínima no alcanzada: se esperaban >=50 registros a lo largo de las 6 páginas, obtenidos {total_records}"
            )

            # Verificar que el Markdown contiene filas de datos reales
            data_table_lines = [l for l in lines[6:] if l.strip().startswith("|") and not l.startswith("|---|")]
            self.assertGreaterEqual(
                len(data_table_lines), 50,
                f"La tabla Markdown debe contener filas de datos reales, encontradas {len(data_table_lines)}"
            )

            print(f"\nESTADILLO_E2E_SUCCESS pages=6 records={total_records} warnings={len(doc_res.warnings)}")
        finally:
            shutil.rmtree(temp_output, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
