import structlog
from fastapi import APIRouter
from sqlalchemy import text

from config import get_settings
from database import AsyncSessionLocal

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/health", tags=["health"])
settings = get_settings()

_VERSION = "1.0.0"


@router.get(
    "",
    status_code=200,
    summary="Health check",
    description="Returns status of all dependent services.",
)
async def health_check() -> dict:
    results: dict[str, str] = {}

    # PostgreSQL
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        results["db"] = "ok"
    except Exception as exc:
        log.error("health_db_failed", error=str(exc))
        results["db"] = f"error: {exc}"

    # Qdrant
    try:
        from storage.qdrant_client import get_qdrant_client
        client = get_qdrant_client()
        await client.get_collections()
        results["qdrant"] = "ok"
    except Exception as exc:
        log.error("health_qdrant_failed", error=str(exc))
        results["qdrant"] = f"error: {exc}"

    # Ollama
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(f"{settings.ollama_base_url}/api/tags")
        results["ollama"] = "ok" if resp.status_code == 200 else f"http {resp.status_code}"
    except Exception as exc:
        log.error("health_ollama_failed", error=str(exc))
        results["ollama"] = f"error: {exc}"

    # Redis
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url)
        
        import inspect
        ping_result = r.ping()
        if inspect.isawaitable(ping_result):
            await ping_result
            
        await r.aclose()
        results["redis"] = "ok"
    except Exception as exc:
        log.error("health_redis_failed", error=str(exc))
        results["redis"] = f"error: {exc}"

    overall = "ok" if all(v == "ok" for v in results.values()) else "degraded"

    return {
        "status": overall,
        "version": _VERSION,
        "env": settings.app_env,
        **results,
    }