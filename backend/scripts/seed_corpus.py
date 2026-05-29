"""
Ingest seed legal documents from backend/data/corpus/ into Qdrant and BM25.

Usage:
    python -m scripts.seed_corpus
"""
import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import structlog
from sqlalchemy import select

from config import get_settings
from database import AsyncSessionLocal, init_db
from ingestion.chunker import chunk_document
from ingestion.embedder import embed_and_store_chunks
from models.corpus_document import CorpusDocument
from storage.bm25_index import get_bm25_index
from storage.qdrant_client import ensure_collections_exist

log = structlog.get_logger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Metadata for each seed document
# ---------------------------------------------------------------------------

SEED_METADATA: dict[str, dict] = {
    "ca_tenant_rights.txt": {
        "title": "California Tenant Rights Guide",
        "jurisdiction": "CA",
        "legal_domain": "tenant-rights",
        "document_type": "legal_guide",
        "source_authority": "California Courts Self-Help Center",
        "source_url": "https://selfhelp.courts.ca.gov/landlord-tenant",
    },
    "ca_small_claims_guide.txt": {
        "title": "California Small Claims Court Guide",
        "jurisdiction": "CA",
        "legal_domain": "small-claims",
        "document_type": "legal_guide",
        "source_authority": "California Courts Self-Help Center",
        "source_url": "https://selfhelp.courts.ca.gov/small-claims",
    },
    "federal_wage_hour_guide.txt": {
        "title": "Federal Wage and Hour Rights Guide (FLSA)",
        "jurisdiction": "federal",
        "legal_domain": "employment",
        "document_type": "legal_guide",
        "source_authority": "U.S. Department of Labor — Wage and Hour Division",
        "source_url": "https://www.dol.gov/agencies/whd/flsa",
    },
}


async def seed_document(filename: str, text: str, meta: dict) -> None:
    checksum = hashlib.sha256(text.encode()).hexdigest()

    async with AsyncSessionLocal() as session:
        # Skip if already ingested with same checksum
        result = await session.execute(
            select(CorpusDocument).where(CorpusDocument.checksum == checksum)
        )
        existing = result.scalar_one_or_none()
        if existing:
            print(f"   ⏭  Skipping {filename} — already ingested (checksum match).")
            return

        corpus_doc = CorpusDocument(
            title=meta["title"],
            jurisdiction=meta["jurisdiction"],
            legal_domain=meta["legal_domain"],
            document_type=meta.get("document_type"),
            source_url=meta.get("source_url"),
            source_authority=meta.get("source_authority"),
            checksum=checksum,
            ingestion_status="processing",
        )
        session.add(corpus_doc)
        await session.flush()
        await session.refresh(corpus_doc)
        doc_id = corpus_doc.id

        doc_metadata = {
            "title": meta["title"],
            "jurisdiction": meta["jurisdiction"],
            "legal_domain": meta["legal_domain"],
            "document_type": meta.get("document_type", ""),
            "source_authority": meta.get("source_authority", ""),
            "procedure_id": None,
            "procedure_phase": None,
            "step_number": None,
        }

        # Chunk
        chunks = chunk_document(text, doc_metadata)
        print(f"   📄 {filename}: {len(chunks)} chunks")

        # Embed + upsert Qdrant
        count = await embed_and_store_chunks(
            chunks=chunks,
            corpus_document_id=doc_id,
            doc_metadata=doc_metadata,
        )

        # Update BM25
        bm25 = get_bm25_index()
        bm25.add_documents([{"chunk_id": c.chunk_id, "text": c.text} for c in chunks])

        corpus_doc.ingestion_status = "completed"
        corpus_doc.chunk_count = count
        await session.commit()
        print(f"   ✅ {filename}: ingested {count} chunks.")


async def main() -> None:
    print("LexAra — Seeding corpus documents...\n")

    await init_db()
    await ensure_collections_exist()

    corpus_path = settings.corpus_path
    if not corpus_path.exists():
        print(f"❌ Corpus directory not found: {corpus_path}")
        sys.exit(1)

    txt_files = list(corpus_path.glob("*.txt"))
    if not txt_files:
        print("⚠️  No .txt files found in corpus directory.")
        return

    for txt_file in sorted(txt_files):
        filename = txt_file.name
        meta = SEED_METADATA.get(filename)
        if not meta:
            print(f"   ⚠️  No metadata for {filename} — skipping.")
            continue

        print(f"\n🔄 Processing: {filename}")
        text = txt_file.read_text(encoding="utf-8", errors="replace")
        await seed_document(filename, text, meta)

    print("\n✅ Corpus seeding complete.")


if __name__ == "__main__":
    asyncio.run(main())