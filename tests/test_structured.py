import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.fieldnotes.schemas.evidence import EvidenceValue
from src.fieldnotes.schemas.estadillo import EstadilloHeader, EstadilloRow, EstadilloPage
from src.fieldnotes.schemas.document import DocumentIR, PageIR
from src.fieldnotes.vlm.errors import (
    StructuredOutputError,
    StructuredOutputParseError,
    StructuredOutputValidationError,
)
from src.fieldnotes.vlm.structured import generate_json_schema, parse_structured_json
from src.fieldnotes.vlm.lmstudio import LMStudioClient
from src.fieldnotes.artifacts import PageArtifact


class TestStructuredSchemaGeneration(unittest.TestCase):
    def test_generate_json_schema_structure(self):
        payload = generate_json_schema(EstadilloPage)
        self.assertEqual(payload["type"], "json_schema")
        json_schema = payload["json_schema"]
        self.assertEqual(json_schema["name"], "EstadilloPage")
        self.assertTrue(json_schema["strict"])
        schema_dict = json_schema["schema"]
        self.assertEqual(schema_dict.get("additionalProperties"), False)

    def test_generate_json_schema_additional_properties_in_defs(self):
        payload = generate_json_schema(EstadilloPage)
        defs = payload["json_schema"]["schema"].get("$defs", {})
        for name, sub_schema in defs.items():
            if sub_schema.get("type") == "object":
                self.assertEqual(
                    sub_schema.get("additionalProperties"),
                    False,
                    f"Def '{name}' no tiene additionalProperties: False",
                )

    def test_generate_json_schema_does_not_mutate_class(self):
        original_schema = EstadilloPage.model_json_schema()
        _ = generate_json_schema(EstadilloPage)
        after_schema = EstadilloPage.model_json_schema()
        self.assertEqual(original_schema, after_schema)

    def test_estadillo_page_schema_enforces_required_nonnull_source_page_in_header(self):
        """Verifica que el JSON Schema de EstadilloPage fuerce inequívocamente source_page como integer >= 1 no nulo."""
        payload = generate_json_schema(EstadilloPage)
        defs = payload["json_schema"]["schema"].get("$defs", {})
        self.assertIn("EstadilloPageHeader", defs)
        header_def = defs["EstadilloPageHeader"]

        self.assertIn("source_page", header_def.get("required", []))
        source_page_prop = header_def.get("properties", {}).get("source_page", {})
        self.assertEqual(source_page_prop.get("type"), "integer")
        self.assertEqual(source_page_prop.get("minimum"), 1)
        self.assertNotIn("anyOf", source_page_prop)
        self.assertNotIn("null", str(source_page_prop))


