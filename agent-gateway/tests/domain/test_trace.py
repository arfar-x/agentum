# Covers: FR-MEM-01, FR-MEM-02, FR-MEM-04, FR-MEM-05

from agent_gateway.domain import trace


def test_an_entry_keeps_the_call_and_its_outcome_but_drops_gateway_flags():
    e = trace.entry("assistant", "kb_search", {"queries": ["cto"], "confirm": True}, ok=True)
    assert e == {"agent": "assistant", "tool": "kb_search", "args": {"queries": ["cto"]}, "ok": True}
    assert trace.entry("a", "t", None, ok=False)["ok"] is False


def test_long_values_are_shortened_and_secret_named_arguments_are_hidden():
    e = trace.entry("a", "t", {"body": "x" * 500, "api_key": "sk-123", "nested": {"password": "p", "n": 1}}, ok=True)
    assert len(e["args"]["body"]) == trace.VALUE_CHARS + 1 and e["args"]["body"].endswith("…")
    assert e["args"]["api_key"] == trace.REDACTED and e["args"]["nested"] == {"password": trace.REDACTED, "n": 1}
    assert len(trace.entry("a", "t", {"ids": list(range(50))}, ok=True)["args"]["ids"]) == 10


def test_a_catalog_lookup_records_how_many_hits_it_found():
    e = trace.entry("a", trace.CATALOG_LOOKUP, {"query": "who is the cto"}, ok=True, found=3)
    assert e["found"] == 3 and "found 3" in trace.note([e])


def test_the_record_is_bounded_and_drops_the_oldest_first():
    old = [trace.entry("a", f"t{i}", {}, ok=True) for i in range(35)]
    new = [trace.entry("a", f"n{i}", {}, ok=True) for i in range(10)]
    merged = trace.merge(old, new)
    assert len(merged) == trace.MAX_ENTRIES and merged[-1]["tool"] == "n9" and merged[0]["tool"] == "t5"


def test_the_note_says_what_ran_and_that_it_was_not_a_guess():
    text = trace.note([trace.entry("assistant", "kb_get", {"id": "form:cto"}, ok=True),
                       trace.entry("assistant", "kb_search", {"queries": ["x"]}, ok=False)])
    assert "did not see these calls" in text and "Do not say you guessed" in text
    assert 'kb_get {"id": "form:cto"} -> worked' in text and "kb_search" in text and "-> failed" in text
    assert "results" in text  # it says only calls are listed
    assert trace.note([]) is None


def test_the_note_lists_only_the_latest_entries():
    entries = [trace.entry("a", f"t{i}", {}, ok=True) for i in range(40)]
    text = trace.note(entries)
    assert "t39" in text and "t19 " not in text and "t20" in text
