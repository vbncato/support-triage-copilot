import json

import pytest

from app.llm.contract import OUTPUT_SCHEMA, asks_for_secret, validate_output

GIVEN = ["kb-auth-401", "kb-auth-token-lifecycle"]


def out(**kw):
    base = {
        "disposition": "DRAFT_REPLY",
        "category": "AUTHENTICATION",
        "draft_reply": "Refresh the token before it expires.",
        "source_ids": ["kb-auth-401"],
        "requested_details": [],
        "reason": "401 token_expired.",
    }
    base.update(kw)
    return json.dumps(base)


def codes(v):
    return {e["code"] for e in v.errors}


def test_valid_draft_reply():
    v = validate_output(out(), GIVEN)
    assert v.ok
    assert v.proposal["queue"] == "access-and-identity"


def test_queue_comes_from_app_mapping_not_model():
    v = validate_output(out(category="WEBHOOK_DELIVERY"), GIVEN)
    assert v.proposal["queue"] == "integrations"


def test_draft_reply_requires_text_and_source():
    assert "DRAFT_REQUIRED" in codes(validate_output(out(draft_reply=None), GIVEN))
    assert "SOURCE_REQUIRED" in codes(validate_output(out(source_ids=[]), GIVEN))


def test_source_not_given_to_model_is_rejected():
    v = validate_output(out(source_ids=["kb-auth-401", "kb-rate-limits-advanced"]), GIVEN)
    assert not v.ok
    assert "UNKNOWN_SOURCE" in codes(v)


def test_existing_section_that_was_not_provided_is_rejected():
    # kb-rate-limit-429 exists in the KB but was not among the sections passed to the model.
    v = validate_output(out(source_ids=["kb-rate-limit-429"]), GIVEN)
    assert "UNKNOWN_SOURCE" in codes(v)


def test_needs_info_requires_details_and_question():
    v = validate_output(out(disposition="NEEDS_INFO", requested_details=[], source_ids=[]), GIVEN)
    assert "DETAILS_REQUIRED" in codes(v)
    v = validate_output(out(disposition="NEEDS_INFO", requested_details=["request_id"], draft_reply=None), GIVEN)
    assert "DRAFT_REQUIRED" in codes(v)
    v = validate_output(out(disposition="NEEDS_INFO", requested_details=["request_id", "timestamp"],
                            source_ids=[]), GIVEN)
    assert v.ok


def test_requested_details_closed_set():
    v = validate_output(out(disposition="NEEDS_INFO", requested_details=["api_key"]), GIVEN)
    assert "BAD_DETAIL" in codes(v)


def test_manual_review_drops_draft_and_needs_reason():
    v = validate_output(out(disposition="MANUAL_REVIEW", source_ids=[], draft_reply="ignored"), GIVEN)
    assert v.ok and v.proposal["draft_reply"] is None
    assert "MISSING_REASON" in codes(validate_output(out(disposition="MANUAL_REVIEW", reason=" "), GIVEN))


def test_enum_case_is_normalised():
    v = validate_output(out(disposition="Draft_Reply", category="authentication"), GIVEN)
    assert v.ok and v.proposal["disposition"] == "DRAFT_REPLY" and v.proposal["category"] == "AUTHENTICATION"


@pytest.mark.parametrize("raw", ["not json", "[1,2]", "", "{"])
def test_invalid_json(raw):
    v = validate_output(raw, GIVEN)
    assert v.proposal is None and codes(v) == {"INVALID_JSON"}


def test_unknown_category_and_extra_field():
    v = validate_output(out(category="BILLING", confidence=0.93), GIVEN)
    assert {"BAD_CATEGORY", "UNEXPECTED_FIELD"} <= codes(v)


def test_secret_request_is_rejected_but_warning_is_allowed():
    assert asks_for_secret("Please send us your API key so we can check.")
    assert asks_for_secret("Could you share the access token you are using?")
    assert not asks_for_secret("Please do not send API keys or tokens; we do not need them.")
    assert not asks_for_secret("Never share your client secret with anyone.")
    v = validate_output(out(draft_reply="Please paste your password here."), GIVEN)
    assert "SECRET_REQUEST" in codes(v)


def test_schema_uses_only_supported_features():
    text = json.dumps(OUTPUT_SCHEMA)
    for unsupported in ("maxLength", "minLength", "minimum", "maximum", "oneOf", "pattern"):
        assert unsupported not in text
    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"])
