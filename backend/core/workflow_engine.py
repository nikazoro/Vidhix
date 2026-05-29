import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from config import get_settings
from core.llm_provider import invoke_llm

log = structlog.get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class StepDeadline:
    response_days: int | None
    deadline_type: str
    excludes_weekends: bool = False
    notes: str = ""


@dataclass
class Step:
    id: str
    step_number: int
    phase: str
    title: str
    description: str
    actions: list[str]
    deadlines: StepDeadline
    forms: list[str]
    agency: str
    self_help_possible: bool
    escalation_triggers: list[str]


@dataclass
class Edge:
    from_step: str | None
    to_step: str | None
    condition: str | None


@dataclass
class Procedure:
    id: str
    title: str
    jurisdiction: str
    legal_domain: str
    description: str
    steps: dict[str, Step]        # step_id → Step
    edges: list[Edge]
    steps_ordered: list[Step]     # sorted by step_number


@dataclass
class ProcedurePosition:
    procedure_id: str
    procedure_title: str
    current_step_id: str
    current_step_title: str
    current_phase: str
    completed_steps: list[str] = field(default_factory=list)
    declared_facts: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _parse_step(raw: dict) -> Step:
    dl = raw.get("deadlines", {})
    return Step(
        id=raw["id"],
        step_number=raw["step_number"],
        phase=raw["phase"],
        title=raw["title"],
        description=raw["description"],
        actions=raw.get("actions", []),
        deadlines=StepDeadline(
            response_days=dl.get("response_days"),
            deadline_type=dl.get("deadline_type", "none"),
            excludes_weekends=dl.get("excludes_weekends", False),
            notes=dl.get("notes", ""),
        ),
        forms=raw.get("forms", []),
        agency=raw.get("agency", ""),
        self_help_possible=raw.get("self_help_possible", True),
        escalation_triggers=raw.get("escalation_triggers", []),
    )


