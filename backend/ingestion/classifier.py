import re

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Keyword maps for rule-based classification
# ---------------------------------------------------------------------------

_DOC_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("eviction_notice", ["unlawful detainer", "pay or quit", "3-day notice", "notice to vacate", "notice to quit"]),
    ("lease_agreement", ["lease agreement", "rental agreement", "month-to-month", "tenancy agreement"]),
    ("court_filing", ["plaintiff", "defendant", "complaint", "superior court", "district court", "cause of action"]),
    ("demand_letter", ["demand letter", "we hereby demand", "failure to comply", "legal action will"]),
    ("wage_statement", ["pay stub", "earnings statement", "gross wages", "net pay", "payroll"]),
    ("employment_contract", ["employment agreement", "at-will employment", "offer of employment", "terms of employment"]),
    ("small_claims_form", ["small claims", "sc-100", "claim of plaintiff", "defendant's claim"]),
    ("government_notice", ["department of labor", "labor commissioner", "eeoc", "nlrb", "notice of hearing"]),
    ("legal_guide", ["your rights", "know your rights", "legal guide", "tenant rights", "self-help"]),
    ("contract", ["agreement", "terms and conditions", "consideration", "whereas", "hereinafter"]),
]

_JURISDICTION_RULES: list[tuple[str, list[str]]] = [
    ("CA", ["california", "ca civil code", "california code", "superior court of california", "ca labor code"]),
    ("NY", ["new york", "nyc", "new york city", "ny civil rights"]),
    ("TX", ["texas", "tx", "texas property code"]),
    ("FL", ["florida", "fl statute"]),
    ("federal", ["federal", "u.s.c.", "united states code", "cfr", "federal register", "u.s. district"]),
]

_DOMAIN_RULES: list[tuple[str, list[str]]] = [
    ("tenant-rights", ["eviction", "landlord", "tenant", "rent", "lease", "habitability", "security deposit"]),
    ("employment", ["wage", "overtime", "employer", "employee", "wrongful termination", "discrimination", "fmla"]),
    ("small-claims", ["small claims", "sc-100", "claim amount", "filing fee", "service of process"]),
    ("consumer", ["debt collection", "fdcpa", "consumer protection", "warranty", "refund", "chargeback"]),
    ("immigration", ["visa", "green card", "undocumented", "deportation", "asylum", "uscis"]),
    ("family", ["divorce", "custody", "child support", "alimony", "domestic violence", "restraining order"]),
]


def _score_rules(text_lower: str, rules: list[tuple[str, list[str]]]) -> str | None:
    """Return the category with the highest keyword hit count, or None if no hits."""
    scores: dict[str, int] = {}
    for category, keywords in rules:
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > 0:
            scores[category] = count
    if not scores:
        return None
    return max(scores, key=lambda k: scores[k])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_document(text: str) -> dict[str, str | None]:
    """
    Rule-based document classification using keyword matching.

    Returns:
        {
            "document_type": str | None,
            "jurisdiction": str | None,
            "legal_domain": str | None,
        }
    """
    text_lower = text.lower()

    document_type = _score_rules(text_lower, _DOC_TYPE_RULES)
    jurisdiction = _score_rules(text_lower, _JURISDICTION_RULES)
    legal_domain = _score_rules(text_lower, _DOMAIN_RULES)

    log.info(
        "document_classified",
        document_type=document_type,
        jurisdiction=jurisdiction,
        legal_domain=legal_domain,
    )
    return {
        "document_type": document_type,
        "jurisdiction": jurisdiction,
        "legal_domain": legal_domain,
    }