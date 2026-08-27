import os
import sys

def setup_encoding():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

def get_lm_studio_url():
    url = os.getenv("LM_STUDIO_URL", "http://localhost:1234").rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"

def get_lm_studio_api_key():
    return os.getenv("LM_STUDIO_API_KEY", "lm-studio")

def get_lm_studio_vision_model() -> str | None:
    return os.getenv("LM_STUDIO_VISION_MODEL")

def get_lm_studio_text_model() -> str | None:
    return os.getenv("LM_STUDIO_TEXT_MODEL")

def get_lm_studio_legacy_model() -> str | None:
    return os.getenv("LM_STUDIO_MODEL")

def get_lm_studio_timeout() -> int:
    val = os.getenv("LM_STUDIO_TIMEOUT", "120")
    try:
        timeout = int(val)
        return timeout if timeout > 0 else 120
    except (ValueError, TypeError):
        return 120

def get_ocr_worker_timeout() -> int:
    val = os.getenv("OCR_WORKER_TIMEOUT", "1800")
    try:
        timeout = int(val)
        return timeout if timeout > 0 else 1800
    except (ValueError, TypeError):
        return 1800


from .schemas.notebook import NotebookConfig, NotebookSectionConfig

__all__ = [
    "setup_encoding",
    "get_lm_studio_url",
    "get_lm_studio_api_key",
    "get_lm_studio_vision_model",
    "get_lm_studio_text_model",
    "get_lm_studio_legacy_model",
    "get_lm_studio_timeout",
    "get_ocr_worker_timeout",
    "NotebookConfig",
    "NotebookSectionConfig",
]
