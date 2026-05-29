"""
Load workflow DAG JSON files into the database and verify they parse correctly.

Usage:
    python -m scripts.seed_workflows
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings
from core.workflow_engine import WorkflowEngine, _load_procedure_from_file

settings = get_settings()


async def main() -> None:
    print("LexAra — Loading workflow DAGs...\n")

    workflows_path = settings.workflows_path
    if not workflows_path.exists():
        print(f"❌ Workflows directory not found: {workflows_path}")
        sys.exit(1)

    json_files = list(workflows_path.glob("*.json"))
    if not json_files:
        print("⚠️  No workflow JSON files found.")
        return

    engine = WorkflowEngine()
    success = 0
    errors = 0

    for json_file in sorted(json_files):
        print(f"🔄 Loading: {json_file.name}")
        try:
            proc = _load_procedure_from_file(json_file)
            print(f"   ✅ {proc.title}")
            print(f"      ID:          {proc.id}")
            print(f"      Jurisdiction: {proc.jurisdiction}")
            print(f"      Domain:       {proc.legal_domain}")
            print(f"      Steps:        {len(proc.steps)}")
            print(f"      Edges:        {len(proc.edges)}")

            # Validate all edge references point to real step IDs
            for edge in proc.edges:
                if edge.from_step and edge.from_step not in proc.steps:
                    print(f"   ⚠️  Edge references unknown from_step: {edge.from_step!r}")
                if edge.to_step and edge.to_step not in proc.steps:
                    print(f"   ⚠️  Edge references unknown to_step: {edge.to_step!r}")

            success += 1
        except Exception as exc:
            print(f"   ❌ Failed to load {json_file.name}: {exc}")
            errors += 1
        print()

    print(f"Done. {success} loaded, {errors} failed.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())