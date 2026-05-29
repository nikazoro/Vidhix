import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for legal entity extraction
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\b"
    r"|\b\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2}\b",
    re.IGNORECASE,
)

_MONEY_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d{2})?"
    r"|\b[\d,]+(?:\.\d{2})?\s*(?:dollars?|USD)\b",
    re.IGNORECASE,
)

_DEADLINE_RE = re.compile(
    r"\b(\d+)[- ](?:calendar\s+)?days?\b"
    r"|\bwithin\s+(\d+)\s+days?\b"
    r"|\b(\d+)[- ]day\s+(?:notice|period|deadline|response)\b",
    re.IGNORECASE,
)

_STATUTE_RE = re.compile(
    r"\b(?:Cal\.?\s*)?(?:Civ\.?|Lab\.?|Bus\.?\s*&?\s*Prof\.?|Gov\.?|Pen\.?)"
    r"\s*Code\s*[§Ss]?\s*\d[\d\.\-]*\b"
    r"|\b\d+\s+U\.S\.C\.?\s*[§Ss]?\s*\d+\b"
    r"|\bCFR\s*[§Ss]?\s*\d+\.\d+\b",
    re.IGNORECASE,
)

_COURT_RE = re.compile(
    r"\b(?:Superior|District|Municipal|Appellate|Supreme|Small Claims)\s+Court"
    r"(?:\s+of\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)?\b",
    re.IGNORECASE,
)

_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy  # type: ignore
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            log.error("spacy_model_not_found", hint="python -m spacy download en_core_web_sm")
            raise
    return _nlp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_entities(text: str) -> dict[str, Any]:
    """
    Extract structured legal entities from document text.

    Returns:
        {
            "parties": list[str],
            "dates": list[str],
            "amounts": list[str],
            "deadlines": list[str],
            "statutes": list[str],
            "courts": list[str],
            "organizations": list[str],
        }
    """
    nlp = _get_nlp()
    doc = nlp(text[:100_000])  # cap for performance

    # spaCy NER
    parties: list[str] = []
    organizations: list[str] = []
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            val = ent.text.strip()
            if val and val not in parties:
                parties.append(val)
        elif ent.label_ == "ORG":
            val = ent.text.strip()
            if val and val not in organizations:
                organizations.append(val)

    # Regex extractions
    dates = list(dict.fromkeys(m.group().strip() for m in _DATE_RE.finditer(text)))
    amounts = list(dict.fromkeys(m.group().strip() for m in _MONEY_RE.finditer(text)))
    statutes = list(dict.fromkeys(m.group().strip() for m in _STATUTE_RE.finditer(text)))
    courts = list(dict.fromkeys(m.group().strip() for m in _COURT_RE.finditer(text)))

    deadline_matches: list[str] = []
    for m in _DEADLINE_RE.finditer(text):
        days = m.group(1) or m.group(2) or m.group(3)
        context = text[max(0, m.start() - 30): m.end() + 30].strip()
        label = f"{days} days — context: ...{context}..."
        deadline_matches.append(label)
    deadlines = list(dict.fromkeys(deadline_matches))

    result = {
        "parties": parties[:20],
        "dates": dates[:20],
        "amounts": amounts[:20],
        "deadlines": deadlines[:10],
        "statutes": statutes[:20],
        "courts": courts[:10],
        "organizations": organizations[:20],
    }

    log.info(
        "entity_extraction_complete",
        parties=len(parties),
        dates=len(dates),
        amounts=len(amounts),
        statutes=len(statutes),
    )
    return result