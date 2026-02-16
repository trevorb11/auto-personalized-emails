"""
HTTP retry helper for transient failures.

Wraps httpx async requests with exponential backoff on:
  - 429 Too Many Requests (rate limited)
  - 500/502/503/504 (server errors)
  - Connection errors and timeouts

Usage:
    from tools.http_retry import fetch

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await fetch(client, "GET", url, params={...})
"""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds — doubles each attempt (1s, 2s, 4s)


async def fetch(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    max_retries: int = _MAX_RETRIES,
    **kwargs,
) -> httpx.Response:
    """Make an HTTP request with automatic retry on transient failures."""
    last_exc = None
    resp = None

    for attempt in range(max_retries + 1):
        try:
            resp = await client.request(method, url, **kwargs)

            if resp.status_code not in _RETRYABLE_STATUS or attempt == max_retries:
                return resp

            delay = _BASE_DELAY * (2 ** attempt)
            logger.warning(
                "%s %s returned %d (attempt %d/%d) — retrying in %.0fs",
                method, url[:80], resp.status_code,
                attempt + 1, max_retries + 1, delay,
            )
            await asyncio.sleep(delay)

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                httpx.PoolTimeout, httpx.ConnectTimeout) as exc:
            last_exc = exc
            if attempt == max_retries:
                raise
            delay = _BASE_DELAY * (2 ** attempt)
            logger.warning(
                "%s %s failed (attempt %d/%d): %s — retrying in %.0fs",
                method, url[:80], attempt + 1, max_retries + 1, exc, delay,
            )
            await asyncio.sleep(delay)

    return resp  # type: ignore[return-value]
