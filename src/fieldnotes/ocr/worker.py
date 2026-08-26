import os
import sys
import json
import uuid
import subprocess
import traceback
from pathlib import Path
from typing import Literal, Optional, Any
from ..config import setup_encoding, get_ocr_worker_timeout

ALLOWED_MODES = ("image", "pdf")
ALLOWED_MAPPING_STATUSES = ("pending", "mapped", "unaligned")


class OCRError(Exception):
    """Base exception for OCR-related errors."""


class OCRWorkerError(OCRError):
    """Base exception for OCR worker subprocess errors."""


class OCRWorkerTimeoutError(OCRWorkerError):
    """Raised when the OCR worker subprocess exceeds its configured timeout."""

    def __init__(
        self,
        message: str,
        timeout_seconds: int,
        command: Optional[list[str]] = None,
        request_id: Optional[str] = None,
        stdout_path: Optional[str] = None,
        stderr_path: Optional[str] = None,
    ):
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.command = command
        self.request_id = request_id
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path


class OCRWorkerExecutionError(OCRWorkerError):
    """Raised when the OCR worker subprocess exits with a non-zero return code or returns an error status."""

    def __init__(
        self,
        message: str,
        returncode: Optional[int] = None,
        command: Optional[list[str]] = None,
        request_id: Optional[str] = None,
        stdout_path: Optional[str] = None,
        stderr_path: Optional[str] = None,
        error_message: Optional[str] = None,
        traceback_str: Optional[str] = None,
    ):
        super().__init__(message)
        self.returncode = returncode
        self.command = command
        self.request_id = request_id
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.error_message = error_message
        self.traceback_str = traceback_str


class OCRWorkerProtocolError(OCRWorkerError):
    """Raised when the IPC protocol is violated (missing response, invalid JSON, request_id mismatch, path escape)."""

    def __init__(
        self,
        message: str,
        request_id: Optional[str] = None,
        response_path: Optional[str] = None,
        stdout_path: Optional[str] = None,
        stderr_path: Optional[str] = None,
        details: Optional[str] = None,
    ):
        super().__init__(message)
        self.request_id = request_id
        self.response_path = response_path
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.details = details


class OCRWorkerPathError(OCRWorkerProtocolError):
    """Raised when a path in the worker request/response escapes the expected root."""


def validate_safe_output_path(
    output_dir: str,
    target_path: str,
    must_exist: bool = False,
    must_be_file: bool = False,
) -> Path:
    """Valida que target_path se encuentre estrictamente dentro de output_dir y opcionalmente exista."""
    real_out = Path(output_dir).resolve()
    real_target = Path(target_path).resolve()
    try:
        real_target.relative_to(real_out)
    except ValueError as exc:
        raise OCRWorkerPathError(
            f"Path escapes output directory: target='{target_path}', output_dir='{output_dir}'",
            details=str(exc),
        ) from exc

    if must_exist and not real_target.exists():
        raise OCRWorkerProtocolError(
            f"Required output file does not exist on disk: target='{target_path}'"
        )

    if must_be_file and not real_target.is_file():
        raise OCRWorkerProtocolError(
            f"Expected a regular file at output path: target='{target_path}'"
        )

    return real_target


def validate_request_dict(req: Any) -> dict[str, Any]:
    """Valida estrictamente el esquema y tipos de una petición de worker."""
    if not isinstance(req, dict):
        raise OCRWorkerProtocolError("Worker request must be a JSON object")
    if (
        req.get("version") != 1
        or not isinstance(req.get("version"), int)
        or isinstance(req.get("version"), bool)
    ):
        raise OCRWorkerProtocolError(
            f"Unsupported or missing protocol version in request: {req.get('version')!r}"
        )

    req_id = req.get("request_id")
    if not isinstance(req_id, str) or not req_id.strip():
        raise OCRWorkerProtocolError(f"Invalid or empty request_id in request: {req_id!r}")

    mode = req.get("mode")
    if mode not in ALLOWED_MODES:
        raise OCRWorkerProtocolError(
            f"Invalid mode in request: {mode!r}. Must be one of {ALLOWED_MODES}"
        )

    model_name = req.get("model_name")
    if not isinstance(model_name, str) or not model_name.strip():
        raise OCRWorkerProtocolError(
            f"Invalid or empty model_name in request: {model_name!r}"
        )

    image_paths = req.get("image_paths")
    if not isinstance(image_paths, list) or not all(isinstance(p, str) for p in image_paths):
        raise OCRWorkerProtocolError(
            f"image_paths must be a list of strings, got: {image_paths!r}"
        )

    output_dir = req.get("output_dir")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise OCRWorkerProtocolError(f"Invalid output_dir in request: {output_dir!r}")

    return req


