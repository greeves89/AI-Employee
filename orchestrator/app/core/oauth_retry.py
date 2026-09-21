"""Bounded retries only before an OAuth refresh request could be sent."""
import asyncio
import logging
import random

import httpx

logger = logging.getLogger(__name__)


async def post_refresh_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """One attempt + two retries; keep the client's per-attempt timeout intact.

    Never retry read/write errors or HTTP responses: the provider may already
    have rotated the refresh token. Cancellation also propagates immediately.
    Log neither endpoint URLs nor request data, which may carry credentials.
    """
    for attempt in range(3):
        try:
            return await client.post(url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if attempt == 2:
                raise
            delay = 0.5 * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning(
                "[OAuth refresh] connection attempt %s/3 failed (%s); retrying in %.2fs",
                attempt + 1, type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)
