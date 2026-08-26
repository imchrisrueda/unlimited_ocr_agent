import os
from src.fieldnotes.pipeline import UnlimitedOCRAgent
from src.fieldnotes.cli import main

_lm_studio_url = os.getenv("LM_STUDIO_URL", "http://localhost:1234").rstrip("/")
LM_STUDIO_BASE_URL = _lm_studio_url if _lm_studio_url.endswith("/v1") else f"{_lm_studio_url}/v1"
LM_STUDIO_API_KEY = os.getenv("LM_STUDIO_API_KEY", "lm-studio")

if __name__ == "__main__":
    main()