def validate_response_dict(
    resp: Any, expected_request_id: str, output_dir: str
) -> dict[str, Any]:
    """Valida estrictamente el esquema, tipos y confinamiento de rutas de la respuesta."""
    if not isinstance(resp, dict):
        raise OCRWorkerProtocolError("Worker response must be a JSON object")

    if (
        resp.get("version") != 1
        or not isinstance(resp.get("version"), int)
        or isinstance(resp.get("version"), bool)
    ):
        raise OCRWorkerProtocolError(
            f"Unsupported or missing protocol version in response: {resp.get('version')!r}"
        )

    req_id = resp.get("request_id")
    if req_id != expected_request_id:
        raise OCRWorkerProtocolError(
            f"Discrepancia en request_id: se esperaba '{expected_request_id}' pero se recibió '{req_id}'",
            request_id=expected_request_id,
        )

    status = resp.get("status")

    if status == "error":
        err_msg = resp.get("error_message") or "Worker reported error"
        raise OCRWorkerExecutionError(
            err_msg,
            request_id=expected_request_id,
            error_message=err_msg,
            traceback_str=resp.get("traceback"),
        )
    elif status != "success":
        raise OCRWorkerProtocolError(
            f"Invalid status in response: {status!r}. Must be 'success' or 'error'"
        )

    result_path = resp.get("result_path")
    if not isinstance(result_path, str) or not result_path.strip():
        raise OCRWorkerProtocolError(
            f"Invalid or missing result_path in success response: {result_path!r}"
        )

    # Debe ser un archivo existente dentro de output_dir
    validate_safe_output_path(
        output_dir, result_path, must_exist=True, must_be_file=True
    )

    mapping_status = resp.get("mapping_status")
    if mapping_status not in ALLOWED_MAPPING_STATUSES:
        raise OCRWorkerProtocolError(
            f"Invalid mapping_status in response: {mapping_status!r}"
        )

    page_artifacts = resp.get("page_artifacts")
    if not isinstance(page_artifacts, list):
        raise OCRWorkerProtocolError(
            f"page_artifacts must be a list, got: {type(page_artifacts).__name__}"
        )

    for idx, art in enumerate(page_artifacts):
        if not isinstance(art, dict):
            raise OCRWorkerProtocolError(
                f"page_artifacts[{idx}] must be a dict, got {type(art).__name__}"
            )

        page_num = art.get("page_number")
        if (
            not isinstance(page_num, int)
            or isinstance(page_num, bool)
            or page_num < 1
        ):
            raise OCRWorkerProtocolError(
                f"page_artifacts[{idx}] has invalid page_number: {page_num!r}"
            )

        img_p = art.get("image_path")
        if not isinstance(img_p, str) or not img_p.strip():
            raise OCRWorkerProtocolError(
                f"page_artifacts[{idx}] has invalid image_path: {img_p!r}"
            )

        art_status = art.get("ocr_mapping_status")
        if art_status not in ALLOWED_MAPPING_STATUSES:
            raise OCRWorkerProtocolError(
                f"page_artifacts[{idx}] has invalid ocr_mapping_status: {art_status!r}"
            )

        raw_p = art.get("raw_path")
        if raw_p is not None:
            if not isinstance(raw_p, str) or not raw_p.strip():
                raise OCRWorkerProtocolError(
                    f"page_artifacts[{idx}] has invalid raw_path: {raw_p!r}"
                )
            validate_safe_output_path(
                output_dir,
                raw_p,
                must_exist=(art_status == "mapped"),
                must_be_file=(art_status == "mapped"),
            )
        elif art_status == "mapped":
            raise OCRWorkerProtocolError(
                f"page_artifacts[{idx}] has ocr_mapping_status='mapped' but raw_path is None"
            )

        mapping_err = art.get("mapping_error")
        if mapping_err is not None and not isinstance(mapping_err, str):
            raise OCRWorkerProtocolError(
                f"page_artifacts[{idx}] has invalid mapping_error: {mapping_err!r}"
            )

    return resp


def write_atomic_json(target_path: str | Path, data: dict[str, Any]) -> None:
    """Escribe un archivo JSON atómicamente utilizando un archivo temporal intermedio."""
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.tmp_{uuid.uuid4().hex}")
    with open(temp_path, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, target_path)


