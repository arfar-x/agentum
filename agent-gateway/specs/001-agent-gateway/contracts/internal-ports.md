# Contract: application ports

All ports are `Protocol`s in `application/ports.py`; adapters implement them and tests use
in-memory fakes.

| Port | Responsibility |
|---|---|
| `Llm` | `complete(request) -> Completion` and `stream(request) -> iterator[Chunk]`; raises typed `LlmTransient` / `LlmRejected`. |
| `ToolGateway` | `list_tools(server)`, `call(server, tool, args, credentials) -> ToolResult` (errors returned as results). |
| `Retriever` | `search(queries, limit, types) -> hits` (the catalog). |
| `KeyStore` | `create`, `find_by_secret`, `list`, `revoke`. |
| `CredentialStore` | `get(user, server) -> {var: value}`, `set`, `delete` (encrypted at rest). |
| `ConversationStore` | `get_thread`, `put_thread`, `put_pending`, `take_pending(user, call_id)` (atomic consume), `prune`. |
| `Lock` | `hold(user, thread_key)` context. |
| `RateLimiter` | `allow(key_id) -> bool`. |
| `Clock` | `now()`. |
| `AgentRuntime` | `run(turn) -> iterator[Event]`; the LangGraph adapter implements it using the other ports. |
