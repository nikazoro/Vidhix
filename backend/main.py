import time
from collections import defaultdict
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.cases import router as cases_router
from api.chat import router as chat_router
from api.documents import router as documents_router
from api.feedback import router as feedback_router
from api.health import router as health_router
from api.workflows import router as workflows_router
from config import get_settings
from database import init_db
from storage.bm25_index import get_bm25_index
from storage.qdrant_client import ensure_collections_exist

log = structlog.get_logger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Structlog configuration
# ---------------------------------------------------------------------------

# structlog.configure(
#     processors=[
#         structlog.stdlib.add_log_level,
#         structlog.stdlib.add_logger_name,
#         structlog.processors.TimeStamper(fmt="iso"),
#         structlog.processors.StackInfoRenderer(),
#         structlog.processors.format_exc_info,
#         structlog.processors.JSONRenderer() if settings.is_production
#         else structlog.dev.ConsoleRenderer(),
#     ],
# )
import logging

logging.basicConfig(
    format="%(message)s",
    level=logging.INFO,
)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        (
            structlog.processors.JSONRenderer()
            if settings.is_production
            else structlog.dev.ConsoleRenderer()
        ),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("lexara_starting", env=settings.app_env, llm_provider=settings.llm_provider)

    # Init PostgreSQL tables
    try:
        await init_db()
        log.info("database_ready")
    except Exception as exc:
        log.error("database_init_failed", error=str(exc))

    # Init Qdrant collections
    try:
        await ensure_collections_exist()
        log.info("qdrant_ready")
    except Exception as exc:
        log.error("qdrant_init_failed", error=str(exc))

    # Load/build BM25 index
    try:
        bm25 = get_bm25_index()
        await bm25.load_or_build()
        log.info("bm25_ready", doc_count=bm25.doc_count)
    except Exception as exc:
        log.error("bm25_init_failed", error=str(exc))

    log.info("lexara_started")
    yield
    log.info("lexara_shutting_down")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="LexAra API",
        description="AI-powered legal assistance backend",
        version="1.0.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    origins = ["*"] if not settings.is_production else [
        "https://lexara.yourdomain.com",
        "http://localhost:8501",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Request logging middleware
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        t0 = time.monotonic()
        response = await call_next(request)
        latency_ms = (time.monotonic() - t0) * 1000
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=round(latency_ms, 1),
            client=request.client.host if request.client else "unknown",
        )
        return response

    # ------------------------------------------------------------------
    # Rate limiting middleware (sliding window, 30 req/min per IP)
    # ------------------------------------------------------------------
    _rate_windows: dict[str, list[float]] = defaultdict(list)
    _RATE_LIMIT = 30
    _RATE_WINDOW = 60.0

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path.endswith("/health"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = _rate_windows[client_ip]

        # Remove timestamps older than 60 seconds
        _rate_windows[client_ip] = [t for t in window if now - t < _RATE_WINDOW]

        if len(_rate_windows[client_ip]) >= _RATE_LIMIT:
            log.warning("rate_limit_exceeded", client_ip=client_ip)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Maximum 30 requests per minute."},
            )

        _rate_windows[client_ip].append(now)
        return await call_next(request)

    # ------------------------------------------------------------------
    # Global exception handler
    # ------------------------------------------------------------------
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        log.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            exc_info=exc,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred. Please try again."},
        )

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    prefix = "/api/v1"
    app.include_router(health_router, prefix=prefix)
    app.include_router(chat_router, prefix=prefix)
    app.include_router(documents_router, prefix=prefix)
    app.include_router(cases_router, prefix=prefix)
    app.include_router(feedback_router, prefix=prefix)
    app.include_router(workflows_router, prefix=prefix)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=not settings.is_production,
        log_level="info",
    )