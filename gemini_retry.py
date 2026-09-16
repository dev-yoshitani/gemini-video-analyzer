"""Gemini APIの一時的な利用制限・混雑に対する共通リトライ処理。"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from typing import TypeVar


T = TypeVar("T")
DEFAULT_RETRY_DELAYS = (15, 30, 60, 120)


def is_retryable_gemini_error(error: BaseException) -> bool:
    message = str(error).lower().replace("_", "")
    markers = (
        "429",
        "resourceexhausted",
        "too many requests",
        "quota",
        "rate limit",
        "ratelimit",
        "503",
        "unavailable",
        "deadline exceeded",
        "deadlineexceeded",
        "temporarily unavailable",
    )
    return any(marker in message for marker in markers)


def _server_retry_delay(error: BaseException) -> int | None:
    """エラーメッセージ中の retry-after / retryDelay を秒へ変換する。"""
    message = str(error)
    patterns = (
        r"retry(?:Delay|[-_ ]after)?[\"':=\s]+(\d+(?:\.\d+)?)\s*s",
        r"retry in\s+(\d+(?:\.\d+)?)\s*s",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return max(1, min(300, int(float(match.group(1)) + 0.999)))
    return None


def wait_for_gemini_retry(
    error: BaseException,
    fallback_seconds: int,
    *,
    description: str,
    language: str = "ja",
) -> None:
    seconds = _server_retry_delay(error) or fallback_seconds
    if language == "en":
        print(
            f"\nGemini is rate-limited or busy. "
            f"Retrying {description} automatically in {seconds} seconds."
        )
    else:
        print(
            f"\nGeminiの利用制限または混雑を検出しました。"
            f"{seconds}秒後に{description}を自動再試行します。"
        )
    remaining = seconds
    from workflow_control import checkpoint, notify
    notify(kind="waiting", seconds=seconds)
    while remaining > 0:
        checkpoint()
        step = min(1, remaining)
        time.sleep(step)
        remaining -= step
        if remaining > 0 and (remaining % 30 == 0 or remaining <= 5):
            if language == "en":
                print(f"  About {remaining} seconds until retry...")
            else:
                print(f"  再試行まで残り約{remaining}秒...")


def call_with_gemini_retry(
    operation: Callable[[], T],
    *,
    description: str,
    delays: Sequence[int] = DEFAULT_RETRY_DELAYS,
    language: str = "ja",
) -> T:
    """一時的エラーだけを段階的な待機時間で再試行する。"""
    for attempt in range(len(delays) + 1):
        from workflow_control import checkpoint
        checkpoint()
        try:
            return operation()
        except Exception as exc:
            if not is_retryable_gemini_error(exc) or attempt >= len(delays):
                raise
            wait_for_gemini_retry(
                exc,
                int(delays[attempt]),
                description=description,
                language=language,
            )
    raise RuntimeError("Gemini APIの再試行に失敗しました。")