def _load_procedure_from_file(path: Path) -> Procedure:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    steps_list = [_parse_step(s) for s in raw["steps"]]
    steps_dict = {s.id: s for s in steps_list}
    steps_ordered = sorted(steps_list, key=lambda s: s.step_number)
    edges = [
        Edge(
            from_step=e.get("from"),
            to_step=e.get("to"),
            condition=e.get("condition"),
        )
        for e in raw.get("edges", [])
    ]
    return Procedure(
        id=raw["id"],
        title=raw["title"],
        jurisdiction=raw["jurisdiction"],
        legal_domain=raw["legal_domain"],
        description=raw["description"],
        steps=steps_dict,
        edges=edges,
        steps_ordered=steps_ordered,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class WorkflowEngine:
    def __init__(self) -> None:
        self._procedures: dict[str, Procedure] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        workflow_dir = settings.workflows_path
        if not workflow_dir.exists():
            log.warning("workflow_dir_not_found", path=str(workflow_dir))
            self._loaded = True
            return

        for json_file in workflow_dir.glob("*.json"):
            try:
                proc = _load_procedure_from_file(json_file)
                self._procedures[proc.id] = proc
                log.info("workflow_loaded", procedure_id=proc.id, steps=len(proc.steps))
            except Exception as exc:
                log.error("workflow_load_failed", file=str(json_file), error=str(exc))

        self._loaded = True

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def load_procedure(self, procedure_id: str) -> Procedure:
        self._ensure_loaded()
        if procedure_id not in self._procedures:
            raise KeyError(f"Procedure not found: {procedure_id!r}")
        return self._procedures[procedure_id]

    def list_procedures(self) -> list[dict]:
        self._ensure_loaded()
        return [
            {
                "id": p.id,
                "title": p.title,
                "jurisdiction": p.jurisdiction,
                "legal_domain": p.legal_domain,
                "description": p.description,
                "step_count": len(p.steps),
            }
            for p in self._procedures.values()
        ]

    def get_step(self, procedure_id: str, step_id: str) -> Step:
        proc = self.load_procedure(procedure_id)
        if step_id not in proc.steps:
            raise KeyError(f"Step {step_id!r} not found in procedure {procedure_id!r}")
        return proc.steps[step_id]

    def get_next_steps(
        self,
        procedure_id: str,
        step_id: str,
        condition: str | None = None,
    ) -> list[Step]:
        proc = self.load_procedure(procedure_id)
        next_step_ids: list[str] = []
        for edge in proc.edges:
            if edge.from_step == step_id:
                if condition is None or edge.condition is None or edge.condition == condition:
                    if edge.to_step is not None:
                        next_step_ids.append(edge.to_step)
        return [proc.steps[sid] for sid in next_step_ids if sid in proc.steps]

    def get_phase_steps(self, procedure_id: str, phase: str) -> list[Step]:
        proc = self.load_procedure(procedure_id)
        return [s for s in proc.steps_ordered if s.phase == phase]

    async def detect_procedure_position(
        self,
        user_message: str,
        session_history: list[dict],
    ) -> ProcedurePosition | None:
        """
        Use LLM with structured output to detect which procedure the user is in
        and which step they are currently at, based on their message and history.
        """
        self._ensure_loaded()
        if not self._procedures:
            return None

        procedure_list_str = "\n".join(
            f"- id={p.id}, title={p.title}, domain={p.legal_domain}, jurisdiction={p.jurisdiction}"
            for p in self._procedures.values()
        )

        # Build recent history context (last 6 turns)
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:300]}"
            for m in session_history[-6:]
        )

        prompt = f"""You are analyzing a conversation to detect if the user is navigating a specific legal procedure.

AVAILABLE PROCEDURES:
{procedure_list_str}

RECENT CONVERSATION:
{history_text}

CURRENT USER MESSAGE:
{user_message}

Analyze the conversation. If the user appears to be in one of the listed procedures, return JSON like:
{{
  "procedure_detected": true,
  "procedure_id": "<id from list above>",
  "current_step_id": "<best matching step id>",
  "completed_step_ids": ["<step_id>", ...],
  "declared_facts": {{"key": "value"}}
}}

If no procedure is detected, return:
{{"procedure_detected": false}}

Base your answer on phrases like: "I received a notice", "I already filed", "the hearing is next week", "I paid the rent but...", "I want to sue for", etc.
Return ONLY valid JSON, no explanation."""

        from langchain_core.messages import HumanMessage, SystemMessage
        try:
            response_text = await invoke_llm(
                [SystemMessage(content="You are a legal procedure position detector. Respond only in valid JSON."),
                 HumanMessage(content=prompt)],
                trace_name="detect_procedure_position",
            )
            # Strip markdown fences if present
            cleaned = response_text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(cleaned)
        except Exception as exc:
            log.warning("procedure_position_detection_failed", error=str(exc))
            return None

        if not data.get("procedure_detected"):
            return None

        procedure_id = data.get("procedure_id", "")
        current_step_id = data.get("current_step_id", "")

        if procedure_id not in self._procedures:
            return None

        proc = self._procedures[procedure_id]
        if current_step_id not in proc.steps:
            # Fall back to first step
            current_step_id = proc.steps_ordered[0].id if proc.steps_ordered else ""

        if not current_step_id:
            return None

        step = proc.steps[current_step_id]
        return ProcedurePosition(
            procedure_id=procedure_id,
            procedure_title=proc.title,
            current_step_id=current_step_id,
            current_step_title=step.title,
            current_phase=step.phase,
            completed_steps=data.get("completed_step_ids", []),
            declared_facts=data.get("declared_facts", {}),
        )

    def get_retrieval_filter(self, position: ProcedurePosition) -> dict:
        """
        Returns a metadata filter dict for Qdrant.
        Restricts retrieval to the current + next 2 phases of the procedure.
        """
        proc = self._procedures.get(position.procedure_id)
        if not proc:
            return {}

        # Ordered unique phases
        all_phases = list(dict.fromkeys(s.phase for s in proc.steps_ordered))
        try:
            current_idx = all_phases.index(position.current_phase)
        except ValueError:
            return {}

        relevant_phases = all_phases[current_idx: current_idx + 3]
        return {
            "procedure_id": position.procedure_id,
            "procedure_phases": relevant_phases,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine: WorkflowEngine | None = None


def get_workflow_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine