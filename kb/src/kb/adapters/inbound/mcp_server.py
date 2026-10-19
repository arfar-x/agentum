"""The agent's front door: two read-only MCP tools over HTTP.

Internal-only, with no published port and no authentication of its own -- the
same trust model `mcp-agent-skills` runs under, and for the same reason:
reachability from `api` on the backend network is the access control. Unlike
that server, this one takes **no credentials at all**. The catalog is shared, so
there is nothing per-user for a caller to supply, and nothing a caller could
supply that would change what it sees.

Both tools return a structured error instead of raising. An agent that gets
`{"error": {...}}` can tell the user the catalog is unavailable and carry on;
an exception would fail the whole turn over a search that was only ever meant to
add context.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from kb.container import Container
from kb.domain.entry import EntryType

logger = logging.getLogger("kb.mcp")

INSTRUCTIONS = """\
The catalog of what knowledge exists in this organization and where it lives.

It holds pointers and summaries. Search it to find out *which* document answers
a question, then read that document with the tool named in the result's `fetch`
field -- the summary is a pointer, not a source. For some sources the catalog
also keeps each document's full text; for those, `fetch` names `kb_get`, which
returns the text page by page.

Every search carries the question twice: as the user wrote it (`queries`) and
rewritten in the catalog's language (`catalog_queries`, required). Documents are
catalogued only in that language, so a question that reaches the catalog in
another one finds nothing; the rankings of both are combined.
"""


def _hit_payload(hit: Any) -> dict[str, Any]:
    return {
        "id": hit.entry.id,
        "type": hit.entry.type.value,
        "title": dict(hit.entry.title),
        "summary": dict(hit.entry.summary),
        "owner_team": hit.entry.owner_team,
        "tags": list(hit.entry.tags),
        "url": hit.entry.location.url,
        # The ready-made next call. Without it a smaller model has to work out
        # which tool reads a wiki page versus an issue, which is the most
        # likely way a two-step retrieval goes wrong.
        "fetch": hit.fetch_hint,
        "stale": hit.stale,
    }


#: fastmcp takes only a docstring's summary line as the tool description, so
#: anything a model must know to call the tool correctly is passed explicitly
#: here. (Per-argument help still comes from the docstring's Args section,
#: which does reach the schema.)
def search_description(languages: tuple[str, ...]) -> str:
    """The `kb_search` description, naming the languages this catalog is written in."""
    catalog = " and ".join(languages)
    return f"""\
Find which documents answer a question, and where they live.

Returns hits with a `fetch` field naming the tool and arguments that read the
real document -- always read it before answering; a catalog summary is a
pointer, not a source. That tool is sometimes `kb_get` itself, for documents
whose full text the catalog keeps. A hit marked `stale` may have an
out-of-date summary, but the document itself is always current.

The catalog is written in: {catalog}. Always pass `queries` (the question in the
user's own words and language) AND `catalog_queries` (the same question
rewritten in {catalog}, including the key terms, names and synonyms you would
expect in a document). Both are searched and the rankings combined. If the
user already wrote in {catalog}, repeat the question there. A search without
`catalog_queries` is refused: a question in another language finds nothing.
"""


GET_DESCRIPTION = """\
Everything the catalog knows about one entry, by its id -- and, for sources
that keep it, the document's own text.

When the result has `content`, answer from `content.text`: it is the real
document, not a summary. It comes in pages; if `content.next_offset` is set
and you need more, call again with `offset` set to it. If `content.truncated`
is true the catalog holds only the start of the document -- say so, and give
the entry's `location.url` for the rest. When `content` is null, read the
document with the tool named in `fetch`, or point the user to `location.url`.
"""


def build_app(container: Container, languages: tuple[str, ...] = ("en",)) -> FastMCP:
    app = FastMCP(name="kb", instructions=INSTRUCTIONS)

    @app.tool(description=search_description(languages))
    def kb_search(
        queries: list[str],
        catalog_queries: list[str],
        types: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Find which documents answer a question.

        Args:
            queries: The question in the user's own words and language (one
                or more phrasings).
            catalog_queries: REQUIRED. The same question rewritten in the
                catalog's language (named in this tool's description), with
                its key terms, names and synonyms. Repeat the question if the
                user already wrote in that language.
            types: Optional filter: doc, spec, product, system, team, person,
                term.
            tags: Optional filter on an entry's tags.
            limit: Maximum number of results.

        Returns hits with a `fetch` field naming the tool and arguments that
        read the real document. Always read that document before answering from
        a summary. A hit marked `stale` may have an out-of-date summary; the
        document itself is always current.
        """
        catalog_queries = [q for q in catalog_queries if q and q.strip()]
        if not catalog_queries:
            return {
                "error": {
                    "type": "bad_argument",
                    "message": (
                        "catalog_queries is required: pass the same question rewritten in "
                        f"the catalog's language ({' and '.join(languages)}), then search again."
                    ),
                }
            }
        try:
            parsed_types = [EntryType(t) for t in types] if types else None
        except ValueError as exc:
            return {"error": {"type": "bad_argument", "message": str(exc)}}
        try:
            result = container.search.execute([*queries, *catalog_queries], types=parsed_types, tags=tags, limit=limit)
        except Exception as exc:  # database down, connection dropped, ...
            logger.exception("kb_search failed")
            return {"error": {"type": "catalog_unavailable", "message": str(exc).strip()}}
        return {
            "hits": [_hit_payload(hit) for hit in result.hits],
            "queries": list(result.queries),
            "notes": list(result.notes),
        }

    @app.tool(description=GET_DESCRIPTION)
    def kb_get(id: str, offset: int = 0) -> dict[str, Any]:
        """Everything the catalog knows about one entry, by its id.

        Args:
            id: An entry id, as returned by `kb_search`.
            offset: Where in the document's text to start reading, for the
                next page of a long document: pass the previous result's
                `content.next_offset`. Leave at 0 for the first page.

        Returns the entry, its `fetch` hint and, for a source that keeps
        documents' text, one page of it in `content` (null otherwise).
        """
        try:
            view = container.get_entry.execute(id, offset=offset)
        except Exception as exc:
            logger.exception("kb_get failed")
            return {"error": {"type": "catalog_unavailable", "message": str(exc).strip()}}
        if view is None:
            return {"error": {"type": "not_found", "message": f"no entry with id {id!r}"}}
        return {
            "entry": view.entry.model_dump(mode="json"),
            "fetch": view.fetch_hint,
            "stale": view.stale,
            "hidden": view.hidden,
            "content": view.content.model_dump(mode="json") if view.content else None,
        }

    return app


def main() -> None:  # pragma: no cover -- the container's entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from kb.config import Settings
    from kb.container import build

    settings = Settings.from_env()
    container = build(settings)
    app = build_app(container, settings.summary_languages)
    logger.info("kb mcp server listening on %s:%s/mcp", settings.mcp_host, settings.mcp_port)
    app.run(transport="http", host=settings.mcp_host, port=settings.mcp_port, path="/mcp")


if __name__ == "__main__":  # pragma: no cover
    main()
