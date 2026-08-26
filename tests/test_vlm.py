import unittest
from unittest.mock import MagicMock, patch
import tempfile
import os
import sys
import shutil
from pathlib import Path
from src.fieldnotes.vlm.lmstudio import (
    LMStudioClient,
    encode_image_to_data_uri,
    sanitize_message,
    LMStudioError,
    LMStudioConnectionError,
    LMStudioModelNotConfiguredError,
    LMStudioModelNotFoundError,
    LMStudioResponseError,
    LMStudioEmptyResponseError,
    LMStudioResponseTruncatedError,
)
from openai import APIConnectionError, APIStatusError, NotFoundError


class TestImageValidationAndEncoding(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_img_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_encode_png_valid(self):
        img_path = Path(self.test_dir) / "sample.png"
        with open(img_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtestdata")

        data_uri = encode_image_to_data_uri(img_path)
        self.assertTrue(data_uri.startswith("data:image/png;base64,"))

    def test_encode_jpg_valid(self):
        img_path = Path(self.test_dir) / "sample.jpg"
        with open(img_path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0\x00\x10JFIFtestdata")

        data_uri = encode_image_to_data_uri(img_path)
        self.assertTrue(data_uri.startswith("data:image/jpeg;base64,"))

    def test_encode_webp_valid(self):
        img_path = Path(self.test_dir) / "sample.webp"
        with open(img_path, "wb") as f:
            f.write(b"RIFF\x00\x00\x00\x00WEBPVP8 testdata")

        data_uri = encode_image_to_data_uri(img_path)
        self.assertTrue(data_uri.startswith("data:image/webp;base64,"))

    def test_extension_to_magic_mismatch_raises_value_error(self):
        mismatched = Path(self.test_dir) / "mismatched.png"
        with open(mismatched, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0\x00\x10JFIFtestdata")

        with self.assertRaises(ValueError) as ctx:
            encode_image_to_data_uri(mismatched)
        self.assertIn("Firma mágica inválida para archivo con extensión .png", str(ctx.exception))

    def test_bmp_and_unsupported_extensions_rejected(self):
        bmp_path = Path(self.test_dir) / "sample.bmp"
        with open(bmp_path, "wb") as f:
            f.write(b"BM\x00\x00\x00\x00testdata")

        with self.assertRaises(ValueError) as ctx:
            encode_image_to_data_uri(bmp_path)
        self.assertIn("no soportada", str(ctx.exception))

    def test_encode_missing_file_raises_file_not_found(self):
        missing_path = Path(self.test_dir) / "non_existent.png"
        with self.assertRaises(FileNotFoundError) as ctx:
            encode_image_to_data_uri(missing_path)
        self.assertIn("no existe", str(ctx.exception))

    def test_invalid_max_bytes_arguments_raise_value_error(self):
        valid_png = Path(self.test_dir) / "sample.png"
        with open(valid_png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtestdata")

        for invalid_val in (0, -1, False, True, "20000", 10.5):
            with self.assertRaises(ValueError):
                encode_image_to_data_uri(valid_png, max_bytes=invalid_val)

    def test_encode_size_limit_exceeded_raises_value_error(self):
        large_png = Path(self.test_dir) / "large.png"
        with open(large_png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + (b"A" * 90))

        with self.assertRaises(ValueError) as ctx:
            encode_image_to_data_uri(large_png, max_bytes=50)
        self.assertIn("supera el tamaño máximo", str(ctx.exception))
        self.assertNotIn("QUFB", str(ctx.exception))


class TestSanitizationAndRedaction(unittest.TestCase):
    def test_sanitize_removes_api_keys_and_bearer_tokens(self):
        msg = "Error with api_key='sk-1234567890abcdef' and header Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        sanitized = sanitize_message(msg)
        self.assertNotIn("sk-1234567890abcdef", sanitized)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", sanitized)
        self.assertIn("[REDACTED_API_KEY]", sanitized)
        self.assertIn("[REDACTED_TOKEN]", sanitized)

    def test_sanitize_removes_base64_data_uri(self):
        msg = "Failed processing data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg== in payload"
        sanitized = sanitize_message(msg)
        self.assertNotIn("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", sanitized)
        self.assertIn("data:image/[REDACTED_BASE64]", sanitized)


class TestLMStudioModelResolution(unittest.TestCase):
    @patch("openai.OpenAI")
    def test_model_resolution_precedence_vision(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.models.list.return_value.data = [
            MagicMock(id="model_call"),
            MagicMock(id="model_ctor_v"),
            MagicMock(id="model_env_v"),
            MagicMock(id="model_legacy_flag"),
            MagicMock(id="model_legacy_env"),
        ]

        # 1. Per-call overrides constructor, env and legacy
        with patch.dict(os.environ, {"LM_STUDIO_VISION_MODEL": "model_env_v", "LM_STUDIO_MODEL": "model_legacy_env"}):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                vision_model="model_ctor_v",
                default_model="model_legacy_flag",
            )
            self.assertEqual(client.resolve_model(model_type="vision", requested_model="model_call"), "model_call")

        # 2. Constructor vision_model overrides env and legacy
        with patch.dict(os.environ, {"LM_STUDIO_VISION_MODEL": "model_env_v", "LM_STUDIO_MODEL": "model_legacy_env"}):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                vision_model="model_ctor_v",
                default_model="model_legacy_flag",
            )
            self.assertEqual(client.resolve_model(model_type="vision"), "model_ctor_v")

        # 3. Environment LM_STUDIO_VISION_MODEL used when constructor is None
        with patch.dict(os.environ, {"LM_STUDIO_VISION_MODEL": "model_env_v", "LM_STUDIO_MODEL": "model_legacy_env"}):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                vision_model=None,
                default_model="model_legacy_flag",
            )
            self.assertEqual(client.resolve_model(model_type="vision"), "model_env_v")

        # 4. Constructor default_model overrides legacy env LM_STUDIO_MODEL
        with patch.dict(os.environ, {"LM_STUDIO_MODEL": "model_legacy_env"}, clear=True):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                vision_model=None,
                default_model="model_legacy_flag",
            )
            self.assertEqual(client.resolve_model(model_type="vision"), "model_legacy_flag")

        # 5. Legacy env LM_STUDIO_MODEL used when vision env and default_model are absent
        with patch.dict(os.environ, {"LM_STUDIO_MODEL": "model_legacy_env"}, clear=True):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                vision_model=None,
                default_model=None,
            )
            self.assertEqual(client.resolve_model(model_type="vision"), "model_legacy_env")

    @patch("openai.OpenAI")
    def test_model_resolution_precedence_text(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.models.list.return_value.data = [
            MagicMock(id="text_call"),
            MagicMock(id="text_ctor"),
            MagicMock(id="text_env"),
            MagicMock(id="text_legacy_flag"),
            MagicMock(id="text_legacy_env"),
        ]

        # 1. Constructor text_model overrides env
        with patch.dict(os.environ, {"LM_STUDIO_TEXT_MODEL": "text_env", "LM_STUDIO_MODEL": "text_legacy_env"}):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                text_model="text_ctor",
            )
            self.assertEqual(client.resolve_model(model_type="text"), "text_ctor")

        # 2. Environment LM_STUDIO_TEXT_MODEL overrides legacy flag and legacy env
        with patch.dict(os.environ, {"LM_STUDIO_TEXT_MODEL": "text_env", "LM_STUDIO_MODEL": "text_legacy_env"}):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                text_model=None,
                default_model="text_legacy_flag",
            )
            self.assertEqual(client.resolve_model(model_type="text"), "text_env")

        # 3. Legacy flag default_model overrides legacy env LM_STUDIO_MODEL
        with patch.dict(os.environ, {"LM_STUDIO_MODEL": "text_legacy_env"}, clear=True):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                text_model=None,
                default_model="text_legacy_flag",
            )
            self.assertEqual(client.resolve_model(model_type="text"), "text_legacy_flag")

        # 4. Legacy env LM_STUDIO_MODEL used when text env and default_model are absent
        with patch.dict(os.environ, {"LM_STUDIO_MODEL": "text_legacy_env"}, clear=True):
            client = LMStudioClient(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                text_model=None,
                default_model=None,
            )
            self.assertEqual(client.resolve_model(model_type="text"), "text_legacy_env")

    @patch("openai.OpenAI")
    def test_model_cache_invalidation_on_setter(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.models.list.return_value.data = [
            MagicMock(id="model_1"),
            MagicMock(id="model_2"),
            MagicMock(id="model_3"),
        ]

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            vision_model="model_1",
            validate_model=True,
        )

        self.assertEqual(client.resolve_model(model_type="vision"), "model_1")
        self.assertEqual(mock_client.models.list.call_count, 1)

        # Repetir usa caché
        self.assertEqual(client.resolve_model(model_type="vision"), "model_1")
        self.assertEqual(mock_client.models.list.call_count, 1)

        # Modificar setter vision_model invalida caché
        client.vision_model = "model_2"
        self.assertEqual(client.resolve_model(model_type="vision"), "model_2")
        self.assertEqual(mock_client.models.list.call_count, 2)

        # Modificar setter text_model invalida caché
        client.text_model = "model_3"
        self.assertEqual(client.resolve_model(model_type="text"), "model_3")
        self.assertEqual(mock_client.models.list.call_count, 3)

        # Modificar setter legacy model invalida caché
        client.model = "model_1"
        self.assertEqual(client.resolve_model(model_type="text"), "model_1")
        self.assertEqual(mock_client.models.list.call_count, 4)

    @patch("openai.OpenAI")
    def test_fetch_models_connection_failure_propagates_chained_error(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.models.list.side_effect = ConnectionError("LM Studio server down")

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            vision_model="qwen/qwen3.5-9b",
            validate_model=True,
        )

        with self.assertRaises(LMStudioConnectionError) as ctx:
            client.resolve_model(model_type="vision")
        self.assertIsNotNone(ctx.exception.__cause__)
        self.assertIn("Fallo al conectar con LM Studio", str(ctx.exception))

    @patch("openai.OpenAI")
    def test_fetch_models_empty_advertised_raises_not_found(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.models.list.return_value.data = []

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            vision_model="qwen/qwen3.5-9b",
            validate_model=True,
        )

        with self.assertRaises(LMStudioModelNotFoundError) as ctx:
            client.resolve_model(model_type="vision")
        self.assertIn("no tiene ningún modelo cargado", str(ctx.exception))


