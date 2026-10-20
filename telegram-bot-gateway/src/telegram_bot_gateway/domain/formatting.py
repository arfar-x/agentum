"""Markdown -> Telegram MarkdownV2, a plain-text fallback, and message splitting."""

from __future__ import annotations

import re
from dataclasses import dataclass

TELEGRAM_LIMIT = 4096
SUFFIX_RESERVE = 16

_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_INLINE = re.compile(
    r"\[(?P<ltext>[^\]\n]+)\]\((?P<url>(?:[^()\s]|\([^()\s]*\))+)\)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|__(?P<bold2>.+?)__"
    r"|~~(?P<strike>.+?)~~"
    r"|(?<![\w*])\*(?!\s)(?P<ital>[^*\n]+?)(?<!\s)\*(?![\w*])"
    r"|(?<!\w)_(?!\s)(?P<ital2>[^_\n]+?)(?<!\s)_(?!\w)",
)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_SMALL_TABLE = (5, 12)  # columns, rows


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def escape(text: str) -> str:
    return _SPECIAL.sub(r"\\\1", text)


def _escape_code(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _escape_url(url: str) -> str:
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _inline(text: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _INLINE.finditer(text):
        out.append(escape(text[pos:m.start()]))
        pos = m.end()
        if m.group("ltext") is not None:
            out.append(f"[{_inline(m.group('ltext'))}]({_escape_url(m.group('url'))})")
        elif m.group("bold") is not None or m.group("bold2") is not None:
            out.append(f"*{_inline(m.group('bold') or m.group('bold2'))}*")
        elif m.group("strike") is not None:
            out.append(f"~{_inline(m.group('strike'))}~")
        else:
            out.append(f"_{_inline(m.group('ital') or m.group('ital2'))}_")
    out.append(escape(text[pos:]))
    return "".join(out)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(lines: list[str]) -> list[str]:
    """A markdown table as bullets when small, as a code block when large."""
    header, rows = _cells(lines[0]), [_cells(l) for l in lines[2:]]
    if len(header) <= _SMALL_TABLE[0] and len(rows) <= _SMALL_TABLE[1]:
        out = []
        for row in rows:
            lead = row[0] if row else ""
            rest = "; ".join(f"{h}: {c}" for h, c in zip(header[1:], row[1:]) if c)
            out.append("• " + (f"**{lead}**" if lead else "") + (f" — {rest}" if rest else ""))
        return out
    return ["```", *lines, "```"]


def to_markdown_v2(text: str) -> str:
    stash: list[str] = []

    def keep(rendered: str) -> str:
        stash.append(rendered)
        return f"{len(stash) - 1}"

    text = _FENCE.sub(lambda m: keep(f"```{m.group(1).strip()}\n{_escape_code(m.group(2))}```"), text)
    text = _INLINE_CODE.sub(lambda m: keep(f"`{_escape_code(m.group(1))}`"), text)

    lines = text.split("\n")
    rendered: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            for piece in _table(lines[i:j]):
                if piece.startswith("```") or piece == "```":
                    rendered.append(piece)
                else:
                    rendered.append(_inline(piece))
            i = j
            continue
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        if heading:
            rendered.append(f"*{_inline(heading.group(1))}*" if heading.group(1) else "")
        elif line.startswith(">"):
            rendered.append(">" + _inline(line[1:].lstrip()))
        elif bullet:
            rendered.append(f"{bullet.group(1)}• {_inline(bullet.group(2))}")
        else:
            rendered.append(_inline(line))
        i += 1
    result = "\n".join(rendered)
    return re.sub("(\\d+)", lambda m: stash[int(m.group(1))], result)


def to_plain(text: str) -> str:
    text = re.sub(r"```[^\n`]*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    return re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)


def split_markdown(text: str, budget: int) -> list[str]:
    """Cut ``text`` into pieces of at most ``budget`` UTF-16 units, at line or word breaks.

    A cut inside a code fence closes it and reopens it in the next piece.
    """
    pieces: list[str] = []
    fence: str | None = None
    rest = text
    while utf16_len(rest) > budget:
        cut = _cut_point(rest, budget)
        head, rest = rest[:cut].rstrip("\n"), rest[cut:].lstrip("\n")
        for line in head.split("\n"):
            if line.startswith("```"):
                fence = None if fence is not None else line[3:].strip()
        if fence is not None:
            head += "\n```"
            rest = f"```{fence}\n{rest}"
        pieces.append(head)
    pieces.append(rest)
    return [p for p in pieces if p.strip()]


def _cut_point(text: str, budget: int) -> int:
    # the longest prefix within budget, then back off to a newline, else a space
    lo, hi = 1, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if utf16_len(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    for sep in ("\n\n", "\n", " "):
        at = text.rfind(sep, 0, lo)
        if at > lo // 3:
            return at + len(sep)
    return lo


@dataclass(frozen=True)
class Chunk:
    markdown_v2: str
    plain: str


def render(text: str, limit: int = TELEGRAM_LIMIT) -> list[Chunk]:
    """The answer as chunks that each fit Telegram, each in MarkdownV2 and in plain text."""
    budget = limit - SUFFIX_RESERVE
    pieces = split_markdown(text, budget) if utf16_len(text) > budget else [text]
    chunks: list[Chunk] = []
    queue = list(pieces)
    while queue:
        piece = queue.pop(0)
        converted = to_markdown_v2(piece)
        if utf16_len(converted) > budget and utf16_len(piece) > 1:
            queue[0:0] = split_markdown(piece, max(1, utf16_len(piece) // 2))
            continue
        chunks.append(Chunk(converted, to_plain(piece)))
    if len(chunks) > 1:
        n = len(chunks)
        chunks = [Chunk(c.markdown_v2 + f"\n\\({i}/{n}\\)", c.plain + f"\n({i}/{n})") for i, c in enumerate(chunks, 1)]
    return chunks
