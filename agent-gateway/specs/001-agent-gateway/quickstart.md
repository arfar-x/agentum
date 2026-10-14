# Quickstart: validating the agent gateway

Prerequisites: the agentflow stack is up (`make up`), `.env.agent-gateway` filled in from its
example, at least one agent file in `agent-gateway/config/agents/`.

1. **Start**: `make gateway-up`; `curl localhost:$GATEWAY_PORT/healthz` returns ok.
2. **Create a key**: `make gateway-key-create USER=you@example.org` prints a key once.
3. **Models**: `curl -H "Authorization: Bearer $KEY" $URL/v1/models` lists upstream models and
   agents.
4. **Agent chat (SDK)**: with the OpenAI Python SDK, set `base_url=$URL/v1`, `api_key=$KEY`,
   `model="product-assistant"`, stream a question the catalog covers. Expect a grounded answer
   and `reasoning_content` mentioning the lookup.
5. **Passthrough**: same call with a raw upstream model; compare with calling the upstream
   directly.
6. **Tool routing**: run a Jira lookup (a) from a client that has its own Jira tool, (b) with
   credential headers, (c) after `make gateway-creds-set`, (d) with nothing; confirm the four
   behaviours of spec User Story 4.
7. **Approval**: ask the agent to create a ticket in a test project; reject, then repeat and
   approve; confirm only the approved change exists.
8. **Handoff**: send a request that belongs to a specialist; confirm the announcement and that
   the next turn stays with the specialist.
9. **Isolation**: with a second key, confirm the first user's conversation, approval and
   credentials are unreachable; revoke the first key and confirm it is refused.
10. **Tests**: `cd agent-gateway && python -m pytest`; `make gateway-test`.
