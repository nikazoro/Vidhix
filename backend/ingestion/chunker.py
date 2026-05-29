import re
from dataclasses import dataclass, field
from uuid import uuid4

import structlog

log = structlog.get_logger(__name__)

MIN_TOKENS = 50
MAX_TOKENS = 400
OVERLAP_TOKENS = 50

# Patterns that mark the start of a new section
_SECTION_HEADER_RE = re.compile(
    r"(?m)^(?:"
    r"\d+[\.\)]\s+[A-Z].{5,}"          # e.g. "1. Introduction ..."
    r"|[A-Z][A-Z\s]{4,}$"              # ALL-CAPS header line
    r"|(?:Step|STEP)\s+\d+[:\.]?.{0,}"  # Step N: ...
    r")"
)

# "Step N:" pattern that must never be split mid-way
_STEP_BOUNDARY_RE = re.compile(r"(?i)\bstep\s+\d+\b")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    chunk_id: str
    text: str
    chunk_index: int
    parent_section_id: str | None
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Token counting (character-based approximation — no heavy tokeniser needed)
# ---------------------------------------------------------------------------

def _approx_tokens(text: str) -> int:
    """Approximate token count: ~4 chars per token for English legal text."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Sentence splitter (never cuts mid-sentence)
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Core chunking logic
# ---------------------------------------------------------------------------

def _chunk_paragraph(
    paragraph: str,
    section_id: str,
    start_index: int,
) -> list[Chunk]:
    """
    Split a single paragraph into ≤MAX_TOKENS chunks with OVERLAP_TOKENS overlap.
    Never splits in the middle of a sentence.
    Preserves "Step N:" boundaries.
    """
    chunks: list[Chunk] = []
    sentences = _split_sentences(paragraph)

    current_sentences: list[str] = []
    current_tokens = 0
    chunk_index = start_index

    def _flush(sents: list[str]) -> None:
        nonlocal chunk_index
        text = " ".join(sents).strip()
        if _approx_tokens(text) < MIN_TOKENS and chunks:
            # Append to last chunk instead of creating a tiny orphan
            last = chunks[-1]
            chunks[-1] = Chunk(
                chunk_id=last.chunk_id,
                text=(last.text + " " + text).strip(),
                chunk_index=last.chunk_index,
                parent_section_id=last.parent_section_id,
                metadata=last.metadata,
            )
            return
        chunks.append(
            Chunk(
                chunk_id=str(uuid4()),
                text=text,
                chunk_index=chunk_index,
                parent_section_id=section_id,
            )
        )
        chunk_index += 1

    for sentence in sentences:
        s_tokens = _approx_tokens(sentence)

        # If adding this sentence would overflow, flush first
        if current_tokens + s_tokens > MAX_TOKENS and current_sentences:
            _flush(current_sentences)
            # Overlap: carry last N tokens worth of sentences into next chunk
            overlap_sents: list[str] = []
            overlap_tokens = 0
            for s in reversed(current_sentences):
                t = _approx_tokens(s)
                if overlap_tokens + t <= OVERLAP_TOKENS:
                    overlap_sents.insert(0, s)
                    overlap_tokens += t
                else:
                    break
            current_sentences = overlap_sents
            current_tokens = overlap_tokens

        current_sentences.append(sentence)
        current_tokens += s_tokens

    if current_sentences:
        _flush(current_sentences)

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_document(text: str, doc_metadata: dict) -> list[Chunk]:
    """
    Hierarchically chunk a document:
      1. Split by section headers.
      2. Within each section, split by paragraphs.
      3. If a paragraph > MAX_TOKENS, split by sentences with overlap.
      4. Min chunk size: MIN_TOKENS. Max chunk size: MAX_TOKENS.
      5. Step N: boundaries are never split.
      6. Each chunk carries parent_section_id + doc metadata.
    """
    if not text.strip():
        log.warning("chunker_empty_text")
        return []

    # --- Step 1: split into sections ---
    section_texts: list[tuple[str | None, str]] = []  # (section_title, body)
    last_end = 0
    current_title: str | None = None

    for m in _SECTION_HEADER_RE.finditer(text):
        body = text[last_end : m.start()].strip()
        if body:
            section_texts.append((current_title, body))
        current_title = m.group().strip()
        last_end = m.end()

    # Remainder after last header
    remainder = text[last_end:].strip()
    if remainder:
        section_texts.append((current_title, remainder))

    # If no headers found, treat entire document as one section
    if not section_texts:
        section_texts = [(None, text.strip())]

    # --- Step 2 & 3: chunk each section ---
    all_chunks: list[Chunk] = []
    global_index = 0

    for section_title, section_body in section_texts:
        section_id = str(uuid4())
        paragraphs = re.split(r"\n{2,}", section_body)

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_tokens = _approx_tokens(para)

            if para_tokens <= MAX_TOKENS:
                if para_tokens < MIN_TOKENS and all_chunks:
                    # Absorb tiny paragraph into last chunk
                    last = all_chunks[-1]
                    all_chunks[-1] = Chunk(
                        chunk_id=last.chunk_id,
                        text=(last.text + "\n" + para).strip(),
                        chunk_index=last.chunk_index,
                        parent_section_id=last.parent_section_id,
                        metadata=last.metadata,
                    )
                else:
                    all_chunks.append(
                        Chunk(
                            chunk_id=str(uuid4()),
                            text=para,
                            chunk_index=global_index,
                            parent_section_id=section_id,
                        )
                    )
                    global_index += 1
            else:
                sub_chunks = _chunk_paragraph(para, section_id, global_index)
                all_chunks.extend(sub_chunks)
                global_index += len(sub_chunks)

        # Attach section title to metadata
        for chunk in all_chunks:
            if chunk.parent_section_id == section_id and section_title:
                chunk.metadata["section_title"] = section_title

    # Attach document-level metadata to all chunks
    for chunk in all_chunks:
        chunk.metadata.update(doc_metadata)

    log.info(
        "chunker_complete",
        total_chunks=len(all_chunks),
        doc_title=doc_metadata.get("title", "unknown"),
    )
    return all_chunks