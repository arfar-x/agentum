from telegram_bot_gateway.domain.formatting import escape, render, split_markdown, to_markdown_v2, to_plain, utf16_len

# Covers: FR-OUT-03, FR-OUT-04, FR-OUT-05


def test_special_characters_are_escaped():
    assert to_markdown_v2("a.b (c) - d!") == r"a\.b \(c\) \- d\!"


def test_bold_italic_strike_and_headings():
    assert to_markdown_v2("**b** and *i* and ~~s~~") == r"*b* and _i_ and ~s~"
    assert to_markdown_v2("# Title") == "*Title*"


def test_code_is_protected():
    assert to_markdown_v2("run `a_b*c`") == "run `a_b*c`"
    out = to_markdown_v2("```py\nx = 1 # *no*\n```")
    assert out == "```py\nx = 1 # *no*\n```"
    assert to_markdown_v2("`a\\b`") == "`a\\\\b`"


def test_links_keep_their_target():
    assert to_markdown_v2("[docs](https://x.org/a_(b))") == r"[docs](https://x.org/a_(b\))"


def test_bullets_and_quotes():
    assert to_markdown_v2("- one\n- two") == "• one\n• two"
    assert to_markdown_v2("> quoted.") == r">quoted\."


def test_small_table_becomes_bullets_and_large_becomes_code():
    small = "| name | role |\n|---|---|\n| ann | dev |"
    assert to_markdown_v2(small) == r"• *ann* — role: dev"
    rows = "\n".join(f"| {i} | x |" for i in range(20))
    big = "| n | v |\n|---|---|\n" + rows
    assert to_markdown_v2(big).startswith("```\n")


def test_plain_fallback_strips_markup():
    assert to_plain("**bold** `code` [a](http://b) # x") == "bold code a (http://b) # x"
    assert to_plain("# Head\ntext") == "Head\ntext"


def test_short_text_is_one_chunk_without_suffix():
    chunks = render("hello.")
    assert len(chunks) == 1 and chunks[0].markdown_v2 == r"hello\." and chunks[0].plain == "hello."


def test_long_text_splits_within_the_limit_with_suffixes():
    text = "\n".join(f"line {i} with some words." for i in range(600))
    chunks = render(text)
    assert len(chunks) > 1
    assert all(utf16_len(c.markdown_v2) <= 4096 for c in chunks)
    assert chunks[0].plain.endswith(f"(1/{len(chunks)})")
    assert chunks[-1].markdown_v2.endswith(f"\\({len(chunks)}/{len(chunks)}\\)")


def test_limit_counts_utf16_units():
    assert utf16_len("😀") == 2
    chunks = render("😀" * 3000)
    assert all(utf16_len(c.markdown_v2) <= 4096 for c in chunks)


def test_escaping_that_grows_the_text_is_resplit():
    chunks = render("." * 6000)
    assert all(utf16_len(c.markdown_v2) <= 4096 for c in chunks)


def test_a_cut_inside_a_fence_closes_and_reopens_it():
    body = "\n".join("x = 1" for _ in range(100))
    pieces = split_markdown(f"```py\n{body}\n```", 200)
    assert len(pieces) > 1
    assert pieces[0].endswith("```") and pieces[1].startswith("```py")


def test_escape_helper():
    assert escape("a_b") == r"a\_b"
