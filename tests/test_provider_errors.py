import unittest

from chessharness.providers.base import ProviderError


class _StatusError(Exception):
    def __init__(self, status_code: int, message: str = "upstream failure") -> None:
        self.status_code = status_code
        super().__init__(message)


class ProviderErrorTests(unittest.TestCase):
    def test_timeout_cause_is_retryable_timeout(self) -> None:
        error = ProviderError(
            "provider",
            "request timed out while uploading image",
            cause=TimeoutError("timed out"),
        )

        self.assertEqual(error.kind, "timeout")
        self.assertTrue(error.retryable)

    def test_explicit_unsupported_image_response_is_not_a_timeout(self) -> None:
        error = ProviderError(
            "provider",
            "image input is not supported for this model",
            cause=_StatusError(400),
        )

        self.assertEqual(error.kind, "image_unsupported")
        self.assertFalse(error.retryable)

    def test_status_codes_cover_rate_limit_auth_and_upstream(self) -> None:
        self.assertEqual(
            ProviderError("provider", "too many requests", cause=_StatusError(429)).kind,
            "rate_limit",
        )
        self.assertFalse(
            ProviderError("provider", "request failed", cause=_StatusError(401)).retryable
        )
        upstream = ProviderError("provider", "request failed", cause=_StatusError(503))
        self.assertEqual(upstream.kind, "upstream")
        self.assertTrue(upstream.retryable)

    def test_empty_response_is_retryable(self) -> None:
        error = ProviderError("provider", "Provider returned no output tokens.")

        self.assertEqual(error.kind, "empty_response")
        self.assertTrue(error.retryable)

    def test_unknown_provider_error_keeps_one_retry_eligible(self) -> None:
        error = ProviderError("third-party", "temporary outage")

        self.assertEqual(error.kind, "unknown")
        self.assertTrue(error.retryable)

    def test_explicit_kind_and_retryability_are_preserved(self) -> None:
        error = ProviderError(
            "provider",
            "custom failure",
            kind="auth",
            retryable=True,
        )

        self.assertEqual(error.kind, "auth")
        self.assertTrue(error.retryable)
        self.assertEqual(str(error), "[provider] custom failure")

    def test_wrapping_provider_error_preserves_classification(self) -> None:
        original = ProviderError("provider", "connection lost", kind="upstream")
        wrapped = ProviderError("wrapper", "request failed", cause=original)

        self.assertEqual(wrapped.kind, "upstream")
        self.assertTrue(wrapped.retryable)


if __name__ == "__main__":
    unittest.main()
