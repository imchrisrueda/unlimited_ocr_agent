import json
import unittest
from unittest.mock import MagicMock, patch
from io import BytesIO
import urllib.error

from src.fieldnotes.vlm.lmstudio import LMStudioClient
from src.fieldnotes.cli import build_parser


class TestMemoryManagement(unittest.TestCase):
    @patch("subprocess.run")
    @patch("urllib.request.urlopen", side_effect=OSError("unavailable"))
    def test_loaded_models_cli_json(self, mock_urlopen, mock_subp):
        client = LMStudioClient(base_url="http://localhost:1234/v1", api_key="test-key", validate_model=False)
        mock_subp.return_value = MagicMock(
            returncode=0, stdout='[{"identifier": "vision-instance"}]'
        )
        self.assertEqual(client.get_loaded_models(), ["vision-instance"])
        self.assertEqual(mock_subp.call_args.args[0], ["lms", "ps", "--json"])

    def test_cli_parser_memory_flags(self):
        parser = build_parser()
        args = parser.parse_args(["doc.pdf"])
        self.assertFalse(args.keep_models_loaded)
        self.assertFalse(args.unload_all)

        args = parser.parse_args(["--unload-all"])
        self.assertTrue(args.unload_all)

        args = parser.parse_args(["doc.pdf", "--keep-models-loaded"])
        self.assertTrue(args.keep_models_loaded)

    @patch("torch.cuda.empty_cache")
    @patch("torch.cuda.is_available", return_value=True)
    def test_unlimited_ocr_unload(self, mock_is_avail, mock_empty_cache):
        from src.fieldnotes.ocr.unlimited import UnlimitedOCR

        ocr = object.__new__(UnlimitedOCR)
        ocr.model = MagicMock()
        ocr.tokenizer = MagicMock()

        ocr.unload()

        self.assertIsNone(ocr.model)
        self.assertIsNone(ocr.tokenizer)
        mock_empty_cache.assert_called()

    @patch("src.fieldnotes.pipeline.LMStudioClient")
    def test_agent_unload_methods(self, mock_vlm_cls):
        from src.fieldnotes.pipeline import UnlimitedOCRAgent

        mock_vlm = MagicMock()
        mock_vlm.unload_used_models.return_value = ["test-vision-model"]
        mock_vlm_cls.return_value = mock_vlm

        agent = UnlimitedOCRAgent(output_dir="./test_mem_out", ocr_mode="worker")
        mock_ocr = MagicMock()
        agent.ocr = mock_ocr

        agent.unload_ocr()
        mock_ocr.unload.assert_called_once()
        self.assertIsNone(agent.ocr)

        unloaded = agent.unload_vlm()
        self.assertEqual(unloaded, ["test-vision-model"])

        res = agent.unload_all(include_vlm=True)
        self.assertTrue(res["ocr_unloaded"])
        self.assertEqual(res["vlm_unloaded"], ["test-vision-model"])

        agent.cleanup()

    @patch("urllib.request.urlopen")
    def test_lmstudio_get_loaded_models(self, mock_urlopen):
        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="test-key",
            validate_model=False,
        )

        mock_response = MagicMock()
        payload = {
            "models": [
                {"key": "model-1", "loaded_instances": [{"id": "model-1-inst"}]},
                {"key": "model-2", "loaded_instances": []},
                {"key": "model-3", "loaded_instances": [{"id": "model-3-inst"}]},
            ]
        }
        mock_response.read.return_value = json.dumps(payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        loaded = client.get_loaded_models()
        self.assertEqual(loaded, ["model-1-inst", "model-3-inst"])

    @patch("urllib.request.urlopen")
    def test_lmstudio_unload_model_rest_success(self, mock_urlopen):
        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="test-key",
            validate_model=False,
        )
        client._models_used.add("qwen/qwen3.5-9b")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        success = client.unload_model("qwen/qwen3.5-9b")
        self.assertTrue(success)
        self.assertNotIn("qwen/qwen3.5-9b", client._models_used)

    @patch("subprocess.run")
    @patch("urllib.request.urlopen")
    def test_lmstudio_unload_model_404_uses_fallback(self, mock_urlopen, mock_subp):
        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="test-key",
            validate_model=False,
        )
        # HTTP 404 can also mean that the server lacks this endpoint.
        mock_subp.return_value = MagicMock(returncode=1)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://localhost:1234/api/v1/models/unload",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=BytesIO(b'{"error":"not loaded"}'),
        )

        success = client.unload_model("already-unloaded-model")
        self.assertFalse(success)
        mock_subp.assert_called_once()

    @patch("subprocess.run")
    @patch("urllib.request.urlopen")
    def test_lmstudio_unload_model_cli_fallback(self, mock_urlopen, mock_subp):
        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="test-key",
            validate_model=False,
        )
        # REST fails with connection error
        mock_urlopen.side_effect = Exception("Connection refused")

        mock_subp.return_value = MagicMock(returncode=0)

        success = client.unload_model("fallback-model")
        self.assertTrue(success)
        mock_subp.assert_called_with(
            ["lms", "unload", "fallback-model"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch.object(LMStudioClient, "unload_model", return_value=True)
    def test_lmstudio_unload_used_models(self, mock_unload):
        client = LMStudioClient(
            base_url="http://localhost:1234/v1",
            api_key="test-key",
            vision_model="vision-m",
            text_model="text-m",
            validate_model=False,
        )
        client._models_used.add("extra-m")

        unloaded = client.unload_used_models()
        self.assertIn("vision-m", unloaded)
        self.assertIn("text-m", unloaded)
        self.assertIn("extra-m", unloaded)
        self.assertEqual(len(unloaded), 3)


if __name__ == "__main__":
    unittest.main()
