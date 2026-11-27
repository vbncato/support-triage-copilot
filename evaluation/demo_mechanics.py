"""Demo-mode stand-in for the model during evaluation. NOT a measure of model quality.

It builds outputs from the case labels and then injects known errors at fixed positions, so the
evaluation code is exercised on both correct and wrong outputs (wrong category, wrong disposition,
invented source, missing draft). Labels are used here only to fabricate these mechanics fixtures;
they are never sent to a model. Reports produced with this adapter say so in their header.
"""

from __future__ import annotations

import json

from app.config import CATEGORIES
from app.llm.base import ModelOutput, ModelRequest

INJECTED = {
    2: "wrong_category",
    5: "invented_source",
    8: "wrong_disposition",
    11: "missing_draft",
}


class LabelEchoAdapter:
    mode = "demo"
    model = "demo-mechanics-label-echo"

    def __init__(self, cases: list[dict]):
        self.by_subject = {c["subject"]: (i, c) for i, c in enumerate(cases)}
        self.injected: dict[str, str] = {}

    def complete(self, request: ModelRequest) -> ModelOutput:
        i, case = self.by_subject[request.subject]
        provided = [d["id"] for d in json.loads(
            request.user.split("<knowledge_base_sections>\n", 1)[1].split("\n</knowledge_base_sections>")[0]
        )]
        disposition = case["expected_dispositions"][0]
        category = (case["expected_categories"] or ["OTHER"])[0]
        sources = [s for s in case["allowed_sources"] if s in provided][:1]
        draft = None if disposition == "MANUAL_REVIEW" else "[synthetic demo draft generated from labels]"
        details = case["reasonable_requests"][:3] if disposition == "NEEDS_INFO" else []
        if disposition == "NEEDS_INFO" and not details:
            details = ["request_id"]
        if disposition == "DRAFT_REPLY" and not sources and provided:
            sources = provided[:1]

        fault = INJECTED.get(i % 12)
        if fault == "wrong_category":
            category = CATEGORIES[(CATEGORIES.index(category) + 1) % len(CATEGORIES)]
        elif fault == "invented_source":
            sources = sources + ["kb-does-not-exist"]
        elif fault == "wrong_disposition":
            disposition = "DRAFT_REPLY" if disposition != "DRAFT_REPLY" else "MANUAL_REVIEW"
            draft = "[synthetic demo draft generated from labels]" if disposition == "DRAFT_REPLY" else None
            sources = sources or provided[:1]
        elif fault == "missing_draft" and disposition != "MANUAL_REVIEW":
            draft = None
        if fault:
            self.injected[case["id"]] = fault
        out = {
            "disposition": disposition,
            "category": category,
            "draft_reply": draft,
            "source_ids": sources,
            "requested_details": details,
            "reason": f"[synthetic demo output for {case['id']}]",
        }
        return ModelOutput(json.dumps(out), self.model, "end_turn")
