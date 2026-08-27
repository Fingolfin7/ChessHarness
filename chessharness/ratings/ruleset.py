"""Versioned eligibility rules for persistent Harness ratings."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from chessharness.config import GameConfig
from chessharness.players.llm import PROMPT_VERSION


STANDARD_RULESET_ID = "standard-v1"
RETRY_POLICY_VERSION = "output-forfeit-v1"


def _behavior_payload(game: GameConfig) -> dict[str, object]:
    """Return only settings that can change move quality or game eligibility."""

    return {
        "starting_fen": game.starting_fen or "start",
        "board_input": game.board_input,
        "show_legal_moves": game.show_legal_moves,
        "max_retries": game.max_retries,
        "max_output_tokens": game.max_output_tokens,
        "reasoning_effort": game.reasoning_effort,
        "move_timeout": game.move_timeout,
        "prompt_version": PROMPT_VERSION,
        "retry_policy_version": RETRY_POLICY_VERSION,
    }


_STANDARD_GAME = GameConfig()
STANDARD_RULESET_PAYLOAD = _behavior_payload(_STANDARD_GAME)


def ruleset_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


STANDARD_RULESET_HASH = ruleset_hash(STANDARD_RULESET_PAYLOAD)


@dataclass(frozen=True, slots=True)
class RulesetEvaluation:
    pool_id: str
    ruleset_hash: str
    eligible: bool
    reason: str | None
    payload: dict[str, object]


def evaluate_ruleset(game: GameConfig, pool_id: str = STANDARD_RULESET_ID) -> RulesetEvaluation:
    payload = _behavior_payload(game)
    current_hash = ruleset_hash(payload)
    eligible = pool_id == STANDARD_RULESET_ID and current_hash == STANDARD_RULESET_HASH
    if pool_id != STANDARD_RULESET_ID:
        reason = f"Unknown rated pool: {pool_id}"
    elif not eligible:
        changed = [
            key
            for key, expected in STANDARD_RULESET_PAYLOAD.items()
            if payload.get(key) != expected
        ]
        reason = "Non-standard rated settings: " + ", ".join(changed)
    else:
        reason = None
    return RulesetEvaluation(
        pool_id=pool_id,
        ruleset_hash=current_hash,
        eligible=eligible,
        reason=reason,
        payload=payload,
    )
