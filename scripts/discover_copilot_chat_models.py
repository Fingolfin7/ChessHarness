from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from openai import OpenAI


AUTH_PATH = Path(".chessharness_auth.json")
COPILOT_BASE_URL = "https://api.githubcopilot.com"
COPILOT_PROVIDER_NAMES = ("copilot_chat", "copilot")


def _read_auth_tokens() -> dict[str, str]:
    if not AUTH_PATH.exists():
        raise RuntimeError(f"Auth store not found: {AUTH_PATH}")
    try:
        raw = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {AUTH_PATH}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"Unexpected auth store format in {AUTH_PATH}")
    return {str(k): str(v) for k, v in raw.items()}


def _github_http_get(url: str, *, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"token {token}",
            "User-Agent": "ChessHarness/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub HTTP {exc.code}: {body[:500]}") from exc


def _exchange_copilot_token(github_token: str) -> str:
    result = _github_http_get("https://api.github.com/copilot_internal/v2/token", token=github_token)
    token = str(result.get("token", "")).strip()
    if not token:
        raise RuntimeError(f"Copilot token exchange failed: {result}")
    return token


def _copilot_headers() -> dict[str, str]:
    return {
        "Editor-Version": "vscode/1.95.3",
        "Editor-Plugin-Version": "copilot-chat/0.22.1",
        "Copilot-Integration-Id": "vscode-chat",
    }


def _discover_models(access_token: str) -> list[str]:
    client = OpenAI(
        api_key=access_token,
        base_url=COPILOT_BASE_URL,
        default_headers=_copilot_headers(),
    )
    # Some SDK versions return a pager-like object; iterate defensively.
    resp = client.models.list()
    ids: list[str] = []
    for model in resp:
        model_id = getattr(model, "id", None)
        if model_id:
            ids.append(str(model_id))
    ids = sorted(set(ids), key=str.lower)
    return [m for m in ids if "embedding" not in m.lower()]


def _display_name(model_id: str) -> str:
    return model_id


def _build_provider_block(model_ids: list[str]) -> str:
    lines = [
        "  # GitHub Copilot Chat (sign in via the web UI; device flow recommended)",
        "  copilot_chat:",
        f'    base_url: "{COPILOT_BASE_URL}"',
        "    models:",
    ]
    for model_id in model_ids:
        lines.append(f"      - id: {json.dumps(model_id)}")
        lines.append(f'        name: "{_display_name(model_id)}"')
    return "\n".join(lines) + "\n"


def _replace_provider_block_text(text: str, new_block: str) -> str:
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"

    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped in {"copilot:", "copilot_chat:"} and line.startswith("  "):
            start = i
            # Include contiguous leading comments/blanks immediately above this provider.
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
        raise RuntimeError("Could not find copilot/copilot_chat provider block to replace")
    new_block_text = new_block.replace("\n", newline)
    first_start = spans[0][0]
    kept_chunks: list[str] = []
    cursor = 0
    for start, end in spans:
        kept_chunks.append("".join(lines[cursor:start]))
        cursor = end
    kept_chunks.append("".join(lines[cursor:]))
    without_old = "".join(kept_chunks)

    # Insert at the original first span location in the post-removal text.
    prefix = "".join(lines[:first_start])
    suffix = without_old[len(prefix):]
    return prefix + new_block_text + suffix


def _update_config_file(path: Path, model_ids: list[str]) -> None:
    if not path.exists():
        print(f"skip: {path} (not found)")
        return

    original = path.read_text(encoding="utf-8")

    generated = _build_provider_block(model_ids)

    updated = _replace_provider_block_text(original, generated)
    path.write_text(updated, encoding="utf-8")
    print(f"updated: {path}")


def main() -> int:
    try:
        auth = _read_auth_tokens()
        github_token = ""
        for provider in COPILOT_PROVIDER_NAMES:
            github_token = auth.get(f"{provider}__github_token", "").strip()
            if github_token:
                break
        if not github_token:
            raise RuntimeError(
                "No Copilot GitHub token found in .chessharness_auth.json "
                "(expected copilot_chat__github_token)"
            )

        access_token = _exchange_copilot_token(github_token)
        model_ids = _discover_models(access_token)
        if not model_ids:
            raise RuntimeError("No models returned from Copilot Chat models.list()")

        print(f"discovered {len(model_ids)} Copilot Chat models")
        for model_id in model_ids:
            print(f"  - {model_id}")

        _update_config_file(Path("config.yaml"), model_ids)
        _update_config_file(Path("config.example.yaml"), model_ids)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
