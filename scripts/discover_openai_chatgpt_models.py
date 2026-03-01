from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from openai import OpenAI


AUTH_STORE_PATH = Path(".chessharness_auth.json")
CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"
PROVIDER_NAME = "openai_chatgpt"
DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_CLIENT_VERSION = "1.0.0"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"Unexpected JSON format in {path}")
    return raw


def _read_auth_store() -> dict[str, str]:
    raw = _read_json(AUTH_STORE_PATH)
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, str):
            out[str(k)] = v
    return out


def _read_codex_auth_token() -> str:
    raw = _read_json(CODEX_AUTH_PATH)
    api_key = raw.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip()
    tokens = raw.get("tokens")
    if isinstance(tokens, dict):
        access_token = tokens.get("access_token")
        if isinstance(access_token, str) and access_token.strip():
            return access_token.strip()
    return ""


def _resolve_token_and_base_url() -> tuple[str, str]:
    auth = _read_auth_store()
    token = auth.get(PROVIDER_NAME, "").strip()
    base_url = auth.get(f"{PROVIDER_NAME}__base_url", "").strip() or DEFAULT_BASE_URL
    if token:
        return token, base_url

    token = _read_codex_auth_token()
    if token:
        return token, base_url

    raise RuntimeError(
        "No token found. Connect openai_chatgpt in the app or run `codex login` first."
    )


def _discover_models(token: str, base_url: str) -> list[str]:
    # First try via SDK.
    try:
        client = OpenAI(api_key=token, base_url=base_url)
        resp = client.models.list()
        ids: list[str] = []
        for model in resp:
            model_id = getattr(model, "id", None)
            if model_id:
                ids.append(str(model_id))
        if ids:
            return sorted(set(ids), key=str.lower)
    except Exception:
        pass

    # Fallback: direct HTTP with required `client_version` query parameter.
    url = (
        base_url.rstrip("/")
        + "/models?"
        + urllib.parse.urlencode({"client_version": DEFAULT_CLIENT_VERSION})
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "ChessHarness/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected models response type: {type(data).__name__}")

    raw_models = data.get("data")
    if not isinstance(raw_models, list):
        # Codex endpoint commonly returns {"models": [...]} with "slug" fields.
        raw_models = data.get("models")
    if not isinstance(raw_models, list):
        raise RuntimeError(
            "Unexpected models response payload: expected list in 'data' or 'models'"
        )

    ids = []
    for item in raw_models:
        if isinstance(item, dict):
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                model_id = item.get("slug")
            if isinstance(model_id, str) and model_id.strip():
                ids.append(model_id.strip())
    return sorted(set(ids), key=str.lower)


def _display_name(model_id: str) -> str:
    return model_id


def _build_provider_block(model_ids: list[str], *, base_url: str) -> str:
    lines = [
        "  # OpenAI ChatGPT/Codex session auth (supports \"Use Codex Login\" in the web UI)",
        "  openai_chatgpt:",
        f"    base_url: {json.dumps(base_url)}",
        "    models:",
    ]
    for model_id in model_ids:
        lines.append(f"      - id: {json.dumps(model_id)}")
        lines.append(f"        name: {json.dumps(_display_name(model_id))}")
        lines.append("        supports_vision: true")
    return "\n".join(lines) + "\n"


def _replace_provider_block_text(text: str, provider: str, new_block: str) -> str:
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"

    spans: list[tuple[int, int]] = []
    i = 0
    needle = f"{provider}:"
    while i < len(lines):
        line = lines[i]
        if line.startswith("  ") and line.strip() == needle:
            start = i
            j = i - 1
            while j >= 0 and (lines[j].startswith("  #") or lines[j].strip() == ""):
                j -= 1
            start = j + 1

            end = len(lines)
            k = i + 1
            while k < len(lines):
                probe = lines[k]
                if probe.startswith("  ") and not probe.startswith("    ") and probe.strip().endswith(":"):
                    end = k
                    break
                k += 1
            spans.append((start, end))
            i = end
            continue
        i += 1

    if not spans:
        raise RuntimeError(f"Could not find provider block '{provider}' to replace")

    new_block_text = new_block.replace("\n", newline)
    first_start = spans[0][0]
    kept_chunks: list[str] = []
    cursor = 0
    for start, end in spans:
        kept_chunks.append("".join(lines[cursor:start]))
        cursor = end
    kept_chunks.append("".join(lines[cursor:]))
    without_old = "".join(kept_chunks)

    prefix = "".join(lines[:first_start])
    suffix = without_old[len(prefix):]
    return prefix + new_block_text + suffix


def _update_config_file(path: Path, model_ids: list[str], *, base_url: str) -> None:
    if not path.exists():
        print(f"skip: {path} (not found)")
        return
    original = path.read_text(encoding="utf-8")
    generated = _build_provider_block(model_ids, base_url=base_url)
    updated = _replace_provider_block_text(original, PROVIDER_NAME, generated)
    path.write_text(updated, encoding="utf-8")
    print(f"updated: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover models exposed by openai_chatgpt (Codex endpoint).",
    )
    parser.add_argument(
        "--no-write-config",
        action="store_true",
        help="Only print discovered models; do not update config files.",
    )
    args = parser.parse_args()

    try:
        token, base_url = _resolve_token_and_base_url()
        model_ids = _discover_models(token, base_url)
        if not model_ids:
            raise RuntimeError("No models returned from openai_chatgpt models.list()")

        print(f"discovered {len(model_ids)} openai_chatgpt models")
        for model_id in model_ids:
            print(f"  - {model_id}")

        if not args.no_write_config:
            _update_config_file(Path("config.yaml"), model_ids, base_url=base_url)
            _update_config_file(Path("config.example.yaml"), model_ids, base_url=base_url)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
