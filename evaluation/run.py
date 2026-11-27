"""Evaluation CLI.

    python -m evaluation.run --split dev|holdout|all [--mode demo|live] [--name NAME]

Writes evaluation/results/<run>/cases.jsonl (expected vs actual per case), summary.json and
report.md. Retrieval and baseline numbers are measurements of deterministic code in every mode.
Model metrics are measurements of the model only in live mode; in demo mode the "model" is a
label-echo stand-in with injected faults (evaluation/demo_mechanics.py) and the report says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from app import db, knowledge
from app.config import CATEGORY_QUEUE, get_settings
from app.llm.contract import OUTPUT_SCHEMA
from app.llm.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from app.llm.provider import build_adapter
from app.triage import analyze_text
from evaluation import baseline
from evaluation.demo_mechanics import LabelEchoAdapter

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
RESULTS_DIR = HERE / "results"


def load_cases(split: str) -> list[dict]:
    files = ["dev.jsonl", "holdout.jsonl"] if split == "all" else [f"{split}.jsonl"]
    cases = []
    for f in files:
        for line in (CASES_DIR / f).read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append(json.loads(line))
    return cases


def config_hash(settings, conn) -> str:
    kb = conn.execute("SELECT id, content_hash FROM kb_sections ORDER BY id").fetchall()
    blob = json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "schema": OUTPUT_SCHEMA,
            "model": settings.llm_model,
            "effort": settings.llm_effort,
            "max_tokens": settings.llm_max_tokens,
            "top_k": settings.retrieval_top_k,
            "min_terms": settings.retrieval_min_terms,
            "max_chars": settings.retrieval_max_chars,
            "kb": [(r["id"], r["content_hash"]) for r in kb],
            "baseline": baseline.KEYWORDS,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def expected_queues(case: dict) -> list[str] | None:
    cats = case["expected_categories"]
    return None if cats is None else sorted({CATEGORY_QUEUE[c] for c in cats})


def raw_cited_ids(raw: str | None) -> list[str]:
    try:
        ids = json.loads(raw or "").get("source_ids", [])
        return [i for i in ids if isinstance(i, str)]
    except (ValueError, AttributeError):
        return []


def evaluate_case(case: dict, settings, conn, adapter) -> dict:
    sections = knowledge.search(
        conn, case["subject"], case["body"],
        top_k=settings.retrieval_top_k, min_terms=settings.retrieval_min_terms,
        max_chars=settings.retrieval_max_chars,
    )
    ids = [s.id for s in sections]
    allowed = case["allowed_sources"]
    eq = expected_queues(case)
    b_cat, b_queue = baseline.route(case["subject"], case["body"])

    t0 = time.monotonic()
    r = analyze_text(case["subject"], case["body"], sections, adapter, attempt_no=1)
    latency = round(time.monotonic() - t0, 2)
    p = r.proposal or {}
    valid = r.state == "SUCCEEDED"
    outcome = r.outcome if valid else None
    cited = raw_cited_ids(r.raw_output) if r.decided_by == "model" else []
    llm_queue = p.get("queue") if valid else None
    return {
        "id": case["id"],
        "split": case["split"],
        "tags": case["tags"],
        "subject": case["subject"],
        "body": case["body"],
        "expected": {
            "categories": case["expected_categories"],
            "queues": eq,
            "dispositions": case["expected_dispositions"],
            "allowed_sources": allowed,
            "must_manual": case["must_manual"],
            "allowed_facts": case["allowed_facts"],
            "reasonable_requests": case["reasonable_requests"],
        },
        "retrieval": {
            "ids": ids,
            "ranks": [s.rank for s in sections],
            "matched_terms": [s.matched_terms for s in sections],
            "hit_at_k": bool(allowed) and any(i in allowed for i in ids),
            "hit_at_1": bool(allowed) and bool(ids) and ids[0] in allowed,
        },
        "baseline": {
            "category": b_cat,
            "queue": b_queue,
            "queue_correct": None if eq is None else b_queue in eq,
        },
        "llm": {
            "state": r.state,
            "decided_by": r.decided_by,
            "model": r.model,
            "error_type": r.error_type,
            "error_detail": r.error_detail,
            "validation_errors": r.validation_errors,
            "disposition": outcome,
            "category": p.get("category") if valid else None,
            "queue": llm_queue,
            "draft_reply": p.get("draft_reply") if valid else None,
            "source_ids": p.get("source_ids") if valid else [],
            "requested_details": p.get("requested_details") if valid else [],
            "reason": p.get("reason") if valid else None,
            "raw_cited_ids": cited,
            "raw_output": r.raw_output,
            "latency_seconds": latency,
            "queue_correct": None if eq is None else (llm_queue in eq),
            "disposition_correct": outcome in case["expected_dispositions"],
            "draft_has_relevant_source": (
                None
                if not (valid and p.get("draft_reply"))
                else any(s in allowed for s in p.get("source_ids", []))
            ),
            "manual_ok": None if not case["must_manual"] else outcome == "MANUAL_REVIEW",
        },
    }


def ratio(num: int, den: int) -> dict:
    return {"num": num, "den": den, "pct": round(100 * num / den, 1) if den else None}


def summarize(rows: list[dict]) -> dict:
    with_src = [r for r in rows if r["expected"]["allowed_sources"]]
    routed = [r for r in rows if r["expected"]["queues"] is not None]
    must = [r for r in rows if r["expected"]["must_manual"]]
    not_must = [r for r in rows if not r["expected"]["must_manual"]]
    model_rows = [r for r in rows if r["llm"]["decided_by"] == "model"]
    cited_total = sum(len(r["llm"]["raw_cited_ids"]) for r in model_rows)
    cited_valid = sum(
        sum(1 for c in r["llm"]["raw_cited_ids"] if c in r["retrieval"]["ids"]) for r in model_rows
    )
    drafts = [r for r in rows if r["llm"]["draft_has_relevant_source"] is not None]
    b_vs_l = Counter()
    for r in routed:
        b, l = r["baseline"]["queue_correct"], r["llm"]["queue_correct"]
        b_vs_l["both_correct" if b and l else "only_baseline" if b else "only_model" if l else "both_wrong"] += 1
    return {
        "retrieval_hit_at_k": ratio(sum(r["retrieval"]["hit_at_k"] for r in with_src), len(with_src)),
        "retrieval_hit_at_1": ratio(sum(r["retrieval"]["hit_at_1"] for r in with_src), len(with_src)),
        "retrieval_no_sections": ratio(sum(1 for r in rows if not r["retrieval"]["ids"]), len(rows)),
        "baseline_routing": ratio(sum(r["baseline"]["queue_correct"] for r in routed), len(routed)),
        "model_routing": ratio(sum(bool(r["llm"]["queue_correct"]) for r in routed), len(routed)),
        "routing_comparison": dict(b_vs_l),
        "model_disposition": ratio(sum(r["llm"]["disposition_correct"] for r in rows), len(rows)),
        "model_valid_source_ids": ratio(cited_valid, cited_total),
        "drafts_with_relevant_source": ratio(sum(r["llm"]["draft_has_relevant_source"] for r in drafts), len(drafts)),
        "manual_review_recall": ratio(sum(bool(r["llm"]["manual_ok"]) for r in must), len(must)),
        "manual_review_unneeded": ratio(sum(1 for r in not_must if r["llm"]["disposition"] == "MANUAL_REVIEW"), len(not_must)),
        "decided_by_rule": ratio(sum(1 for r in rows if r["llm"]["decided_by"] == "rule"), len(rows)),
        "failed_attempts": ratio(sum(1 for r in rows if r["llm"]["state"] == "FAILED"), len(rows)),
        "error_types": dict(Counter(r["llm"]["error_type"] for r in rows if r["llm"]["error_type"])),
    }


def fmt(m: dict) -> str:
    return "n/a (0 cases)" if not m["den"] else f"{m['num']}/{m['den']} ({m['pct']}%)"


def write_report(path: Path, meta: dict, summary: dict, rows: list[dict]) -> None:
    demo = meta["mode"] == "demo"
    L = []
    L.append(f"# Evaluation report: {meta['run']}\n")
    if demo:
        L.append("> **DEMO MODE: mechanics only. Model quality NOT MEASURED.** Model rows come from a "
                 "label-echo stand-in with injected faults (`evaluation/demo_mechanics.py`). Retrieval and "
                 "keyword-baseline rows are real measurements of deterministic code.\n")
    else:
        L.append("> **LIVE MODE.** Model rows are fresh responses from the configured model.\n")
    L.append("| Field | Value |\n|---|---|")
    for k in ("mode", "model", "split", "cases", "started_at_utc", "config_hash", "top_k", "min_terms", "effort"):
        L.append(f"| {k} | `{meta[k]}` |")
    if meta.get("injected_faults"):
        L.append(f"| injected_faults | `{meta['injected_faults']}` |")
    L.append("")
    s = summary
    model_label = "stand-in (not a model metric)" if demo else meta["model"]
    L.append("## Metrics (numerator/denominator)\n")
    L.append("| Metric | Result | Notes |\n|---|---|---|")
    L.append(f"| Retrieval: ≥1 allowed section in top-k | {fmt(s['retrieval_hit_at_k'])} | cases with labelled sources |")
    L.append(f"| Retrieval: allowed section at rank 1 | {fmt(s['retrieval_hit_at_1'])} | |")
    L.append(f"| Retrieval: no section above threshold (rule NO_KB_MATCH) | {fmt(s['retrieval_no_sections'])} | |")
    L.append(f"| Routing: keyword baseline, correct queue | {fmt(s['baseline_routing'])} | cases with labelled category |")
    L.append(f"| Routing: {model_label}, correct queue | {fmt(s['model_routing'])} | failures count as wrong |")
    L.append(f"| Disposition correct | {fmt(s['model_disposition'])} | {model_label} |")
    L.append(f"| Cited IDs that were given to the model | {fmt(s['model_valid_source_ids'])} | raw output, before validation |")
    L.append(f"| Drafts citing an allowed (labelled) source | {fmt(s['drafts_with_relevant_source'])} | citation relevance by label, not text faithfulness |")
    L.append(f"| Must-manual cases routed to manual review | {fmt(s['manual_review_recall'])} | |")
    L.append(f"| Other cases sent to manual review | {fmt(s['manual_review_unneeded'])} | over-escalation |")
    L.append(f"| Decided by app rule (model not called) | {fmt(s['decided_by_rule'])} | |")
    L.append(f"| Failed attempts | {fmt(s['failed_attempts'])} | {s['error_types'] or 'none'} |")
    L.append("")
    L.append(f"Routing comparison on labelled cases: `{s['routing_comparison']}`\n")
    L.append("## Per-case results (expected vs actual)\n")
    L.append("| Case | Tags | Expected queue / disposition | Baseline queue | Model queue / disposition | Retrieved (top-k) | Cited | Check |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        e, b, m = r["expected"], r["baseline"], r["llm"]
        flags = []
        if e["queues"] is not None and not m["queue_correct"]:
            flags.append("queue")
        if not m["disposition_correct"]:
            flags.append("disposition")
        if e["allowed_sources"] and not r["retrieval"]["hit_at_k"]:
            flags.append("retrieval")
        if m["draft_has_relevant_source"] is False:
            flags.append("citation")
        if m["state"] == "FAILED":
            flags.append(m["error_type"])
        if e["queues"] is not None and not b["queue_correct"]:
            flags.append("baseline-queue")
        exp = f"{'/'.join(e['queues']) if e['queues'] else 'any'} · {'/'.join(e['dispositions'])}"
        got = f"{m['queue'] or '—'} · {m['disposition'] or m['error_type']}"
        if m["decided_by"] == "rule":
            got += " (rule)"
        L.append(f"| {r['id']} | {', '.join(r['tags'])} | {exp} | {b['queue']} | {got} | "
                 f"{', '.join(r['retrieval']['ids']) or '—'} | {', '.join(m['raw_cited_ids']) or '—'} | "
                 f"{'OK' if not flags else '✗ ' + ', '.join(flags)} |")
    L.append("")
    L.append("## Text quality\n")
    if demo:
        L.append("Not assessed: demo drafts are placeholders, not model output.\n")
    else:
        L.append("Not scored automatically. See the manual review in `docs/evaluation.md`; drafts are in `cases.jsonl`.\n")
    path.write_text("\n".join(L), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "holdout", "all"])
    ap.add_argument("--mode", choices=["demo", "live"], default=None, help="defaults to MODEL_MODE")
    ap.add_argument("--name", default=None, help="run directory name (default: timestamp-mode-split)")
    ap.add_argument("--rerender", metavar="RUN", help="rebuild report.md of an existing run from its saved files")
    args = ap.parse_args(argv)
    if args.rerender:
        out = RESULTS_DIR / args.rerender
        saved = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        rows = [json.loads(l) for l in (out / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        write_report(out / "report.md", saved["meta"], saved["metrics"], rows)
        print(f"Re-rendered evaluation/results/{args.rerender}/report.md (no cases re-run)")
        return 0

    if not args.split:
        ap.error("--split is required")
    settings = get_settings()
    mode = args.mode or settings.model_mode
    settings = replace(settings, model_mode=mode)
    cases = load_cases(args.split)
    if mode == "live":
        adapter = build_adapter(settings)
        if adapter.__class__.__name__ == "UnavailableAdapter":
            print("Live mode needs LLM_API_KEY (see README). Nothing was sent.", file=sys.stderr)
            return 2
    else:
        adapter = LabelEchoAdapter(cases)

    db.init_pool(settings.database_url)
    started = datetime.now(timezone.utc)
    run = args.name or f"{started.strftime('%Y%m%d-%H%M%S')}-{mode}-{args.split}"
    out = RESULTS_DIR / run
    out.mkdir(parents=True, exist_ok=True)
    try:
        with db.connection() as conn:
            db.seed_knowledge(conn)
            chash = config_hash(settings, conn)
            rows = []
            for c in cases:
                rows.append(evaluate_case(c, settings, conn, adapter))
                print(f"{c['id']}: {rows[-1]['llm']['disposition'] or rows[-1]['llm']['error_type']}", flush=True)
    finally:
        db.close_pool()
    summary = summarize(rows)
    meta = {
        "run": run,
        "mode": mode,
        "model": adapter.model,
        "split": args.split,
        "cases": len(cases),
        "started_at_utc": started.isoformat(timespec="seconds"),
        "config_hash": chash,
        "top_k": settings.retrieval_top_k,
        "min_terms": settings.retrieval_min_terms,
        "effort": settings.llm_effort if mode == "live" else "n/a",
        "injected_faults": getattr(adapter, "injected", None),
    }
    with (out / "cases.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(json.dumps({"meta": meta, "metrics": summary}, indent=2), encoding="utf-8")
    write_report(out / "report.md", meta, summary, rows)
    print(f"\nReport: evaluation/results/{run}/report.md")
    for k, v in summary.items():
        if isinstance(v, dict) and "num" in v:
            print(f"  {k}: {fmt(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
