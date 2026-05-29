import hashlib
import json
from typing import Any

import httpx
import structlog

from config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

_EMBED_TTL = 60 * 60 * 24 * 7  # 7 days in seconds


class EmbeddingServiceUnavailable(Exception):
    """Raised when the Ollama embedding service cannot be reached."""


# ---------------------------------------------------------------------------
# Redis cache helpers
# ---------------------------------------------------------------------------

def _cache_key(text: str) -> str:
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"emb:{digest}"


async def _redis_get(key: str) -> list[float] | None:
    try:
        import redis.asyncio as aioredis  # type: ignore
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        raw = await r.get(key)
        await r.aclose()
        if raw:
            return json.loads(raw)
    except Exception as exc:
        log.warning("redis_cache_get_failed", key=key, error=str(exc))
    return None


async def _redis_set(key: str, vector: list[float]) -> None:
    try:
        import redis.asyncio as aioredis  # type: ignore
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.set(key, json.dumps(vector), ex=_EMBED_TTL)
        await r.aclose()
    except Exception as exc:
        log.warning("redis_cache_set_failed", key=key, error=str(exc))


# ---------------------------------------------------------------------------
# Core embedding call
# ---------------------------------------------------------------------------

async def _call_ollama_embed(text: str) -> list[float]:
    """
    Call Ollama /api/embeddings endpoint directly via httpx.
    Always uses nomic-embed-text regardless of LLM_PROVIDER.
    """
    url = f"{settings.embedding_base_url.rstrip('/')}/api/embeddings"
    payload: dict[str, Any] = {
        "model": settings.embedding_model,
        "prompt": text,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["embedding"]
    except httpx.ConnectError as exc:
        raise EmbeddingServiceUnavailable(
            f"Cannot reach Ollama embedding service at {settings.embedding_base_url}. "
            "Ensure Ollama is running with 'nomic-embed-text' pulled."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise EmbeddingServiceUnavailable(
            f"Ollama embedding request failed: {exc.response.status_code} — {exc.response.text}"
        ) from exc
    except KeyError as exc:
        raise EmbeddingServiceUnavailable(
            "Ollama returned an unexpected response format (missing 'embedding' key)."
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def embed_text(text: str) -> list[float]:
    """
    Embed a single string using Ollama nomic-embed-text.
    Results are cached in Redis for 7 days.
    """
    text = text.strip()
    if not text:
        raise ValueError("Cannot embed empty text.")

    key = _cache_key(text)
    cached = await _redis_get(key)
    if cached is not None:
        log.debug("embedding_cache_hit", key=key)
        return cached

    vector = await _call_ollama_embed(text)
    await _redis_set(key, vector)
    log.debug("embedding_computed", key=key, dims=len(vector))
    return vector


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of strings, using cache where possible.
    Logs progress every 10 texts.
    """
    if not texts:
        return []

    results: list[list[float] | None] = [None] * len(texts)
    miss_indices: list[int] = []

    # Phase 1: populate from cache
    for i, text in enumerate(texts):
        key = _cache_key(text.strip())
        cached = await _redis_get(key)
        if cached is not None:
            results[i] = cached
        else:
            miss_indices.append(i)

    log.info(
        "embedding_batch_start",
        total=len(texts),
        cache_hits=len(texts) - len(miss_indices),
        cache_misses=len(miss_indices),
    )

    # Phase 2: embed cache misses
    for progress, i in enumerate(miss_indices, start=1):
        text = texts[i].strip()
        try:
            vector = await _call_ollama_embed(text)
            await _redis_set(_cache_key(text), vector)
            results[i] = vector
        except EmbeddingServiceUnavailable:
            raise
        except Exception as exc:
            log.error("embedding_single_failed", index=i, error=str(exc))
            raise

        if progress % 10 == 0 or progress == len(miss_indices):
            log.info(
                "embedding_batch_progress",
                done=progress,
                total=len(miss_indices),
            )

    # All slots should be filled now
    final: list[list[float]] = []
    for i, v in enumerate(results):
        if v is None:
            raise RuntimeError(f"Embedding missing for index {i} — this should not happen.")
        final.append(v)

    log.info("embedding_batch_complete", count=len(final))
    return final