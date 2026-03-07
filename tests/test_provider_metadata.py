import unittest

from chessharness.providers.anthropic import _message_metadata
from chessharness.providers.google import _response_metadata as google_response_metadata
from chessharness.providers.openai import _completion_metadata
from chessharness.providers.openai_chatgpt import _response_event_metadata


class _Obj:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class ProviderMetadataTests(unittest.TestCase):
    def test_openai_completion_metadata_includes_finish_reason_and_usage(self) -> None:
        response = _Obj(
            id="chatcmpl_123",
            model="gpt-5",
            system_fingerprint="fp_abc",
            created=1234567890,
            choices=[_Obj(finish_reason="length")],
            usage=_Obj(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        )

        metadata = _completion_metadata(response=response)

        self.assertEqual(metadata["finish_reason"], "length")
        self.assertEqual(metadata["usage"], {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18})

    def test_openai_chatgpt_response_event_metadata_normalizes_nested_values(self) -> None:
        event = _Obj(
            type="response.incomplete",
            response=_Obj(
                id="resp_123",
                model="gpt-5-codex",
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                usage={"input_tokens": 50, "output_tokens": 20},
                error=None,
            ),
        )

        metadata = _response_event_metadata(event)

        self.assertEqual(metadata["event_type"], "response.incomplete")
        self.assertEqual(metadata["status"], "incomplete")
        self.assertEqual(metadata["incomplete_details"], {"reason": "max_output_tokens"})
        self.assertEqual(metadata["usage"], {"input_tokens": 50, "output_tokens": 20})

    def test_anthropic_message_metadata_includes_stop_reason(self) -> None:
        message = _Obj(
            id="msg_123",
            model="claude-sonnet",
            stop_reason="max_tokens",
            stop_sequence=None,
            usage=_Obj(input_tokens=22, output_tokens=9),
        )

        metadata = _message_metadata(message)

        self.assertEqual(metadata["stop_reason"], "max_tokens")
        self.assertEqual(metadata["usage"], {"input_tokens": 22, "output_tokens": 9})

    def test_google_response_metadata_includes_finish_reason_and_usage(self) -> None:
        response = _Obj(
            response_id="resp_google_1",
            model_version="gemini-2.5-pro",
            candidates=[_Obj(finish_reason="MAX_TOKENS", safety_ratings=["safe"])],
            prompt_feedback="ok",
            usage_metadata=_Obj(
                prompt_token_count=12,
                candidates_token_count=8,
                total_token_count=20,
                thoughts_token_count=17,
                cached_content_token_count=4,
                tool_use_prompt_token_count=3,
            ),
        )

        metadata = google_response_metadata(response)

        self.assertEqual(metadata["finish_reason"], "MAX_TOKENS")
        self.assertEqual(
            metadata["usage"],
            {
                "prompt_token_count": 12,
                "candidates_token_count": 8,
                "total_token_count": 20,
                "thoughts_token_count": 17,
                "cached_content_token_count": 4,
                "tool_use_prompt_token_count": 3,
            },
        )


if __name__ == "__main__":
    unittest.main()