def run_worker_process(request_path: str, response_path: str) -> int:
    """Punto de entrada ejecutado dentro del subproceso hijo."""
    setup_encoding()
    request_id = None
    output_dir = None
    can_write_response = False

    try:
        if not os.path.isfile(request_path):
            sys.stderr.write(f"Request file does not exist: {request_path}\n")
            return 1

        with open(request_path, "r", encoding="utf-8") as f:
            raw_req = json.load(f)

        req = validate_request_dict(raw_req)
        request_id = req["request_id"]
        mode = req["mode"]
        model_name = req["model_name"]
        image_paths = req["image_paths"]
        output_dir = req["output_dir"]

        if not os.path.isdir(output_dir):
            sys.stderr.write(f"Output directory does not exist: {output_dir}\n")
            return 1

        # Validar confinamiento de request_path y response_path a output_dir
        validate_safe_output_path(output_dir, request_path)
        validate_safe_output_path(output_dir, response_path)
        can_write_response = True

        from .unlimited import UnlimitedOCR
        from ..artifacts import PageArtifact

        ocr = UnlimitedOCR(model_name=model_name, output_dir=output_dir)

        if mode == "image":
            if not image_paths:
                raise ValueError("No image path provided for mode='image'")
            raw_text = ocr.extract_from_image(image_paths[0])
            result_path = os.path.join(output_dir, "result.md")
            if not os.path.isfile(result_path):
                raise FileNotFoundError(f"result.md was not created at {result_path}")
            raw_path = os.path.join(output_dir, "raw", "page_001.md")
            resp_data = {
                "version": 1,
                "request_id": request_id,
                "status": "success",
                "result_path": result_path,
                "mapping_status": "mapped",
                "mapping_error": None,
                "page_artifacts": [
                    {
                        "page_number": 1,
                        "image_path": str(image_paths[0]),
                        "raw_path": raw_path if os.path.isfile(raw_path) else None,
                        "ocr_mapping_status": "mapped",
                        "mapping_error": None,
                    }
                ],
                "error_type": None,
                "error_message": None,
                "traceback": None,
            }
        elif mode == "pdf":
            raw_text = ocr.extract_from_images(image_paths)
            artifacts = [
                PageArtifact(page_number=i, image_path=Path(p))
                for i, p in enumerate(image_paths, start=1)
            ]
            processed = ocr.process_page_artifacts(artifacts, raw_text)
            result_path = os.path.join(output_dir, "result.md")
            if not os.path.isfile(result_path):
                raise FileNotFoundError(f"result.md was not created at {result_path}")

            mapping_status = (
                "mapped"
                if all(a.ocr_mapping_status == "mapped" for a in processed)
                else "unaligned"
            )
            mapping_error = next(
                (a.mapping_error for a in processed if a.mapping_error is not None), None
            )
            page_artifacts_data = []
            for a in processed:
                raw_file_p = os.path.join(output_dir, "raw", f"page_{a.page_number:03d}.md")
                has_raw_file = (
                    os.path.isfile(raw_file_p) and a.ocr_mapping_status == "mapped"
                )
                page_artifacts_data.append(
                    {
                        "page_number": a.page_number,
                        "image_path": str(a.image_path),
                        "raw_path": raw_file_p if has_raw_file else None,
                        "ocr_mapping_status": a.ocr_mapping_status,
                        "mapping_error": a.mapping_error,
                    }
                )
            resp_data = {
                "version": 1,
                "request_id": request_id,
                "status": "success",
                "result_path": result_path,
                "mapping_status": mapping_status,
                "mapping_error": mapping_error,
                "page_artifacts": page_artifacts_data,
                "error_type": None,
                "error_message": None,
                "traceback": None,
            }
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        write_atomic_json(response_path, resp_data)
        return 0

    except Exception as exc:
        traceback_str = traceback.format_exc()
        sys.stderr.write(f"Worker process error: {exc}\n{traceback_str}\n")
        if can_write_response and response_path:
            err_data = {
                "version": 1,
                "request_id": request_id,
                "status": "error",
                "result_path": None,
                "mapping_status": "unaligned",
                "mapping_error": str(exc),
                "page_artifacts": [],
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback_str,
            }
            try:
                write_atomic_json(response_path, err_data)
            except Exception as write_err:
                sys.stderr.write(f"Failed to write error response: {write_err}\n")
        return 1


