from datetime import datetime, timezone

from telegram_bot_gateway.domain import batching, clarify, content, pairing, settings
from telegram_bot_gateway.domain.content import Item

# Covers: FR-INB-05, FR-INB-06, FR-INB-08, FR-INB-13, FR-SES-06, FR-CMD-04, FR-CMD-06, FR-LNK-02, FR-ASK-01


def test_reply_and_forward_prefixes():
    assert content.compose("hi", quoted="old text") == '[Replying to: "old text"]\n\nhi'
    when = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)
    assert content.compose("hi", forwarded=("Ann", when)) == "[Forwarded from Ann, 2026-10-01 12:30 UTC]\nhi"
    assert content.compose("hi", forwarded=(None, None)).startswith("[Forwarded from an unknown sender]")


def test_merge_joins_text_and_collects_images():
    assert content.merge([Item("a"), Item("b")]) == "a\nb"
    parts = content.merge([Item("look", ("data:image/png;base64,AA",)), Item("", ("data:image/png;base64,BB",))])
    assert [p["type"] for p in parts] == ["text", "image_url", "image_url"]


def test_dropping_images_keeps_the_text_that_identifies_a_thread():
    parts = [{"type": "text", "text": "see"}, {"type": "image_url", "image_url": {"url": "data:x"}}]
    out = content.drop_images([{"role": "user", "content": parts}, {"role": "assistant", "content": "ok"}])
    assert out[0]["content"] == "see" and out[1]["content"] == "ok"
    assert parts[1]["type"] == "image_url"  # the original is untouched


def test_text_debounce_tiers():
    assert batching.text_delay(10) == 0.18
    assert batching.text_delay(500) == 0.24
    assert batching.text_delay(2000) == 0.3
    assert batching.text_delay(4050) == 1.0 and batching.MEDIA_DELAY == 0.8


def test_settings_validation():
    assert settings.parse("temperature", "0.5") == 0.5
    assert settings.parse("max_tokens", "100") == 100
    assert settings.parse("stop", '["a","b"]') == ["a", "b"]
    assert settings.parse("verbosity", "HIGH") == "high"
    for name, bad in [("temperature", "3"), ("top_p", "x"), ("max_tokens", "0"), ("nope", "1"), ("verbosity", "loud"), ("stop", "[1]")]:
        try:
            settings.parse(name, bad)
        except ValueError:
            continue
        raise AssertionError(f"{name}={bad} should be refused")
    assert settings.parse_reasoning("High") == "high"


def test_pairing_codes_use_the_unambiguous_alphabet():
    for _ in range(50):
        code = pairing.new_code()
        assert len(code) == 8 and not set(code) & set("0O1I")
    assert pairing.normalise(" ab-cd ") == "ABCD"


def test_clarify_parses_the_gateway_shape_and_a_flat_one():
    q = clarify.parse('{"questions":[{"question":"Run it?","choices":["Approve","Reject"]}]}')
    assert q.text == "Run it?" and q.is_approval
    flat = clarify.parse('{"question":"Which?","options":[{"label":"a"},"b"]}')
    assert flat.options == ("a", "b") and not flat.is_approval
    assert clarify.parse("not json").text == "not json"
    assert clarify.TOOL["function"]["name"] == "clarify"