class TestStructuredJSONParser(unittest.TestCase):
    def test_parse_plain_json(self):
        json_text = '{"page_number": 1, "rows": []}'
        res = parse_structured_json(json_text, EstadilloPage)
        self.assertIsInstance(res, EstadilloPage)
        self.assertEqual(res.page_number, 1)

    def test_parse_single_fenced_json_block(self):
        fenced_text = '```json\n{\n  "page_number": 1,\n  "rows": []\n}\n```'
        res = parse_structured_json(fenced_text, EstadilloPage)
        self.assertIsInstance(res, EstadilloPage)
        self.assertEqual(res.page_number, 1)

    def test_parse_single_fenced_block_without_json_tag(self):
        fenced_text = '```\n{\n  "page_number": 1,\n  "rows": []\n}\n```'
        res = parse_structured_json(fenced_text, EstadilloPage)
        self.assertIsInstance(res, EstadilloPage)
        self.assertEqual(res.page_number, 1)

    def test_reject_top_level_list(self):
        list_json = '[{"page_number": 1}]'
        with self.assertRaises(StructuredOutputParseError) as ctx:
            parse_structured_json(list_json, EstadilloPage)
        self.assertIn("objeto json (dict)", str(ctx.exception).lower())

    def test_reject_top_level_scalar(self):
        scalar_json = '"simple string"'
        with self.assertRaises(StructuredOutputParseError) as ctx:
            parse_structured_json(scalar_json, EstadilloPage)
        self.assertIn("objeto json (dict)", str(ctx.exception).lower())

    def test_reject_prose_before_or_after_json(self):
        prose_text = 'Aquí tienes el resultado solicitado:\n```json\n{"page_number": 1}\n```\nEspero te sirva.'
        with self.assertRaises(StructuredOutputParseError) as ctx:
            parse_structured_json(prose_text, EstadilloPage)
        self.assertIn("texto libre", str(ctx.exception).lower())
        self.assertIsNotNone(ctx.exception.raw_response)

    def test_reject_multiple_code_blocks(self):
        multi_blocks = '```json\n{"page_number": 1}\n```\n```json\n{"page_number": 2}\n```'
        with self.assertRaises(StructuredOutputParseError) as ctx:
            parse_structured_json(multi_blocks, EstadilloPage)
        self.assertIn("múltiples bloques", str(ctx.exception).lower())

    def test_reject_syntax_error_json(self):
        bad_json = '{"page_number": 1, "rows": [}'
        with self.assertRaises(StructuredOutputParseError) as ctx:
            parse_structured_json(bad_json, EstadilloPage)
        self.assertIn("sintaxis json", str(ctx.exception).lower())
        self.assertIsNotNone(ctx.exception.__cause__)

    def test_reject_schema_validation_error(self):
        bad_schema_json = '{"page_number": -5, "rows": []}'
        with self.assertRaises(StructuredOutputValidationError) as ctx:
            parse_structured_json(bad_schema_json, EstadilloPage)
        self.assertIn("EstadilloPage", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.__cause__)

    def test_document_ir_roundtrip_with_path_in_parse_structured_json(self):
        doc = DocumentIR(
            source_file="doc.pdf",
            pages=[PageIR(page_number=1, image_path=Path("pages/page_001.png"))],
        )
        json_str = doc.model_dump_json()
        restored = parse_structured_json(json_str, DocumentIR)
        self.assertIsInstance(restored, DocumentIR)
        self.assertEqual(len(restored.pages), 1)
        self.assertIsInstance(restored.pages[0].image_path, Path)
        self.assertEqual(restored.pages[0].image_path, Path("pages/page_001.png"))

    def test_fenced_json_with_path_in_parse_structured_json(self):
        fenced = '```json\n{"source_file": "doc.pdf", "pages": [{"page_number": 1, "image_path": "pages/page_001.png"}]}\n```'
        restored = parse_structured_json(fenced, DocumentIR)
        self.assertIsInstance(restored, DocumentIR)
        self.assertIsInstance(restored.pages[0].image_path, Path)
        self.assertEqual(restored.pages[0].image_path, Path("pages/page_001.png"))

    def test_strict_json_rejection_of_string_numeric_and_bool_values(self):
        # source_page como string en JSON debe fallar
        bad_source_page = '{"raw": "test", "source_page": "1", "uncertain": false}'
        with self.assertRaises(StructuredOutputValidationError):
            parse_structured_json(bad_source_page, EvidenceValue[str])

        # normalized float como string en JSON debe fallar
        bad_float = '{"raw": "10.5", "normalized": "10.5", "source_page": 1, "uncertain": false}'
        with self.assertRaises(StructuredOutputValidationError):
            parse_structured_json(bad_float, EvidenceValue[float])

        # uncertain como string en JSON debe fallar
        bad_bool = '{"raw": "test", "source_page": 1, "uncertain": "false"}'
        with self.assertRaises(StructuredOutputValidationError):
            parse_structured_json(bad_bool, EvidenceValue[str])

    def test_reject_header_with_null_source_page_observed_response(self):
        """Verifica el rechazo estricto de la respuesta anómala observada en E2E donde header.source_page era null."""
        observed_bad_json = (
            '{\n'
            '  "page_number": 1,\n'
            '  "header": {\n'
            '    "objetivo": {"raw": "Muestreo parcela", "source_page": 1, "uncertain": false},\n'
            '    "fecha": {"raw": "2026-05-06", "source_page": 1, "uncertain": false},\n'
            '    "asistentes": null,\n'
            '    "equipamiento": null,\n'
            '    "situacion_atmosferica": null,\n'
            '    "especies_declaradas": null,\n'
            '    "source_page": null\n'
            '  },\n'
            '  "rows": [],\n'
            '  "additional_text": null,\n'
            '  "warnings": []\n'
            '}'
        )
        with self.assertRaises(StructuredOutputValidationError) as ctx:
            parse_structured_json(observed_bad_json, EstadilloPage)
        self.assertIn("EstadilloPage", str(ctx.exception))

    def test_reject_header_missing_source_page(self):
        """Verifica que omitir source_page en cabecera de página sea rechazado estrictamente."""
        bad_json = (
            '{\n'
            '  "page_number": 1,\n'
            '  "header": {\n'
            '    "objetivo": {"raw": "Muestreo", "source_page": 1, "uncertain": false}\n'
            '  },\n'
            '  "rows": []\n'
            '}'
        )
        with self.assertRaises(StructuredOutputValidationError):
            parse_structured_json(bad_json, EstadilloPage)

    def test_parse_header_with_valid_source_page_matching_page_number(self):
        """Verifica el parseo exitoso de una cabecera de página con source_page entero válido."""
        valid_json = (
            '{\n'
            '  "page_number": 1,\n'
            '  "header": {\n'
            '    "objetivo": {"raw": "Muestreo parcela", "source_page": 1, "uncertain": false},\n'
            '    "fecha": {"raw": "2026-05-06", "source_page": 1, "uncertain": false},\n'
            '    "source_page": 1\n'
            '  },\n'
            '  "rows": []\n'
            '}'
        )
        page = parse_structured_json(valid_json, EstadilloPage)
        self.assertIsInstance(page, EstadilloPage)
        self.assertEqual(page.page_number, 1)
        self.assertIsNotNone(page.header)
        self.assertEqual(page.header.source_page, 1)
        self.assertEqual(page.header.objetivo.raw, "Muestreo parcela")