def run_ocr_worker(
    mode: Literal["pdf", "image"],
    model_name: str,
    image_paths: list[str],
    output_dir: str,
    timeout: Optional[int] = None,
    custom_worker_cmd: Optional[list[str]] = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Invoca Unlimited-OCR en un subproceso hijo mediante IPC determinista con request_id correlacionado.

    Retorna:
        tuple[raw_text, page_artifacts_meta, response_dict]
    """
    if timeout is not None:
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError(f"timeout must be a positive integer, got: {timeout!r}")
    else:
        timeout = get_ocr_worker_timeout()

    if mode not in ALLOWED_MODES:
        raise ValueError(f"Invalid mode: {mode!r}. Must be one of {ALLOWED_MODES}")

    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError(f"Invalid model_name: {model_name!r}")

    if not isinstance(image_paths, list) or not all(isinstance(p, str) for p in image_paths):
        raise ValueError(f"image_paths must be a list of strings, got: {image_paths!r}")

    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ValueError(f"Invalid output_dir: {output_dir!r}")

    request_id = uuid.uuid4().hex
    ipc_dir = os.path.join(output_dir, "ipc")
    os.makedirs(ipc_dir, exist_ok=True)

    request_path = os.path.join(ipc_dir, f"worker_request_{request_id}.json")
    response_path = os.path.join(ipc_dir, f"worker_response_{request_id}.json")
    stdout_path = os.path.join(ipc_dir, f"worker_{request_id}_stdout.log")
    stderr_path = os.path.join(ipc_dir, f"worker_{request_id}_stderr.log")

    # Limpiar cualquier respuesta previa con ese nombre por precaución
    if os.path.exists(response_path):
        try:
            os.remove(response_path)
        except OSError:
            pass

    req_data = {
        "version": 1,
        "request_id": request_id,
        "mode": mode,
        "model_name": model_name,
        "image_paths": image_paths,
        "output_dir": output_dir,
    }
    write_atomic_json(request_path, req_data)

    repo_root = str(Path(__file__).resolve().parents[3])
    src_dir = str(Path(__file__).resolve().parents[2])

    env = os.environ.copy()
    pythonpath_entries = [repo_root, src_dir]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    if custom_worker_cmd:
        cmd = list(custom_worker_cmd) + [
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "src.fieldnotes.ocr.worker",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        ]

    with open(stdout_path, "wb") as f_out, open(stderr_path, "wb") as f_err:
        proc = subprocess.Popen(
            cmd,
            stdout=f_out,
            stderr=f_err,
            shell=False,
            env=env,
        )
        try:
            proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            raise OCRWorkerTimeoutError(
                f"OCR worker superó el tiempo de espera ({timeout} s)",
                timeout_seconds=timeout,
                command=cmd,
                request_id=request_id,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            ) from exc

    # Validar código de salida
    if proc.returncode != 0:
        err_msg = f"El subproceso OCR finalizó con código de salida {proc.returncode}"
        traceback_str = None
        stderr_content = ""
        if os.path.exists(stderr_path):
            try:
                with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                    stderr_content = f.read().strip()
            except Exception:
                pass

        if os.path.exists(response_path):
            try:
                with open(response_path, "r", encoding="utf-8") as f:
                    err_resp = json.load(f)
                    if isinstance(err_resp, dict) and err_resp.get("error_message"):
                        err_msg = f"{err_msg}: {err_resp.get('error_message')}"
                        traceback_str = err_resp.get("traceback")
            except Exception:
                pass

        if not traceback_str and stderr_content:
            err_msg = f"{err_msg}\nDetalles (stderr):\n{stderr_content}"

        raise OCRWorkerExecutionError(
            err_msg,
            returncode=proc.returncode,
            command=cmd,
            request_id=request_id,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error_message=err_msg,
            traceback_str=traceback_str or stderr_content,
        )

    # Validar existencia de respuesta
    if not os.path.isfile(response_path):
        raise OCRWorkerProtocolError(
            f"El worker finalizó pero no generó el archivo de respuesta: {response_path}",
            request_id=request_id,
            response_path=response_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            details="Archivo de respuesta inexistente.",
        )

    try:
        with open(response_path, "r", encoding="utf-8") as f:
            raw_resp = json.load(f)
    except json.JSONDecodeError as exc:
        raise OCRWorkerProtocolError(
            f"Respuesta JSON corrupta o inválida en {response_path}: {exc}",
            request_id=request_id,
            response_path=response_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            details=str(exc),
        ) from exc

    # Validar estrictamente el diccionario de respuesta
    resp = validate_response_dict(raw_resp, request_id, output_dir)

    result_path = resp["result_path"]
    with open(result_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    return raw_text, resp.get("page_artifacts", []), resp


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Unlimited-OCR isolated subprocess worker")
    parser.add_argument("--request", required=True, help="Ruta al archivo JSON de petición")
    parser.add_argument("--response", required=True, help="Ruta al archivo JSON de respuesta")
    args = parser.parse_args()

    exit_code = run_worker_process(args.request, args.response)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
