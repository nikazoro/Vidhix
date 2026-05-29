import time
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from langchain_core.messages import BaseMessage
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Langfuse tracer (optional)
# ---------------------------------------------------------------------------

def _get_langfuse_callback() -> Any | None:
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse.callback import CallbackHandler  # type: ignore
        return CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as exc:
        log.warning("langfuse_init_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def _build_ollama_llm() -> Any:
    from langchain_ollama import ChatOllama  # type: ignore
    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        temperature=0.1,
        top_p=0.9,
    )


def _build_gemini_llm() -> Any:
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.1,
        convert_system_message_to_human=True,
    )


def get_llm() -> Any:
    """Return the correct LangChain chat model based on LLM_PROVIDER env var."""
    if settings.llm_provider == "gemini":
        log.debug("llm_provider_selected", provider="gemini", model=settings.gemini_model)
        return _build_gemini_llm()
    log.debug("llm_provider_selected", provider="ollama", model=settings.ollama_model)
    return _build_ollama_llm()


# ---------------------------------------------------------------------------
# Retry decorator for transient LLM failures
# ---------------------------------------------------------------------------

_RETRYABLE = (Exception,)  # narrow this per-provider if desired


def _llm_retry():
    return retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )


# ---------------------------------------------------------------------------
# Instrumented invoke / stream helpers
# ---------------------------------------------------------------------------

@_llm_retry()
async def invoke_llm(
    messages: list[BaseMessage],
    *,
    trace_name: str = "llm_invoke",
    metadata: dict[str, Any] | None = None,
) -> str:
    """
    Invoke the LLM and return the response text.
    Wraps with Langfuse tracing and structlog timing.
    """
    llm = get_llm()
    callbacks = []
    lf = _get_langfuse_callback()
    if lf:
        callbacks.append(lf)

    t0 = time.monotonic()
    try:
        response = await llm.ainvoke(messages, config={"callbacks": callbacks} if callbacks else {})
        latency_ms = (time.monotonic() - t0) * 1000
        tokens = getattr(response, "usage_metadata", {}) or {}
        log.info(
            "llm_invoke_success",
            trace_name=trace_name,
            model=settings.llm_model_name,
            latency_ms=round(latency_ms, 1),
            tokens=tokens,
            **(metadata or {}),
        )
        return response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        log.error(
            "llm_invoke_error",
            trace_name=trace_name,
            model=settings.llm_model_name,
            latency_ms=round(latency_ms, 1),
            error=str(exc),
            **(metadata or {}),
        )
        raise


async def stream_llm(
    messages: list[BaseMessage],
    *,
    trace_name: str = "llm_stream",
    metadata: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream LLM response tokens one by one.
    Yields individual string chunks as they arrive.
    """
    llm = get_llm()
    callbacks = []
    lf = _get_langfuse_callback()
    if lf:
        callbacks.append(lf)

    t0 = time.monotonic()
    total_tokens = 0
    try:
        async for chunk in llm.astream(
            messages, config={"callbacks": callbacks} if callbacks else {}
        ):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            if token:
                total_tokens += 1
                yield token
        latency_ms = (time.monotonic() - t0) * 1000
        log.info(
            "llm_stream_complete",
            trace_name=trace_name,
            model=settings.llm_model_name,
            latency_ms=round(latency_ms, 1),
            chunks_yielded=total_tokens,
            **(metadata or {}),
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        log.error(
            "llm_stream_error",
            trace_name=trace_name,
            model=settings.llm_model_name,
            latency_ms=round(latency_ms, 1),
            error=str(exc),
            **(metadata or {}),
        )
        raise