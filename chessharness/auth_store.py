"""Simple local auth token store for web-driven sign-in.

Stores provider tokens in a local JSON file that is gitignored.
This is a pragmatic first step until OS keychain integration lands.
"""

from __future__ import annotations

import json
from pathlib import Path

_AUTH_PATH = Path('.chessharness_auth.json')


def load_auth_tokens() -> dict[str, str]:
    if not _AUTH_PATH.exists():
        return {}
    try:
        return json.loads(_AUTH_PATH.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {}


def save_auth_tokens(tokens: dict[str, str]) -> None:
    _AUTH_PATH.write_text(json.dumps(tokens, indent=2), encoding='utf-8')
