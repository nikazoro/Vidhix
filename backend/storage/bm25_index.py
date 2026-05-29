import pickle
import re
from pathlib import Path
from typing import Any

import structlog
from rank_bm25 import BM25Okapi

from config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer, lowercased."""
    text = text.lower()
    tokens = re.findall(r"\b[a-z0-9]+\b", text)
    return tokens


class BM25Index:
    """
    In-memory BM25 index backed by rank_bm25.BM25Okapi.
    Persists to disk as a pickle file so it survives restarts.
    """

    def __init__(self) -> None:
        self._bm25: BM25Okapi | None = None
        self._chunk_ids: list[str] = []
        self._corpus: list[list[str]] = []
        self._index_path: Path = settings.bm25_index_path

    # ------------------------------------------------------------------
    # Build & persist
    # ------------------------------------------------------------------

    def build_index(self, documents: list[dict[str, Any]]) -> None:
        """
        Build the BM25 index from a list of document dicts.
        Each dict must have: {"chunk_id": str, "text": str}
        """
        if not documents:
            log.warning("bm25_build_empty_corpus")
            return

        self._chunk_ids = [d["chunk_id"] for d in documents]
        self._corpus = [_tokenize(d["text"]) for d in documents]
        self._bm25 = BM25Okapi(self._corpus)
        self._save()
        log.info("bm25_index_built", num_docs=len(documents))

    def _save(self) -> None:
        payload = {
            "bm25": self._bm25,
            "chunk_ids": self._chunk_ids,
            "corpus": self._corpus,
        }
        with open(self._index_path, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        log.info("bm25_index_saved", path=str(self._index_path))

    def _load(self) -> bool:
        if not self._index_path.exists():
            return False
        try:
            with open(self._index_path, "rb") as f:
                payload = pickle.load(f)
            self._bm25 = payload["bm25"]
            self._chunk_ids = payload["chunk_ids"]
            self._corpus = payload["corpus"]
            log.info(
                "bm25_index_loaded",
                path=str(self._index_path),
                num_docs=len(self._chunk_ids),
            )
            return True
        except Exception as exc:
            log.error("bm25_index_load_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Load or build
    # ------------------------------------------------------------------

    async def load_or_build(self) -> None:
        """
        Load from disk if available; otherwise build from PostgreSQL corpus.
        Called at application startup.
        """
        if self._load():
            return
        log.info("bm25_index_not_found_rebuilding")
        await self._build_from_db()

    async def _build_from_db(self) -> None:
        """Query all ingested corpus chunks from PostgreSQL and build the index."""
        try:
            from database import AsyncSessionLocal
            from sqlalchemy import select, text as sa_text

            async with AsyncSessionLocal() as session:
                # We store chunk text in Qdrant, not Postgres, so we do a best-effort
                # build using the extracted_text from corpus_documents. In production
                # the full rebuild task fetches chunk payloads from Qdrant.
                result = await session.execute(
                    sa_text(
                        "SELECT id::text, title FROM corpus_documents "
                        "WHERE ingestion_status = 'completed'"
                    )
                )
                rows = result.fetchall()

            if not rows:
                log.warning("bm25_build_no_corpus_documents")
                return

            documents = [{"chunk_id": row[0], "text": row[1]} for row in rows]
            self.build_index(documents)
        except Exception as exc:
            log.error("bm25_build_from_db_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 50) -> list[tuple[str, float]]:
        """
        Returns a list of (chunk_id, score) tuples, descending by BM25 score.
        Returns empty list if index is not built.
        """
        if self._bm25 is None or not self._chunk_ids:
            log.warning("bm25_search_on_empty_index")
            return []

        tokens = _tokenize(query)
        scores = self._bm25.get_scores(tokens)

        ranked = sorted(
            zip(self._chunk_ids, scores),
            key=lambda x: x[1],
            reverse=True,
        )
        return [(chunk_id, float(score)) for chunk_id, score in ranked[:top_k]]

    # ------------------------------------------------------------------
    # Incremental update
    # ------------------------------------------------------------------

    def add_documents(self, documents: list[dict[str, Any]]) -> None:
        """
        Add new documents and rebuild the index in-place.
        Deduplicates by chunk_id.
        """
        existing_ids = set(self._chunk_ids)
        new_docs = [d for d in documents if d["chunk_id"] not in existing_ids]
        if not new_docs:
            return

        combined = [
            {"chunk_id": cid, "text": " ".join(tokens)}
            for cid, tokens in zip(self._chunk_ids, self._corpus)
        ] + new_docs

        self.build_index(combined)
        log.info("bm25_index_incremental_update", added=len(new_docs))

    @property
    def doc_count(self) -> int:
        return len(self._chunk_ids)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bm25_index: BM25Index | None = None


def get_bm25_index() -> BM25Index:
    global _bm25_index
    if _bm25_index is None:
        _bm25_index = BM25Index()
    return _bm25_index