import unittest
import tempfile
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from src.fieldnotes.ocr.worker import (
    run_ocr_worker,
    run_worker_process,
    write_atomic_json,
    validate_safe_output_path,
    validate_request_dict,
    validate_response_dict,
    OCRError,
    OCRWorkerError,
    OCRWorkerTimeoutError,
    OCRWorkerExecutionError,
    OCRWorkerProtocolError,
    OCRWorkerPathError,
)
from src.fieldnotes.pipeline import UnlimitedOCRAgent


class TestOCRWorker(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_worker_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_write_atomic_json(self):
        target = Path(self.test_dir) / "sub" / "data.json"
        data = {"key": "value", "num": 42}
        write_atomic_json(target, data)

        self.assertTrue(target.is_file())
        with open(target, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded, data)

    def test_validate_safe_output_path(self):
        out_dir = Path(self.test_dir) / "run"
        out_dir.mkdir(parents=True, exist_ok=True)

        valid_file = out_dir / "raw" / "document.md"
        valid_file.parent.mkdir(parents=True, exist_ok=True)
        valid_file.write_text("doc", encoding="utf-8")

        resolved = validate_safe_output_path(str(out_dir), str(valid_file))
        self.assertEqual(resolved, valid_file.resolve())

        # Path escaping out_dir raises OCRWorkerPathError
        outside_file = Path(self.test_dir) / "outside.txt"
        outside_file.write_text("outside", encoding="utf-8")

        with self.assertRaises(OCRWorkerPathError):
            validate_safe_output_path(str(out_dir), str(outside_file))

        # must_exist flag
        nonexistent = out_dir / "missing.txt"
        with self.assertRaises(OCRWorkerProtocolError):
            validate_safe_output_path(str(out_dir), str(nonexistent), must_exist=True)

        # must_be_file flag on a directory
        sub_dir = out_dir / "subfolder"
        sub_dir.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(OCRWorkerProtocolError):
            validate_safe_output_path(str(out_dir), str(sub_dir), must_exist=True, must_be_file=True)

    def test_fake_worker_success_pdf(self):
        fake_worker_script = Path(self.test_dir) / "fake_worker.py"
        script_content = (
            "import sys, json, os\n"
            "req_idx = sys.argv.index('--request') + 1\n"
            "resp_idx = sys.argv.index('--response') + 1\n"
            "req_path = sys.argv[req_idx]\n"
            "resp_path = sys.argv[resp_idx]\n"
            "with open(req_path, 'r', encoding='utf-8') as f:\n"
            "    req = json.load(f)\n"
            "output_dir = req['output_dir']\n"
            "req_id = req['request_id']\n"
            "print('Fake worker starting for request: ' + req_id)\n"
            "sys.stderr.write('Fake worker debug log on stderr\\n')\n"
            "result_file = os.path.join(output_dir, 'result.md')\n"
            "with open(result_file, 'w', encoding='utf-8') as f:\n"
            "    f.write('<PAGE>Pagina 1\\n<PAGE>Pagina 2')\n"
            "raw_dir = os.path.join(output_dir, 'raw')\n"
            "os.makedirs(raw_dir, exist_ok=True)\n"
            "with open(os.path.join(raw_dir, 'document.md'), 'w', encoding='utf-8') as f:\n"
            "    f.write('<PAGE>Pagina 1\\n<PAGE>Pagina 2')\n"
            "with open(os.path.join(raw_dir, 'page_001.md'), 'w', encoding='utf-8') as f:\n"
            "    f.write('Pagina 1\\n')\n"
            "with open(os.path.join(raw_dir, 'page_002.md'), 'w', encoding='utf-8') as f:\n"
            "    f.write('Pagina 2')\n"
            "resp_data = {\n"
            "    'version': 1,\n"
            "    'request_id': req_id,\n"
            "    'status': 'success',\n"
            "    'result_path': result_file,\n"
            "    'mapping_status': 'mapped',\n"
            "    'mapping_error': None,\n"
            "    'page_artifacts': [\n"
            "        {'page_number': 1, 'image_path': req['image_paths'][0], 'raw_path': os.path.join(raw_dir, 'page_001.md'), 'ocr_mapping_status': 'mapped', 'mapping_error': None},\n"
            "        {'page_number': 2, 'image_path': req['image_paths'][1], 'raw_path': os.path.join(raw_dir, 'page_002.md'), 'ocr_mapping_status': 'mapped', 'mapping_error': None}\n"
            "    ],\n"
            "    'error_type': None,\n"
            "    'error_message': None,\n"
            "    'traceback': None\n"
            "}\n"
            "tmp_resp = resp_path + '.tmp'\n"
            "with open(tmp_resp, 'w', encoding='utf-8') as f:\n"
            "    json.dump(resp_data, f)\n"
            "os.replace(tmp_resp, resp_path)\n"
            "sys.exit(0)\n"
        )
        fake_worker_script.write_text(script_content, encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_001")
        os.makedirs(run_dir, exist_ok=True)
        ext_img1 = os.path.join(self.test_dir, "ext_001.png")
        ext_img2 = os.path.join(self.test_dir, "ext_002.png")
        Path(ext_img1).write_text("img1", encoding="utf-8")
        Path(ext_img2).write_text("img2", encoding="utf-8")

        custom_cmd = [sys.executable, str(fake_worker_script)]
        raw_text, page_meta, resp = run_ocr_worker(
            mode="pdf",
            model_name="test-model",
            image_paths=[ext_img1, ext_img2],
            output_dir=run_dir,
            timeout=30,
            custom_worker_cmd=custom_cmd,
        )

        self.assertEqual(raw_text, "<PAGE>Pagina 1\n<PAGE>Pagina 2")
        self.assertEqual(len(page_meta), 2)
        self.assertEqual(page_meta[0]["ocr_mapping_status"], "mapped")
        self.assertEqual(resp["status"], "success")
        self.assertNotIn("raw_text", resp)

        req_id = resp["request_id"]
        stdout_log = os.path.join(run_dir, "ipc", f"worker_{req_id}_stdout.log")
        stderr_log = os.path.join(run_dir, "ipc", f"worker_{req_id}_stderr.log")
        self.assertTrue(os.path.isfile(stdout_log))
        self.assertTrue(os.path.isfile(stderr_log))
        with open(stdout_log, "r", encoding="utf-8") as f:
            self.assertIn("Fake worker starting", f.read())
        with open(stderr_log, "r", encoding="utf-8") as f:
            self.assertIn("Fake worker debug log", f.read())

    def test_timeout_kills_and_waits_child(self):
        fake_worker_script = Path(self.test_dir) / "sleep_worker.py"
        script_content = "import time, sys\ntime.sleep(10)\nsys.exit(0)\n"
        fake_worker_script.write_text(script_content, encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_timeout")
        os.makedirs(run_dir, exist_ok=True)
        custom_cmd = [sys.executable, str(fake_worker_script)]

        with self.assertRaises(OCRWorkerTimeoutError) as ctx:
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=run_dir,
                timeout=1,
                custom_worker_cmd=custom_cmd,
            )

        err = ctx.exception
        self.assertEqual(err.timeout_seconds, 1)
        self.assertIsNotNone(err.request_id)
        self.assertTrue(os.path.isfile(err.stdout_path))
        self.assertTrue(os.path.isfile(err.stderr_path))

    def test_nonzero_exit_code_raises_execution_error(self):
        fake_worker_script = Path(self.test_dir) / "fail_worker.py"
        script_content = (
            "import sys\n"
            "sys.stderr.write('Fatal error in worker! CUDA out of memory simulation\\n')\n"
            "sys.exit(2)\n"
        )
        fake_worker_script.write_text(script_content, encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_fail")
        os.makedirs(run_dir, exist_ok=True)
        custom_cmd = [sys.executable, str(fake_worker_script)]

        with self.assertRaises(OCRWorkerExecutionError) as ctx:
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=run_dir,
                timeout=10,
                custom_worker_cmd=custom_cmd,
            )

        err = ctx.exception
        self.assertEqual(err.returncode, 2)
        self.assertIn("Fatal error in worker", err.error_message or "")
        self.assertTrue(os.path.isfile(err.stderr_path))

    def test_missing_response_raises_protocol_error(self):
        fake_worker_script = Path(self.test_dir) / "no_resp_worker.py"
        fake_worker_script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_no_resp")
        os.makedirs(run_dir, exist_ok=True)
        custom_cmd = [sys.executable, str(fake_worker_script)]

        with self.assertRaises(OCRWorkerProtocolError) as ctx:
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=run_dir,
                timeout=10,
                custom_worker_cmd=custom_cmd,
            )
        self.assertIn("no generó el archivo de respuesta", str(ctx.exception))

    def test_corrupt_response_raises_protocol_error(self):
        fake_worker_script = Path(self.test_dir) / "corrupt_worker.py"
        script_content = (
            "import sys\n"
            "resp_idx = sys.argv.index('--response') + 1\n"
            "resp_path = sys.argv[resp_idx]\n"
            "with open(resp_path, 'w', encoding='utf-8') as f:\n"
            "    f.write('{ invalid json')\n"
            "sys.exit(0)\n"
        )
        fake_worker_script.write_text(script_content, encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_corrupt")
        os.makedirs(run_dir, exist_ok=True)
        custom_cmd = [sys.executable, str(fake_worker_script)]

        with self.assertRaises(OCRWorkerProtocolError) as ctx:
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=run_dir,
                timeout=10,
                custom_worker_cmd=custom_cmd,
            )
        self.assertIn("corrupta o inválida", str(ctx.exception))

    def test_request_id_mismatch_raises_protocol_error(self):
        fake_worker_script = Path(self.test_dir) / "mismatch_worker.py"
        script_content = (
            "import sys, json\n"
            "resp_idx = sys.argv.index('--response') + 1\n"
            "resp_path = sys.argv[resp_idx]\n"
            "resp_data = {'version': 1, 'request_id': 'wrong_id_123', 'status': 'success', 'result_path': 'result.md'}\n"
            "with open(resp_path, 'w', encoding='utf-8') as f:\n"
            "    json.dump(resp_data, f)\n"
            "sys.exit(0)\n"
        )
        fake_worker_script.write_text(script_content, encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_mismatch")
        os.makedirs(run_dir, exist_ok=True)
        custom_cmd = [sys.executable, str(fake_worker_script)]

        with self.assertRaises(OCRWorkerProtocolError) as ctx:
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=run_dir,
                timeout=10,
                custom_worker_cmd=custom_cmd,
            )
        self.assertIn("Discrepancia en request_id", str(ctx.exception))

    def test_path_escape_in_response_raises_error(self):
        fake_worker_script = Path(self.test_dir) / "escape_worker.py"
        script_content = (
            "import sys, json\n"
            "req_idx = sys.argv.index('--request') + 1\n"
            "resp_idx = sys.argv.index('--response') + 1\n"
            "with open(sys.argv[req_idx], 'r', encoding='utf-8') as f:\n"
            "    req = json.load(f)\n"
            "resp_data = {'version': 1, 'request_id': req['request_id'], 'status': 'success', 'result_path': '/etc/passwd', 'mapping_status': 'mapped', 'mapping_error': None, 'page_artifacts': []}\n"
            "with open(sys.argv[resp_idx], 'w', encoding='utf-8') as f:\n"
            "    json.dump(resp_data, f)\n"
            "sys.exit(0)\n"
        )
        fake_worker_script.write_text(script_content, encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_escape")
        os.makedirs(run_dir, exist_ok=True)
        custom_cmd = [sys.executable, str(fake_worker_script)]

        with self.assertRaises(OCRWorkerPathError) as ctx:
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=run_dir,
                timeout=10,
                custom_worker_cmd=custom_cmd,
            )
        self.assertIn("Path escapes output directory", str(ctx.exception))

    def test_paths_with_spaces_in_directory_name(self):
        space_dir = os.path.join(self.test_dir, "test dir with spaces", "run spaces")
        os.makedirs(space_dir, exist_ok=True)

        fake_worker_script = Path(self.test_dir) / "space_worker.py"
        script_content = (
            "import sys, json, os\n"
            "req_idx = sys.argv.index('--request') + 1\n"
            "resp_idx = sys.argv.index('--response') + 1\n"
            "with open(sys.argv[req_idx], 'r', encoding='utf-8') as f:\n"
            "    req = json.load(f)\n"
            "result_file = os.path.join(req['output_dir'], 'result.md')\n"
            "with open(result_file, 'w', encoding='utf-8') as f:\n"
            "    f.write('text in spaces')\n"
            "resp_data = {'version': 1, 'request_id': req['request_id'], 'status': 'success', 'result_path': result_file, 'mapping_status': 'mapped', 'mapping_error': None, 'page_artifacts': []}\n"
            "with open(sys.argv[resp_idx], 'w', encoding='utf-8') as f:\n"
            "    json.dump(resp_data, f)\n"
            "sys.exit(0)\n"
        )
        fake_worker_script.write_text(script_content, encoding="utf-8")

        custom_cmd = [sys.executable, str(fake_worker_script)]
        raw_text, _, resp = run_ocr_worker(
            mode="pdf",
            model_name="test-model",
            image_paths=[],
            output_dir=space_dir,
            timeout=10,
            custom_worker_cmd=custom_cmd,
        )
        self.assertEqual(raw_text, "text in spaces")
        self.assertEqual(resp["status"], "success")

    def test_repeated_calls_generate_unique_request_ids(self):
        fake_worker_script = Path(self.test_dir) / "repeat_worker.py"
        script_content = (
            "import sys, json, os\n"
            "req_idx = sys.argv.index('--request') + 1\n"
            "resp_idx = sys.argv.index('--response') + 1\n"
            "with open(sys.argv[req_idx], 'r', encoding='utf-8') as f:\n"
            "    req = json.load(f)\n"
            "result_file = os.path.join(req['output_dir'], 'result.md')\n"
            "with open(result_file, 'w', encoding='utf-8') as f:\n"
            "    f.write('run output ' + req['request_id'])\n"
            "resp_data = {'version': 1, 'request_id': req['request_id'], 'status': 'success', 'result_path': result_file, 'mapping_status': 'mapped', 'mapping_error': None, 'page_artifacts': []}\n"
            "with open(sys.argv[resp_idx], 'w', encoding='utf-8') as f:\n"
            "    json.dump(resp_data, f)\n"
            "sys.exit(0)\n"
        )
        fake_worker_script.write_text(script_content, encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_repeat")
        os.makedirs(run_dir, exist_ok=True)
        custom_cmd = [sys.executable, str(fake_worker_script)]

        t1, _, r1 = run_ocr_worker(
            mode="image",
            model_name="test-model",
            image_paths=["a.png"],
            output_dir=run_dir,
            timeout=10,
            custom_worker_cmd=custom_cmd,
        )
        t2, _, r2 = run_ocr_worker(
            mode="image",
            model_name="test-model",
            image_paths=["b.png"],
            output_dir=run_dir,
            timeout=10,
            custom_worker_cmd=custom_cmd,
        )

        self.assertNotEqual(r1["request_id"], r2["request_id"])
        self.assertIn(r1["request_id"], t1)
        self.assertIn(r2["request_id"], t2)

    def test_missing_result_file_on_success_raises_protocol_error(self):
        fake_worker_script = Path(self.test_dir) / "missing_result_worker.py"
        script_content = (
            "import sys, json, os\n"
            "req_idx = sys.argv.index('--request') + 1\n"
            "resp_idx = sys.argv.index('--response') + 1\n"
            "with open(sys.argv[req_idx], 'r', encoding='utf-8') as f:\n"
            "    req = json.load(f)\n"
            "result_file = os.path.join(req['output_dir'], 'result.md')\n"
            "# Deliberately do NOT create result.md\n"
            "resp_data = {'version': 1, 'request_id': req['request_id'], 'status': 'success', 'result_path': result_file, 'mapping_status': 'mapped', 'mapping_error': None, 'page_artifacts': []}\n"
            "with open(sys.argv[resp_idx], 'w', encoding='utf-8') as f:\n"
            "    json.dump(resp_data, f)\n"
            "sys.exit(0)\n"
        )
        fake_worker_script.write_text(script_content, encoding="utf-8")

        run_dir = os.path.join(self.test_dir, "run_missing_result")
        os.makedirs(run_dir, exist_ok=True)
        custom_cmd = [sys.executable, str(fake_worker_script)]

        with self.assertRaises(OCRWorkerProtocolError) as ctx:
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=run_dir,
                timeout=10,
                custom_worker_cmd=custom_cmd,
            )
        self.assertIn("does not exist", str(ctx.exception))

    def test_unsupported_protocol_version_raises_protocol_error(self):
        # Invalid version in request
        with self.assertRaises(OCRWorkerProtocolError):
            validate_request_dict({
                "version": 2,
                "request_id": "r1",
                "mode": "pdf",
                "model_name": "m",
                "image_paths": [],
                "output_dir": self.test_dir,
            })

        # Missing version in request
        with self.assertRaises(OCRWorkerProtocolError):
            validate_request_dict({
                "request_id": "r1",
                "mode": "pdf",
                "model_name": "m",
                "image_paths": [],
                "output_dir": self.test_dir,
            })

        # Invalid version in response
        with self.assertRaises(OCRWorkerProtocolError):
            validate_response_dict({
                "version": 99,
                "request_id": "r1",
                "status": "success",
                "result_path": os.path.join(self.test_dir, "result.md"),
            }, "r1", self.test_dir)

    def test_unsupported_mode_in_request_raises_protocol_error(self):
        with self.assertRaises(OCRWorkerProtocolError):
            validate_request_dict({
                "version": 1,
                "request_id": "r1",
                "mode": "unsupported_mode",
                "model_name": "m",
                "image_paths": [],
                "output_dir": self.test_dir,
            })

    def test_malformed_page_artifacts_in_response(self):
        result_file = Path(self.test_dir) / "result.md"
        result_file.write_text("text", encoding="utf-8")

        base_resp = {
            "version": 1,
            "request_id": "r1",
            "status": "success",
            "result_path": str(result_file),
            "mapping_status": "mapped",
            "mapping_error": None,
        }

        # page_artifacts not a list
        with self.assertRaises(OCRWorkerProtocolError):
            validate_response_dict({**base_resp, "page_artifacts": "not-a-list"}, "r1", self.test_dir)

        # page_artifacts item not a dict
        with self.assertRaises(OCRWorkerProtocolError):
            validate_response_dict({**base_resp, "page_artifacts": [123]}, "r1", self.test_dir)

        # page_artifacts invalid page_number (string or <= 0)
        with self.assertRaises(OCRWorkerProtocolError):
            validate_response_dict({**base_resp, "page_artifacts": [
                {"page_number": "1", "image_path": "img.png", "raw_path": None, "ocr_mapping_status": "unaligned", "mapping_error": None}
            ]}, "r1", self.test_dir)

        with self.assertRaises(OCRWorkerProtocolError):
            validate_response_dict({**base_resp, "page_artifacts": [
                {"page_number": 0, "image_path": "img.png", "raw_path": None, "ocr_mapping_status": "unaligned", "mapping_error": None}
            ]}, "r1", self.test_dir)

        # page_artifacts mapped with raw_path None
        with self.assertRaises(OCRWorkerProtocolError):
            validate_response_dict({**base_resp, "page_artifacts": [
                {"page_number": 1, "image_path": "img.png", "raw_path": None, "ocr_mapping_status": "mapped", "mapping_error": None}
            ]}, "r1", self.test_dir)

        # page_artifacts mapped with nonexistent raw_path
        with self.assertRaises(OCRWorkerProtocolError):
            validate_response_dict({**base_resp, "page_artifacts": [
                {"page_number": 1, "image_path": "img.png", "raw_path": os.path.join(self.test_dir, "missing_raw.md"), "ocr_mapping_status": "mapped", "mapping_error": None}
            ]}, "r1", self.test_dir)

    def test_child_entrypoint_unsafe_paths_rejection(self):
        run_dir = Path(self.test_dir) / "run_child"
        run_dir.mkdir(parents=True, exist_ok=True)
        ipc_dir = run_dir / "ipc"
        ipc_dir.mkdir(parents=True, exist_ok=True)

        req_file = ipc_dir / "worker_request_test.json"
        req_data = {
            "version": 1,
            "request_id": "test_req",
            "mode": "image",
            "model_name": "test-model",
            "image_paths": ["img.png"],
            "output_dir": str(run_dir),
        }
        req_file.write_text(json.dumps(req_data), encoding="utf-8")

        # Unsafe response path outside output_dir
        outside_resp = Path(self.test_dir) / "outside_resp.json"
        code = run_worker_process(str(req_file), str(outside_resp))
        self.assertEqual(code, 1)
        self.assertFalse(outside_resp.exists())

        # Unsafe request path outside output_dir
        outside_req = Path(self.test_dir) / "outside_req.json"
        outside_req.write_text(json.dumps(req_data), encoding="utf-8")
        safe_resp = ipc_dir / "worker_response_test.json"
        code2 = run_worker_process(str(outside_req), str(safe_resp))
        self.assertEqual(code2, 1)

    def test_nonpositive_explicit_timeout_raises_value_error(self):
        with self.assertRaises(ValueError):
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=self.test_dir,
                timeout=0,
            )

        with self.assertRaises(ValueError):
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=self.test_dir,
                timeout=-10,
            )

        with self.assertRaises(ValueError):
            run_ocr_worker(
                mode="pdf",
                model_name="test-model",
                image_paths=[],
                output_dir=self.test_dir,
                timeout=True,
            )

        with self.assertRaises(ValueError):
            UnlimitedOCRAgent(output_dir=self.test_dir, worker_timeout=0)

        with self.assertRaises(ValueError):
            UnlimitedOCRAgent(output_dir=self.test_dir, worker_timeout=-5)

    def test_default_worker_startup_cwd_independence(self):
        ext_dir = tempfile.mkdtemp(prefix="ext_test_")
        try:
            repo_root = os.path.abspath(str(Path(__file__).resolve().parents[1]))
            run_dir = os.path.join(ext_dir, "run_test")
            os.makedirs(run_dir, exist_ok=True)

            code = (
                "import sys, os\n"
                f"sys.path.insert(0, r'{repo_root}')\n"
                "from src.fieldnotes.ocr.worker import run_ocr_worker, OCRWorkerExecutionError\n"
                "try:\n"
                "    run_ocr_worker(\n"
                "        mode='image',\n"
                "        model_name='test-nonexistent-model',\n"
                "        image_paths=['fake_nonexistent.png'],\n"
                f"        output_dir=r'{run_dir}',\n"
                "        timeout=10,\n"
                "        custom_worker_cmd=None,\n"
                "    )\n"
                "except OCRWorkerExecutionError as e:\n"
                "    assert 'ModuleNotFoundError: No module named' not in (e.traceback_str or '')\n"
                "    print('DEFAULT_WORKER_CWD_INDEPENDENT_OK')\n"
            )
            p = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                cwd=ext_dir,
            )
            self.assertEqual(p.returncode, 0, f"External cwd run failed: {p.stderr}")
            self.assertIn("DEFAULT_WORKER_CWD_INDEPENDENT_OK", p.stdout)
        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)

    def test_cli_parser_ocr_mode_options(self):
        from src.fieldnotes.cli import build_parser

        parser = build_parser()
        default_args = parser.parse_args(["test.pdf"])
        self.assertEqual(default_args.ocr_mode, "worker")
        self.assertIsNone(default_args.worker_timeout)

        custom_args = parser.parse_args(
            ["test.pdf", "--ocr-mode", "in_process", "--worker-timeout", "1200"]
        )
        self.assertEqual(custom_args.ocr_mode, "in_process")
        self.assertEqual(custom_args.worker_timeout, 1200)


if __name__ == "__main__":
    unittest.main()