class TestLMStudioClientExecution(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_vlm_exec_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("openai.OpenAI")
    def test_ask_text_payload_and_kwargs_omission(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Resultado texto digitalizado"))]
        mock_client.chat.completions.create.return_value = mock_response

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            text_model="text_model_1",
            validate_model=False,
        )

        res = client.ask_text(
            prompt="Extrae la tabla",
            context="<PAGE>Texto OCR",
            reasoning_effort="none",
        )

        self.assertEqual(res, "Resultado texto digitalizado")
        mock_client.chat.completions.create.assert_called_once()
        kwargs = mock_client.chat.completions.create.call_args.kwargs

        self.assertEqual(kwargs["model"], "text_model_1")
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertNotIn("response_format", kwargs)

    @patch("openai.OpenAI")
    def test_ask_vision_multipart_payload(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Resultado vision OCR"))]
        mock_client.chat.completions.create.return_value = mock_response

        sample_img = Path(self.test_dir) / "sample.png"
        with open(sample_img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtestdata")

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            vision_model="vision_qwen_9b",
            validate_model=False,
        )

        res = client.ask_vision(
            image_path=sample_img,
            prompt="Analiza la imagen",
            ocr_context="Columna A | Columna B",
        )

        self.assertEqual(res, "Resultado vision OCR")
        mock_client.chat.completions.create.assert_called_once()
        kwargs = mock_client.chat.completions.create.call_args.kwargs

        self.assertEqual(kwargs["model"], "vision_qwen_9b")
        user_content = kwargs["messages"][1]["content"]
        self.assertIsInstance(user_content, list)
        self.assertEqual(len(user_content), 2)
        self.assertEqual(user_content[0]["type"], "text")
        self.assertIn("Columna A | Columna B", user_content[0]["text"])
        self.assertEqual(user_content[1]["type"], "image_url")
        self.assertTrue(user_content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    @patch("openai.OpenAI")
    def test_response_format_passed_unmutated(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"data": "ok"}'))]
        mock_client.chat.completions.create.return_value = mock_response

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            text_model="text_model_1",
            validate_model=False,
        )

        fmt = {"type": "json_object"}
        client.ask_text(prompt="JSON please", response_format=fmt)

        kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(fmt, {"type": "json_object"})

    @patch("openai.OpenAI")
    def test_extra_body_forwarding_in_all_methods(self, mock_openai):
        from pydantic import BaseModel

        class SimpleModel(BaseModel):
            title: str

        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"title": "test"}'))]
        mock_client.chat.completions.create.return_value = mock_response

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            vision_model="vision_qwen_9b",
            text_model="text_model_1",
            validate_model=False,
        )

        sample_img = Path(self.test_dir) / "sample_eb.png"
        sample_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtestdata")

        eb = {"chat_template_kwargs": {"enable_thinking": False}, "enable_thinking": False}

        # 1. ask_text
        client.ask_text(prompt="test", extra_body=eb)
        self.assertEqual(mock_client.chat.completions.create.call_args.kwargs["extra_body"], eb)

        # 2. ask_vision
        client.ask_vision(image_path=sample_img, prompt="test vision", extra_body=eb)
        self.assertEqual(mock_client.chat.completions.create.call_args.kwargs["extra_body"], eb)

        # 3. ask_text_structured
        res_t = client.ask_text_structured(prompt="test structured", schema=SimpleModel, extra_body=eb)
        self.assertEqual(res_t.title, "test")
        self.assertEqual(mock_client.chat.completions.create.call_args.kwargs["extra_body"], eb)

        # 4. ask_vision_structured
        res_v = client.ask_vision_structured(image_path=sample_img, prompt="test v structured", schema=SimpleModel, extra_body=eb)
        self.assertEqual(res_v.title, "test")
        self.assertEqual(mock_client.chat.completions.create.call_args.kwargs["extra_body"], eb)

    @patch("openai.OpenAI")
    def test_empty_content_raises_empty_response_error(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="   "))]
        mock_client.chat.completions.create.return_value = mock_response

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            text_model="text_model_1",
            validate_model=False,
        )

        with self.assertRaises(LMStudioEmptyResponseError):
            client.ask_text(prompt="test empty")

    @patch("openai.OpenAI")
    def test_truncated_response_raises_response_truncated_error(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_choice = MagicMock()
        mock_choice.message = MagicMock(content='{"partial": "json')
        mock_choice.finish_reason = "length"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            text_model="text_model_1",
            validate_model=False,
        )

        with self.assertRaises(LMStudioResponseTruncatedError) as ctx:
            client.ask_text(prompt="test truncated", max_tokens=8192)

        self.assertIn("finish_reason='length'", str(ctx.exception))
        self.assertIn("8192", str(ctx.exception))

    @patch("openai.OpenAI")
    def test_sdk_connection_error_chained_and_redacted(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        sentinel_secret = "sk-supersecretkey12345"
        mock_client.chat.completions.create.side_effect = APIConnectionError(
            request=MagicMock(headers={"Authorization": f"Bearer {sentinel_secret}"}),
            message=f"Connection failed with auth token {sentinel_secret}",
        )

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key=sentinel_secret,
            text_model="text_model_1",
            validate_model=False,
        )

        with self.assertRaises(LMStudioConnectionError) as ctx:
            client.ask_text(prompt="test connection")

        self.assertIsNotNone(ctx.exception.__cause__)
        self.assertNotIn(sentinel_secret, str(ctx.exception))
        self.assertNotIn(sentinel_secret, repr(ctx.exception))

    @patch("openai.OpenAI")
    def test_sdk_response_error_chained_and_redacted(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        sentinel_b64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        mock_client.chat.completions.create.side_effect = APIStatusError(
            message=f"Model failed processing image payload {sentinel_b64}",
            response=MagicMock(status_code=500),
            body={"error": "internal"},
        )

        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
            vision_model="vision_qwen_9b",
            validate_model=False,
        )

        sample_img = Path(self.test_dir) / "sample.png"
        with open(sample_img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtestdata")

        with self.assertRaises(LMStudioResponseError) as ctx:
            client.ask_vision(image_path=sample_img, prompt="test error")

        self.assertIsNotNone(ctx.exception.__cause__)
        self.assertNotIn("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", str(ctx.exception))


class TestRealLMStudioVisionIntegration(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("RUN_LMSTUDIO_VISION_INTEGRATION") == "1",
        "Prueba de integración real con LM Studio desactivada por defecto; requiere RUN_LMSTUDIO_VISION_INTEGRATION=1",
    )
    def test_real_lmstudio_vision_query(self):
        import urllib.request
        import json

        base_url = os.getenv("LM_STUDIO_URL", "http://localhost:1234").rstrip("/")
        models_url = f"{base_url}/v1/models" if not base_url.endswith("/v1") else f"{base_url}/models"

        try:
            req = urllib.request.Request(models_url, headers={"User-Agent": "test"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                advertised_models = [m["id"] for m in data.get("data", [])]
        except Exception as e:
            self.fail(f"Servidor LM Studio no disponible en {models_url}: {e}")

        vision_model = os.getenv("LM_STUDIO_VISION_MODEL")
        if not vision_model:
            candidates = [m for m in advertised_models if "embedding" not in m.lower()]
            if not candidates:
                self.fail(f"No hay modelos de texto o visión disponibles en LM Studio ({advertised_models})")
            vision_model = candidates[0]

        temp_dir = tempfile.mkdtemp(prefix="test_real_vlm_")
        try:
            sample_pdf = "26-05-06.pdf"
            test_img = Path(temp_dir) / "test_doc.png"
            if os.path.isfile(sample_pdf):
                from src.fieldnotes.ingest.pdf import extract_pdf_images
                imgs = extract_pdf_images(sample_pdf, temp_dir)
                if imgs:
                    test_img = Path(imgs[0])
            if not test_img.is_file():
                with open(test_img, "wb") as f:
                    f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

            client = LMStudioClient(
                base_url=f"{base_url}/v1" if not base_url.endswith("/v1") else base_url,
                api_key=os.getenv("LM_STUDIO_API_KEY", "lm-studio"),
                vision_model=vision_model,
                validate_model=True,
            )

            res = client.ask_vision(
                image_path=test_img,
                prompt="Describe brevemente este documento.",
                max_tokens=1024,
            )
            self.assertTrue(len(res) > 0, "Respuesta de visión vacía")
            print(f"\nLM_STUDIO_VISION_INTEGRATION_SUCCESS model={vision_model} response_len={len(res)}")
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
