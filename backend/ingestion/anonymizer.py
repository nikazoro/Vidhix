import re

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for PII not caught by spaCy NER
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(
    r"\b(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b"
)
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
_SSN_RE = re.compile(
    r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"
)
_CASE_NUMBER_RE = re.compile(
    r"\b(?:Case\s+No\.?|Docket\s+No\.?|File\s+No\.?)\s*[:#]?\s*[A-Z0-9\-\/]+\b",
    re.IGNORECASE,
)

# spaCy labels → replacement tokens
_LABEL_MAP = {
    "PERSON": "[PERSON]",
    "ORG": "[ORGANIZATION]",
    "GPE": "[LOCATION]",
    "LOC": "[LOCATION]",
    "FAC": "[LOCATION]",
    # DATE and MONEY are intentionally kept — they are legally material
}

_nlp = None  # lazy-loaded


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy  # type: ignore
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            log.error(
                "spacy_model_not_found",
                hint="Run: python -m spacy download en_core_web_sm",
            )
            raise
    return _nlp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def anonymize_text(text: str) -> str:
    """
    Detect and replace PII in text using spaCy NER + regex patterns.

    Replaced:
        PERSON      → [PERSON]
        ORG         → [ORGANIZATION]
        GPE/LOC/FAC → [LOCATION]
        Phone       → [PHONE_REDACTED]
        Email       → [EMAIL_REDACTED]
        SSN         → [ID_REDACTED]
        Case/Docket → [CASE_REF]

    Preserved (legally material):
        DATE, MONEY, CARDINAL (numbers), percentages

    This operation is irreversible.
    """
    if not text.strip():
        return text

    nlp = _get_nlp()
    doc = nlp(text)

    # Build replacements in reverse order so character offsets stay valid
    replacements: list[tuple[int, int, str]] = []

    for ent in doc.ents:
        replacement = _LABEL_MAP.get(ent.label_)
        if replacement:
            replacements.append((ent.start_char, ent.end_char, replacement))

    # Apply NER replacements (reversed to preserve offsets)
    result = text
    for start, end, token in sorted(replacements, key=lambda x: x[0], reverse=True):
        result = result[:start] + token + result[end:]

    # Apply regex-based patterns on the NER-cleaned text
    result = _CASE_NUMBER_RE.sub("[CASE_REF]", result)
    result = _SSN_RE.sub("[ID_REDACTED]", result)
    result = _EMAIL_RE.sub("[EMAIL_REDACTED]", result)
    result = _PHONE_RE.sub("[PHONE_REDACTED]", result)

    ner_count = len(replacements)
    regex_hits = (
        len(_PHONE_RE.findall(text))
        + len(_EMAIL_RE.findall(text))
        + len(_SSN_RE.findall(text))
        + len(_CASE_NUMBER_RE.findall(text))
    )
    log.info(
        "anonymize_complete",
        ner_replacements=ner_count,
        regex_replacements=regex_hits,
        original_len=len(text),
        result_len=len(result),
    )
    return result