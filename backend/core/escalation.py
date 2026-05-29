import json
from dataclasses import dataclass, field

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from config import get_settings
from core.llm_provider import invoke_llm

log = structlog.get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class EscalationNode:
    id: str
    title: str
    severity: str
    self_help_possible: bool
    triggers: list[str]
    escalation_message: str
    resources: list[str]


@dataclass
class EscalationResult:
    escalation_needed: bool
    severity: str          # LOW | MEDIUM | HIGH
    reason: str
    matched_nodes: list[EscalationNode] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    escalation_message: str = ""


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class EscalationDetector:
    def __init__(self) -> None:
        self._nodes: list[EscalationNode] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        graph_path = settings.escalation_graph_path
        if not graph_path.exists():
            log.warning("escalation_graph_not_found", path=str(graph_path))
            self._loaded = True
            return
        try:
            with open(graph_path, encoding="utf-8") as f:
                data = json.load(f)
            self._nodes = [
                EscalationNode(
                    id=n["id"],
                    title=n["title"],
                    severity=n["severity"],
                    self_help_possible=n.get("self_help_possible", True),
                    triggers=n.get("triggers", []),
                    escalation_message=n.get("escalation_message", ""),
                    resources=n.get("resources", []),
                )
                for n in data.get("nodes", [])
            ]
            log.info("escalation_graph_loaded", node_count=len(self._nodes))
        except Exception as exc:
            log.error("escalation_graph_load_failed", error=str(exc))
        self._loaded = True

    def _keyword_check(self, text: str) -> list[EscalationNode]:
        """Stage 1: fast keyword scan — runs in < 5ms."""
        text_lower = text.lower()
        matched: list[EscalationNode] = []
        for node in self._nodes:
            for trigger in node.triggers:
                if trigger in text_lower:
                    matched.append(node)
                    break
        return matched

    async def _llm_check(
        self,
        user_message: str,
        context_chunks: list[str],
        session_history: list[dict],
    ) -> dict:
        """Stage 2: LLM-based escalation detection for nuanced situations."""
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:200]}"
            for m in session_history[-4:]
        )
        context_sample = "\n---\n".join(context_chunks[:3])[:1500]

        prompt = f"""Analyze the following legal situation and determine if it involves any high-risk areas requiring an attorney.

CONVERSATION HISTORY:
{history_text}

CURRENT MESSAGE: {user_message}

RETRIEVED LEGAL CONTEXT:
{context_sample}

High-risk areas requiring attorney referral:
- Criminal charges or criminal history affecting the case
- Immigration consequences (deportation risk, visa issues)
- Child custody or child welfare agencies (CPS)
- Domestic violence or threats
- Bankruptcy or overwhelming debt
- Complex business disputes over $25,000
- Employment or housing discrimination claims
- Medical malpractice or serious personal injury

Return ONLY valid JSON:
{{
  "escalation_needed": true/false,
  "reason": "brief explanation of why or why not",
  "severity": "LOW" | "MEDIUM" | "HIGH",
  "matched_areas": ["area1", "area2"]
}}"""

        try:
            response = await invoke_llm(
                [
                    SystemMessage(content="You are a legal risk assessment system. Respond only in valid JSON."),
                    HumanMessage(content=prompt),
                ],
                trace_name="escalation_llm_check",
            )
            cleaned = response.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            return json.loads(cleaned)
        except Exception as exc:
            log.warning("escalation_llm_check_failed", error=str(exc))
            return {"escalation_needed": False, "reason": "check failed", "severity": "LOW", "matched_areas": []}

    async def check_escalation(
        self,
        user_message: str,
        context_chunks: list[str],
        session_history: list[dict],
    ) -> EscalationResult:
        """
        Two-stage escalation detection:
          Stage 1: keyword matching (fast)
          Stage 2: LLM check if Stage 1 finds nothing
        """
        self._ensure_loaded()

        # Build full text to scan (message + recent history)
        scan_text = user_message + " " + " ".join(
            m.get("content", "") for m in session_history[-4:]
        )

        # Stage 1 — keyword
        matched_nodes = self._keyword_check(scan_text)

        if matched_nodes:
            highest_severity = max(
                matched_nodes, key=lambda n: {"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get(n.severity, 0)
            )
            all_resources = list(
                dict.fromkeys(r for node in matched_nodes for r in node.resources)
            )
            combined_message = "\n\n".join(
                f"**{node.title}**: {node.escalation_message}"
                for node in matched_nodes
            )
            log.info(
                "escalation_keyword_triggered",
                matched=[n.id for n in matched_nodes],
                severity=highest_severity.severity,
            )
            return EscalationResult(
                escalation_needed=True,
                severity=highest_severity.severity,
                reason=f"Keywords matched: {', '.join(n.title for n in matched_nodes)}",
                matched_nodes=matched_nodes,
                resources=all_resources,
                escalation_message=combined_message,
            )

        # Stage 2 — LLM (only if no keyword matches)
        llm_result = await self._llm_check(user_message, context_chunks, session_history)

        if not llm_result.get("escalation_needed", False):
            return EscalationResult(
                escalation_needed=False,
                severity="LOW",
                reason=llm_result.get("reason", "No high-risk factors detected."),
            )

        severity = llm_result.get("severity", "MEDIUM")
        reason = llm_result.get("reason", "")
        matched_areas = llm_result.get("matched_areas", [])

        # Try to find matching nodes for resources
        area_nodes = [
            n for n in self._nodes
            if any(area.lower() in n.title.lower() for area in matched_areas)
        ]
        all_resources = list(
            dict.fromkeys(r for node in area_nodes for r in node.resources)
        )
        if not all_resources:
            all_resources = ["Contact your local legal aid organization", "State bar lawyer referral service"]

        escalation_message = (
            f"This situation may involve {', '.join(matched_areas)}. "
            f"{reason} We strongly recommend consulting an attorney before proceeding."
        )

        log.info("escalation_llm_triggered", severity=severity, reason=reason)
        return EscalationResult(
            escalation_needed=True,
            severity=severity,
            reason=reason,
            matched_nodes=area_nodes,
            resources=all_resources,
            escalation_message=escalation_message,
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_detector: EscalationDetector | None = None


def get_escalation_detector() -> EscalationDetector:
    global _detector
    if _detector is None:
        _detector = EscalationDetector()
    return _detector