import os
import unittest
from unittest.mock import patch

from web_main import _reload_enabled


class WebMainTests(unittest.TestCase):
    def test_reload_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_reload_enabled())

    def test_reload_requires_explicit_truthy_environment_value(self) -> None:
        with patch.dict(os.environ, {"CHESSHARNESS_RELOAD": "1"}, clear=True):
            self.assertTrue(_reload_enabled())
        with patch.dict(os.environ, {"CHESSHARNESS_RELOAD": "off"}, clear=True):
            self.assertFalse(_reload_enabled())


if __name__ == "__main__":
    unittest.main()

