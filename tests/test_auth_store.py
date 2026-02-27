import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from chessharness import auth_store


class AuthStoreTests(unittest.TestCase):
    def _make_auth_path(self) -> Path:
        return Path(f".test_auth_{uuid.uuid4().hex}.json")

    def test_save_and_load_round_trip(self) -> None:
        auth_path = self._make_auth_path()
        self.addCleanup(lambda: auth_path.unlink(missing_ok=True))
        with patch.object(auth_store, "_AUTH_PATH", auth_path):
            data = {"openai": "tok_a", "copilot_chat": "tok_b"}
            auth_store.save_auth_tokens(data)
            loaded = auth_store.load_auth_tokens()
            self.assertEqual(loaded, data)

    def test_load_invalid_json_returns_empty_dict(self) -> None:
        auth_path = self._make_auth_path()
        self.addCleanup(lambda: auth_path.unlink(missing_ok=True))
        auth_path.write_text("{bad json", encoding="utf-8")
        with patch.object(auth_store, "_AUTH_PATH", auth_path):
            loaded = auth_store.load_auth_tokens()
            self.assertEqual(loaded, {})

    def test_load_missing_file_returns_empty_dict(self) -> None:
        auth_path = self._make_auth_path()
        self.addCleanup(lambda: auth_path.unlink(missing_ok=True))
        with patch.object(auth_store, "_AUTH_PATH", auth_path):
            loaded = auth_store.load_auth_tokens()
            self.assertEqual(loaded, {})