class TestStructuredExceptionsRedaction(unittest.TestCase):
    def test_validation_error_redacts_api_keys_and_base64_in_all_attributes(self):
        secret_key = "sk-1234567890abcdef12345678"
        bearer_token = "Bearer secret_bearer_token_xyz"
        base64_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA" + ("A" * 200)

        # JSON con tipo erróneo para provocar ValidationError con input_value conteniendo secretos
        injected = (
            f'{{"page_number": "invalid_page_number", '
            f'"secret_field": "{secret_key}", '
            f'"auth": "{bearer_token}", '
            f'"img": "{base64_uri}"}}'
        )

        try:
            parse_structured_json(injected, EstadilloPage)
            self.fail("Debería haber lanzado StructuredOutputValidationError")
        except StructuredOutputValidationError as exc:
            # Comprobar str(exc)
            err_str = str(exc)
            self.assertNotIn(secret_key, err_str)
            self.assertNotIn("secret_bearer_token_xyz", err_str)
            self.assertNotIn("iVBORw0KGgoAAAANSUhEUgAA", err_str)

            # Comprobar repr(exc)
            err_repr = repr(exc)
            self.assertNotIn(secret_key, err_repr)
            self.assertNotIn("secret_bearer_token_xyz", err_repr)
            self.assertNotIn("iVBORw0KGgoAAAANSUhEUgAA", err_repr)

            # Comprobar exc.args
            for arg in exc.args:
                arg_str = str(arg)
                self.assertNotIn(secret_key, arg_str)
                self.assertNotIn("secret_bearer_token_xyz", arg_str)
                self.assertNotIn("iVBORw0KGgoAAAANSUhEUgAA", arg_str)

            # Comprobar exc.raw_response
            self.assertIsNotNone(exc.raw_response)
            self.assertNotIn(secret_key, exc.raw_response)
            self.assertNotIn("secret_bearer_token_xyz", exc.raw_response)
            self.assertNotIn("iVBORw0KGgoAAAANSUhEUgAA", exc.raw_response)
            self.assertLessEqual(len(exc.raw_response), 550)

            # Comprobar preservación de causa
            self.assertIsNotNone(exc.__cause__)


