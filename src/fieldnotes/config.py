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
