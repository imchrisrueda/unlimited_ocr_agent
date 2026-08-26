import unittest
import os
from unittest.mock import patch
from src.fieldnotes.config import get_lm_studio_url, get_lm_studio_api_key

class TestConfig(unittest.TestCase):
    def test_get_lm_studio_url_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_lm_studio_url(), "http://localhost:1234/v1")

    def test_get_lm_studio_url_custom(self):
        with patch.dict(os.environ, {"LM_STUDIO_URL": "http://my-server"}):
            self.assertEqual(get_lm_studio_url(), "http://my-server/v1")
            
    def test_get_lm_studio_api_key_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_lm_studio_api_key(), "lm-studio")

    def test_get_lm_studio_api_key_custom(self):
        with patch.dict(os.environ, {"LM_STUDIO_API_KEY": "my-key"}):
            self.assertEqual(get_lm_studio_api_key(), "my-key")

if __name__ == '__main__':
    unittest.main()
