import unittest
from unittest import mock

import gemini_retry


class GeminiRetryTests(unittest.TestCase):
    def test_rate_limit_is_retried_and_then_succeeds(self):
        operation = mock.Mock(
            side_effect=[RuntimeError("429 RESOURCE_EXHAUSTED retry in 1s"), "ok"]
        )
        with mock.patch("gemini_retry.time.sleep") as sleep:
            result = gemini_retry.call_with_gemini_retry(
                operation,
                description="テスト",
                delays=(15,),
            )
        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_authentication_error_is_not_retried(self):
        operation = mock.Mock(side_effect=RuntimeError("401 invalid API key"))
        with mock.patch("gemini_retry.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "401"):
                gemini_retry.call_with_gemini_retry(
                    operation,
                    description="テスト",
                    delays=(1, 2),
                )
        operation.assert_called_once()
        sleep.assert_not_called()
