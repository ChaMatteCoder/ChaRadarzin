from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from django.core.cache import cache


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


def consume_rate_limit(
    bucket: str,
    identity: str,
    *,
    limit: int,
    window_seconds: int,
    now: float | None = None,
) -> RateLimitResult:
    if limit < 1 or window_seconds < 1:
        raise ValueError("limit e window_seconds devem ser positivos")

    current = time.time() if now is None else now
    window = int(current // window_seconds)
    retry_after = max(1, window_seconds - int(current % window_seconds))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    key = f"chadaradzin:rate:{bucket}:{window}:{digest}"
    timeout = window_seconds + 1

    if cache.add(key, 1, timeout=timeout):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            # A chave pode expirar entre add/incr. Tente recriar uma vez.
            if cache.add(key, 1, timeout=timeout):
                count = 1
            else:
                count = cache.incr(key)

    return RateLimitResult(
        allowed=count <= limit,
        limit=limit,
        remaining=max(0, limit - count),
        retry_after_seconds=retry_after,
    )
