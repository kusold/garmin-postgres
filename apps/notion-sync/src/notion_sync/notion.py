import logging
import time
from typing import Any, Callable

import notion_client.errors

logger = logging.getLogger(__name__)


class NotionSink:
    """Writes rows to a Notion database with client-side rate limiting and retry.

    Notion limits integrations to roughly 3 requests/second and returns HTTP 429
    (and occasionally 5xx) under load. The underlying ``notion-client`` library
    only retries 5xx for idempotent methods (GET/DELETE), so ``pages.create``
    and ``pages.update`` would otherwise propagate on the first transient error.
    We add pacing + retry/backoff here to stay safely under the limit and to
    absorb 429/5xx responses for all calls.
    """

    def __init__(
        self,
        client: Any,
        *,
        dry_run: bool = False,
        min_interval: float = 0.34,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.dry_run = dry_run
        # Minimum seconds between successive Notion API calls (pacing).
        self.min_interval = min_interval
        # Maximum number of retry attempts for a retryable (429/5xx) response.
        self.max_retries = max_retries
        # Injectable timing primitives so tests can avoid real delays.
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call_at: float | None = None

    def _invoke(self, fn: Callable[..., Any], /, **kwargs: Any) -> Any:
        """Call ``fn(**kwargs)`` with pacing and retry/backoff.

        Pacing: sleeps the remainder of ``min_interval`` since the previous call
        so we stay under Notion's ~3 req/s limit.
        Retry: on a retryable Notion error (HTTP 429 or any 5xx) retries up to
        ``max_retries`` times. On 429 a ``Retry-After`` header (seconds) is
        honored when present; otherwise exponential backoff
        ``base * (2 ** attempt)`` capped at 60s is used. Non-retryable errors
        (e.g. 4xx other than 429) propagate immediately.
        """
        # --- Pacing -------------------------------------------------------
        if self.min_interval > 0 and self._last_call_at is not None:
            elapsed = self._monotonic() - self._last_call_at
            remaining = self.min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)

        # --- Retry loop ---------------------------------------------------
        base_delay = 1.0
        max_delay = 60.0
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._last_call_at = self._monotonic()
            try:
                return fn(**kwargs)
            except (notion_client.errors.APIResponseError,
                    notion_client.errors.UnknownHTTPResponseError) as error:
                last_error = error
                status = getattr(error, "status", None)
                retryable = status == 429 or (
                    isinstance(status, int) and status >= 500
                )
                if not retryable or attempt >= self.max_retries:
                    raise

                # Determine the delay before the next attempt.
                delay: float | None = None
                headers = getattr(error, "headers", None)
                if headers is not None:
                    retry_after = headers.get("Retry-After") or headers.get("retry-after")
                    if retry_after is not None:
                        try:
                            delay = float(retry_after)
                        except (TypeError, ValueError):
                            delay = None
                if delay is None:
                    delay = min(base_delay * (2 ** attempt), max_delay)

                logger.warning(
                    "Notion API call failed (status=%s); retrying in %.2fs "
                    "(attempt %d/%d)",
                    status,
                    delay,
                    attempt + 1,
                    self.max_retries,
                )
                self._sleep(delay)

        # Should be unreachable, but propagate the original error if we get here.
        assert last_error is not None
        raise last_error

    def upsert_page(
        self,
        database_id: str,
        *,
        filter_payload: dict,
        properties: dict,
        icon: dict | None = None,
        cover: dict | None = None,
    ) -> str:
        if self.dry_run:
            return "dry_run"

        existing = self._invoke(
            self.client.databases.query,
            database_id=database_id,
            filter=filter_payload,
        )["results"]

        if existing:
            update_payload: dict[str, Any] = {
                "page_id": existing[0]["id"],
                "properties": properties,
            }
            if icon:
                update_payload["icon"] = icon
            if cover:
                update_payload["cover"] = cover
            self._invoke(self.client.pages.update, **update_payload)
            return "updated"

        create_payload: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        if icon:
            create_payload["icon"] = icon
        if cover:
            create_payload["cover"] = cover
        self._invoke(self.client.pages.create, **create_payload)
        return "created"