class TestLMStudioClientStructuredExecution(unittest.TestCase):
    @patch("openai.OpenAI")
    def test_ask_text_structured(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.return_value.data = [MagicMock(id="qwen-text")]

        mock_choice = MagicMock()
        mock_choice.message.content = '{"page_number": 1, "rows": []}'
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            text_model="qwen-text",
        )

        res = client.ask_text_structured("Extrae página", EstadilloPage)
        self.assertIsInstance(res, EstadilloPage)
        self.assertEqual(res.page_number, 1)

        kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")
        self.assertEqual(kwargs["response_format"]["json_schema"]["name"], "EstadilloPage")

    @patch("openai.OpenAI")
    def test_ask_vision_structured(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.return_value.data = [MagicMock(id="qwen/qwen3.5-9b")]

        mock_choice = MagicMock()
        mock_choice.message.content = '```json\n{"page_number": 1, "rows": []}\n```'
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            vision_model="qwen/qwen3.5-9b",
        )

        import tempfile
        tmp_fd, temp_png = tempfile.mkstemp(suffix=".png")
        os.write(tmp_fd, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest")
        os.close(tmp_fd)

        try:
            res = client.ask_vision_structured(temp_png, "Analiza imagen", EstadilloPage)
            self.assertIsInstance(res, EstadilloPage)
            self.assertEqual(res.page_number, 1)

            kwargs = mock_client.chat.completions.create.call_args.kwargs
            self.assertEqual(kwargs["response_format"]["type"], "json_schema")
            messages = kwargs["messages"]
            user_msg = next(m for m in messages if m["role"] == "user")
            self.assertTrue(any(block["type"] == "image_url" for block in user_msg["content"]))
        finally:
            if os.path.exists(temp_png):
                os.unlink(temp_png)


class TestPipelineStructuredMethods(unittest.TestCase):
    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_pipeline_ask_vision_structured(self, mock_lmstudio_cls):
        mock_vlm = MagicMock()
        mock_page = EstadilloPage(page_number=1)
        mock_vlm.ask_vision_structured.return_value = mock_page
        mock_lmstudio_cls.return_value = mock_vlm

        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        import tempfile
        temp_dir = tempfile.mkdtemp()
        try:
            agent = UnlimitedOCRAgent(output_dir=temp_dir, vision_model="qwen/qwen3.5-9b")
            res = agent.ask_vision_structured("img.png", "analiza", EstadilloPage)
            self.assertEqual(res, mock_page)
            mock_vlm.ask_vision_structured.assert_called_once_with(
                image_path="img.png",
                prompt="analiza",
                schema=EstadilloPage,
                ocr_context=None,
            )
            agent.cleanup()
        finally:
            if os.path.exists(temp_dir):
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)

    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_pipeline_ask_page_vision_structured(self, mock_lmstudio_cls):
        mock_vlm = MagicMock()
        mock_page = EstadilloPage(page_number=2)
        mock_vlm.ask_vision_structured.return_value = mock_page
        mock_lmstudio_cls.return_value = mock_vlm

        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        import tempfile
        temp_dir = tempfile.mkdtemp()
        try:
            agent = UnlimitedOCRAgent(output_dir=temp_dir, vision_model="qwen/qwen3.5-9b")
            artifact = PageArtifact(
                page_number=2,
                image_path=Path("page_002.png"),
                raw_ocr="texto pagina 2",
            )
            res = agent.ask_page_vision_structured(artifact, "analiza tabla", EstadilloPage)
            self.assertEqual(res, mock_page)
            mock_vlm.ask_vision_structured.assert_called_once_with(
                image_path=Path("page_002.png"),
                prompt="analiza tabla",
                schema=EstadilloPage,
                ocr_context="texto pagina 2",
            )
            agent.cleanup()
        finally:
            if os.path.exists(temp_dir):
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)


class TestRealLMStudioStructuredIntegration(unittest.TestCase):
    def test_real_lmstudio_structured_query(self):
        """Prueba opt-in de integración real contra LM Studio con structured outputs."""
        flag = os.environ.get("RUN_LMSTUDIO_STRUCTURED_INTEGRATION")
        if flag != "1":
            self.skipTest(
                "Prueba de integración estructurada con LM Studio desactivada por defecto; "
                "requiere RUN_LMSTUDIO_STRUCTURED_INTEGRATION=1"
            )

        model_name = os.environ.get("LM_STUDIO_VISION_MODEL", "qwen/qwen3.5-9b")
        pdf_sample = Path("26-05-06.pdf")
        if not pdf_sample.is_file():
            self.fail(f"Archivo de muestra '{pdf_sample}' no encontrado para la prueba de integración.")

        import pymupdf
        doc = pymupdf.open(str(pdf_sample))
        if len(doc) == 0:
            self.fail("El documento PDF de prueba no contiene páginas.")

        page = doc[0]
        rect = pymupdf.Rect(50, 50, 400, 250)
        pix = page.get_pixmap(clip=rect, dpi=120)
        import tempfile
        tmp_fd, page_png = tempfile.mkstemp(suffix=".png")
        os.close(tmp_fd)
        pix.save(page_png)
        doc.close()

        try:
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                vision_model=model_name,
                validate_model=True,
            )

            prompt = (
                "Analiza la tabla de notas de campo visible en este recorte de página. "
                "Extrae como máximo 1 fila respetando la estructura EstadilloPage con page_number=1."
            )

            result = client.ask_vision_structured(
                image_path=page_png,
                prompt=prompt,
                schema=EstadilloPage,
                max_tokens=4096,
            )

            self.assertIsInstance(result, EstadilloPage)
            self.assertGreaterEqual(result.page_number, 1)

            # Si se devolvieron filas, verificar invariantes de evidencia
            for row in result.rows:
                self.assertEqual(row.source_page, result.page_number)
                for field_name in ("id", "col", "fil", "especie", "altura_cm", "foto", "bbch", "observaciones"):
                    ev = getattr(row, field_name)
                    if ev is not None:
                        self.assertEqual(ev.source_page, result.page_number)

            print(f"\nLM_STUDIO_STRUCTURED_INTEGRATION_SUCCESS model={model_name} page_number={result.page_number} rows={len(result.rows)}")
        except Exception as exc:
            self.fail(f"Fallo en la prueba de integración estructurada real con LM Studio: {exc}")
        finally:
            if os.path.exists(page_png):
                os.unlink(page_png)


if __name__ == "__main__":
    unittest.main()
