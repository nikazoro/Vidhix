"""
LexAra RAGAS Evaluation Script

Runs all 20 golden Q&A pairs against the live API, then computes RAGAS metrics.

Usage:
    python eval/run_ragas.py [--api-url http://localhost:8000]

Requirements:
    pip install ragas datasets langchain-openai   (ragas uses OpenAI by default for its LLM judge)
    OR configure ragas to use a local LLM — see ragas docs.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import requests

_SCRIPT_DIR = Path(__file__).resolve().parent

# Thresholds
_THRESHOLDS = {
    "faithfulness": 0.75,
    "answer_relevancy": 0.70,
    "context_recall": 0.65,
    "context_precision": 0.65,
}


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def create_session(base_url: str, jurisdiction: str) -> str:
    resp = requests.post(
        f"{base_url}/api/v1/chat/sessions",
        json={"jurisdiction": jurisdiction, "legal_domain": None, "session_metadata": {}},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def ask_question(base_url: str, session_id: str, question: str) -> dict:
    """
    Send a question and collect the streaming response.
    Returns {response_text, sources, contexts}.
    """
    url = f"{base_url}/api/v1/chat/sessions/{session_id}/messages"
    payload = {"content": question, "document_ids": []}

    response_text = ""
    sources = []
    contexts = []

    with requests.post(url, json=payload, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            try:
                event = json.loads(raw_line[6:])
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            content = event.get("content")

            if etype == "token":
                response_text += content or ""
            elif etype == "complete" and content:
                response_text = content.get("response", response_text)
                sources = content.get("sources", [])
                contexts = [s.get("chunk_text", "") for s in sources]

    return {
        "response_text": response_text,
        "sources": sources,
        "contexts": contexts,
    }


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def run_evaluation(base_url: str) -> None:
    golden_path = _SCRIPT_DIR / "golden_qa.jsonl"
    if not golden_path.exists():
        print(f"❌ golden_qa.jsonl not found at {golden_path}")
        sys.exit(1)

    qa_pairs = []
    with open(golden_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                qa_pairs.append(json.loads(line))

    print(f"LexAra RAGAS Evaluation — {len(qa_pairs)} test cases\n")
    print(f"Backend: {base_url}\n")

    # Collect raw results first
    raw_results = []
    for i, qa in enumerate(qa_pairs, start=1):
        question = qa["question"]
        ground_truth = qa["ground_truth"]
        jurisdiction = qa.get("jurisdiction", "CA")

        print(f"[{i:02d}/{len(qa_pairs)}] {question[:70]}…")
        try:
            session_id = create_session(base_url, jurisdiction)
            result = ask_question(base_url, session_id, question)
            raw_results.append({
                "question": question,
                "ground_truth": ground_truth,
                "answer": result["response_text"],
                "contexts": result["contexts"],
                "jurisdiction": jurisdiction,
                "domain": qa.get("domain"),
                "should_escalate": qa.get("should_escalate", False),
            })
            print(f"       ✅ Got response ({len(result['response_text'])} chars, {len(result['contexts'])} contexts)\n")
        except Exception as exc:
            print(f"       ❌ Failed: {exc}\n")
            raw_results.append({
                "question": question,
                "ground_truth": ground_truth,
                "answer": "",
                "contexts": [],
                "jurisdiction": jurisdiction,
                "domain": qa.get("domain"),
                "should_escalate": qa.get("should_escalate", False),
                "error": str(exc),
            })
        time.sleep(1)  # avoid rate limiting

    # ---------------------------------------------------------------------------
    # RAGAS evaluation
    # ---------------------------------------------------------------------------
    print("\n" + "="*60)
    print("Running RAGAS metrics…")
    print("="*60 + "\n")

    try:
        from datasets import Dataset  # type: ignore
        from ragas import evaluate  # type: ignore
        from ragas.metrics import (  # type: ignore
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
    except ImportError:
        print(
            "⚠️  RAGAS or datasets not installed.\n"
            "Run: pip install ragas datasets\n"
            "Saving raw results only."
        )
        _save_results(raw_results, {})
        return

    # Filter out errored results for RAGAS
    valid = [r for r in raw_results if r.get("answer") and r.get("contexts")]
    if not valid:
        print("⚠️  No valid results to evaluate. Check backend connectivity.")
        _save_results(raw_results, {})
        return

    dataset = Dataset.from_dict({
        "question": [r["question"] for r in valid],
        "answer": [r["answer"] for r in valid],
        "contexts": [r["contexts"] for r in valid],
        "ground_truth": [r["ground_truth"] for r in valid],
    })

    try:
        result = evaluate(
            dataset=dataset,
            metrics=[faithfulness, answer_relevancy, context_recall, context_precision],
        )
        metrics_df = result.to_pandas()
        metrics_summary = {
            "faithfulness": float(metrics_df["faithfulness"].mean()),
            "answer_relevancy": float(metrics_df["answer_relevancy"].mean()),
            "context_recall": float(metrics_df["context_recall"].mean()),
            "context_precision": float(metrics_df["context_precision"].mean()),
        }
    except Exception as exc:
        print(f"⚠️  RAGAS evaluation failed: {exc}")
        metrics_summary = {}

    # ---------------------------------------------------------------------------
    # Print summary table
    # ---------------------------------------------------------------------------
    print("\n" + "="*60)
    print("RESULTS SUMMARY")
    print("="*60)
    print(f"{'Metric':<25} {'Score':>8}  {'Threshold':>10}  {'Status':>8}")
    print("-"*60)

    all_pass = True
    for metric, threshold in _THRESHOLDS.items():
        score = metrics_summary.get(metric)
        if score is None:
            status = "N/A"
            row = f"{metric:<25} {'N/A':>8}  {threshold:>10.2f}  {status:>8}"
        else:
            passed = score >= threshold
            if not passed:
                all_pass = False
            status = "✅ PASS" if passed else "❌ FAIL"
            row = f"{metric:<25} {score:>8.3f}  {threshold:>10.2f}  {status:>8}"
        print(row)

    print("="*60)
    print(f"\nTotal test cases: {len(qa_pairs)}")
    print(f"Successfully evaluated: {len(valid)}")
    print(f"Errors: {len(qa_pairs) - len(valid)}")
    print(f"\nOverall: {'✅ ALL METRICS PASSED' if all_pass else '❌ SOME METRICS BELOW THRESHOLD'}\n")

    _save_results(raw_results, metrics_summary)


def _save_results(raw_results: list, metrics_summary: dict) -> None:
    results_dir = _SCRIPT_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = results_dir / f"{timestamp}.json"

    output = {
        "timestamp": timestamp,
        "metrics_summary": metrics_summary,
        "thresholds": _THRESHOLDS,
        "results": raw_results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Results saved to: {out_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation for LexAra")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("LEXARA_API_URL", "http://localhost:8000"),
        help="Base URL of the LexAra backend API",
    )
    args = parser.parse_args()
    run_evaluation(args.api_url)