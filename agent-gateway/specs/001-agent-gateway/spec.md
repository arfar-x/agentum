# Feature Specification: Agent Gateway

**Feature Branch**: `feat/agent-gateway`

**Created**: 2026-10-14

**Status**: Draft

**Input**: User description: "A self-hosted service that serves defined agents through an OpenAI-compatible API, so any client (opencode, Hermes, Codex, openclaw, the OpenAI SDK, LibreChat) can use them, over the network, for many users."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Use an agent from any client as if it were a model (Priority: P1)

A user points the client they already use at the gateway's address with a personal key. The gateway lists the agents beside the underlying models. The user picks an agent exactly as they would pick a model, chats, and gets streamed answers. Nothing about the exchange reveals whether it is an agent, a plain hosted model or a real OpenAI service.

**Why this priority**: This is the whole point. Without it the agents stay locked in one UI.

**Independent Test**: With one agent defined, select it by name in an unmodified OpenAI SDK client, send a prompt with a system prompt, a reasoning-effort setting and streaming on, and receive a well-formed streamed answer.

**Acceptance Scenarios**:

1. **Given** a valid key, **When** a client asks for the list of models, **Then** it receives every underlying model and every agent, each as an ordinary model entry.
2. **Given** a valid key, **When** a client sends a chat request naming an agent, **Then** the answer follows the agent's instructions and is delivered in the same shape (streamed or not) a plain model would deliver it.
3. **Given** a client system prompt, **When** it chats with an agent, **Then** that prompt is honoured in addition to the agent's own instructions.
4. **Given** a client that sets reasoning effort or sampling parameters, **When** the agent allows client overrides, **Then** the settings take effect; **When** a parameter is locked by the agent, **Then** the agent's value is kept and the request still succeeds.
5. **Given** no key or a wrong key, **When** any request arrives, **Then** it is refused in the standard error shape.

---

### User Story 2 - Call the underlying model directly, unchanged (Priority: P1)

A user sometimes wants the raw model with no agent. They name the model, and the request goes to it untouched. The same holds for every other OpenAI-style route the gateway does not need to handle itself.

**Why this priority**: Users must never need a second endpoint or a second client configuration.

**Independent Test**: Send the same request directly to the underlying model and through the gateway; the answers are equivalent and no agent instruction appears.

**Acceptance Scenarios**:

1. **Given** a model name that is not an agent, **When** a chat request arrives, **Then** it is forwarded unchanged and no agent instructions, tools or retrieval are added.
2. **Given** any other route the underlying service supports (embeddings and so on), **When** a client calls it through the gateway, **Then** it works as it would directly.
3. **Given** the underlying service is down, **When** a client calls, **Then** it receives the standard error shape with the matching status.

---

### User Story 3 - Agents answer from the organisation's knowledge without being asked to look (Priority: P1)

Agents defined to consult the knowledge catalog always do, before the model answers, so the first answer is already grounded and the user does not pay for the model deciding to search.

**Why this priority**: It lowers latency and removes a failure mode where the model skips lookup.

**Independent Test**: Ask a product question that the catalog covers; the answer cites it, and the model made no search call of its own.

**Acceptance Scenarios**:

1. **Given** an agent with retrieval enabled, **When** the user asks something, **Then** relevant catalog hits are available to the model on its first step.
2. **Given** the catalog is unreachable, **When** the user asks, **Then** the answer still arrives, noting that the catalog could not be consulted.
3. **Given** an agent with retrieval off, **When** the user asks, **Then** no catalog lookup is made.

---

### User Story 4 - Agents use tools, preferring the user's own client and credentials (Priority: P2)

When an agent needs a tool (for example reading or changing a ticket or a wiki page), the user's own client runs it if that client has an equivalent tool, using the credentials already on the user's machine. Otherwise the gateway runs it with credentials the client sent with the request, then with credentials the user stored with the gateway. If none exist, the call is handed to the client, and the agent tells the user plainly what is missing.

**Why this priority**: Tools are what make agents useful, and credential handling decides whether non-technical and technical users can both use them safely.

**Independent Test**: Run the same request four ways (client has the tool, request carries credentials, credentials stored, nothing available) and observe the four behaviours.

**Acceptance Scenarios**:

1. **Given** the client declares an equivalent tool, **When** the agent needs it, **Then** the call is returned to the client to run and the gateway never contacts the external system.
2. **Given** no equivalent client tool but credentials on the request, **When** the agent needs the tool, **Then** the gateway runs it with those credentials only.
3. **Given** no client tool and no request credentials but stored credentials, **When** the agent needs the tool, **Then** the gateway runs it with the stored ones.
4. **Given** nothing available, **When** the agent needs the tool, **Then** the call goes to the client, and if that fails the agent explains in one or two sentences what to set up.
5. **Given** one user's credentials, **When** another user chats, **Then** those credentials are never used.

---

### User Story 5 - Changes to external systems are approved in the conversation (Priority: P2)

Before the gateway itself creates or edits anything in an external system, the user sees exactly what will happen and approves or rejects it. If the client has its own question or approval feature, the prompt appears there. Otherwise it appears as plain text with a one-use approval code that only works for that exact change.

**Why this priority**: Safe writes are a precondition for using agents beyond read-only.

**Independent Test**: Ask an agent to create a ticket; see a preview; reject and verify nothing changed; repeat, approve and verify exactly the previewed change happened.

**Acceptance Scenarios**:

1. **Given** the agent proposes a write the gateway would run, **When** it is shown to the user, **Then** the preview states the action and its arguments and nothing has been written.
2. **Given** approval, **When** the conversation continues, **Then** exactly the previewed change is made.
3. **Given** rejection, **When** the conversation continues, **Then** nothing is written and the agent acknowledges and moves on.
4. **Given** a code that was altered, reused, expired or issued to another user, **When** it is presented, **Then** it is refused and nothing is written.
5. **Given** the client runs the tool itself, **When** the agent proposes a write, **Then** the client's own permission prompt is the gate.

---

### User Story 6 - Work moves between specialist agents visibly (Priority: P2)

An agent can hand the conversation to a specialist. Later turns continue with that specialist. The user is told that a handoff or background step is happening, in the way their client normally shows such things.

**Why this priority**: The current product flow depends on specialists.

**Independent Test**: Start with a router agent, ask for work that belongs to a specialist, see the handoff announced, and see the next message answered by the specialist.

**Acceptance Scenarios**:

1. **Given** a request that belongs to a specialist, **When** the agent hands off, **Then** the user is told and the specialist takes over.
2. **Given** a follow-up message, **When** it arrives after a handoff, **Then** it goes to the specialist, not back to the router.
3. **Given** a client that shows no reasoning or status, **When** a handoff occurs, **Then** the user still learns of it from a short note in the conversation.

---

### User Story 7 - The loop keeps going when the model slips (Priority: P2)

If the model makes a mistake (malformed tool arguments, a tool that does not exist, an empty reply), the gateway tells it concisely what was wrong and how to fix it, and the run continues instead of failing.

**Why this priority**: Reliability is what makes the experience feel like a normal API.

**Independent Test**: Script a model that first emits an unknown tool then a correct call; the user sees one correct answer and no error.

**Acceptance Scenarios**:

1. **Given** a malformed or unknown tool call, **When** it is detected, **Then** the model receives a short corrective message and tries again.
2. **Given** repeated failure beyond a limit, **When** the limit is reached, **Then** the user gets a clear, standard error rather than a hang.
3. **Given** a transient failure of the underlying model, **When** it occurs, **Then** the gateway retries before surfacing an error.

---

### User Story 8 - An operator runs it for many people (Priority: P3)

An operator issues, lists and revokes keys per person, stores a person's credentials for them if wanted, defines agents in files, and runs the service for users on different machines.

**Why this priority**: Needed to operate, but each earlier story is testable without it.

**Independent Test**: Issue two keys, confirm each user sees only their own conversations and credentials, revoke one, confirm it stops working at once.

**Acceptance Scenarios**:

1. **Given** two users, **When** both chat, **Then** neither can read, resume or approve the other's conversation.
2. **Given** a revoked key, **When** it is used, **Then** it is refused immediately.
3. **Given** a heavy user, **When** they exceed their rate limit, **Then** they receive the standard rate-limit response and others are unaffected.
4. **Given** an agent file with a mistake, **When** the service starts, **Then** it names the file and problem and does not serve a half-loaded set.

---

### Edge Cases

- A client resends an edited or truncated history: the conversation continues from what it shows, never errors, and never leaks another user's state.
- Two requests arrive at once for the same conversation: they are handled one after the other.
- A tool takes a long time: the client connection stays alive and the user sees progress.
- A client declares a tool whose name merely resembles an agent tool: matching is by exact normalized name or an explicit alias, never by guess.
- The client's tool list changes between turns: the executor is chosen again for each call.
- The user answers an approval prompt many turns later or never: stale approvals expire and are refused.
- The agent asks the user a question and the client has no question feature: it is asked as plain text.
- A request uses a feature the agent cannot honour (several alternative completions, token probabilities): it is refused in the standard error shape, with a message naming the parameter.
- A stream is cut mid-answer: the gateway stops work and leaves no half-applied write.
- A secret appears in a log, an error or an activity note: it must not.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The service MUST accept requests from standard OpenAI-style clients without client-side changes beyond the address and key.
- **FR-002**: The list of models MUST include every underlying model and every defined agent.
- **FR-003**: A request naming an agent MUST be answered by that agent; one naming anything else MUST be forwarded unchanged.
- **FR-004**: Every other route of the underlying service MUST be reachable through the gateway.
- **FR-005**: Responses, streamed chunks, identifiers, usage figures and errors for agent requests MUST have the same shape a plain model produces; usage MUST total all underlying calls made for the request.
- **FR-006**: Agents MUST be defined in files: name, description, instructions, default model, default parameters, whether clients may override parameters, which parameters are locked, tools, handoffs, approval-gated tools, retrieval settings.
- **FR-007**: A client system prompt MUST be combined with the agent's instructions according to the agent's setting (after, before or ignored); after is the default.
- **FR-008**: Client parameters MUST apply when the agent permits overrides, except locked ones.
- **FR-009**: An agent MAY run on a different underlying model when the request asks for it and the agent allows it.
- **FR-010**: Agents with retrieval enabled MUST consult the knowledge catalog before the model's first step of every user turn, and a catalog failure MUST NOT fail the turn.
- **FR-011**: For each tool call the executor MUST be chosen in order: the user's client if it has an equivalent tool; the gateway with request-supplied credentials; the gateway with the user's stored credentials; otherwise the client.
- **FR-012**: Client tools MUST be matched to agent tools by normalized name or explicit alias only.
- **FR-013**: Credentials MUST only ever be used for the user they belong to, MUST be stored encrypted, and MUST NOT appear in logs, errors or answers.
- **FR-014**: A write the gateway runs itself MUST be previewed and approved by the user in the conversation before it runs, and the model MUST NOT be able to bypass or pre-supply the approval.
- **FR-015**: Approval MUST use the client's own question or approval feature when the client offers one, and a single-use code bound to the user, conversation, action and exact arguments otherwise.
- **FR-016**: Approvals MUST expire, and an expired, altered, reused or foreign approval MUST be refused with no effect.
- **FR-017**: A write executed by the client MUST be left to the client's own permission prompt.
- **FR-018**: Agents MUST be able to hand a conversation to another agent, and later turns MUST continue with the agent that holds it.
- **FR-019**: Handoffs, retrieval and gateway-run tool activity MUST be made visible without the client being identified or configured: through instructions to the model and through a neutral reasoning stream.
- **FR-020**: Errors caused by the model MUST be returned to it concisely with how to correct them, up to a bounded number of attempts, then surfaced as a standard error.
- **FR-021**: Transient failures of the underlying model MUST be retried with backoff before surfacing.
- **FR-022**: Every request MUST be authenticated by a personal key; all conversations, approvals and credentials MUST be scoped to that user.
- **FR-023**: Keys MUST be issuable, listable and revocable by an operator, and revocation MUST take effect immediately.
- **FR-024**: The service MUST rate-limit per key and bound request sizes.
- **FR-025**: Conversation state MUST survive a service restart.
- **FR-026**: Concurrent requests on one conversation MUST be serialized.
- **FR-027**: Configuration with errors MUST stop startup with a message naming the file and problem.
- **FR-028**: The service MUST be usable on its own: it MUST NOT depend on any chat product, and the files that make it specific to one organisation MUST be configuration, not code.
- **FR-029**: The service MUST run only OpenAI-style interfaces for now, but its behaviour MUST be reachable through a neutral internal entry so further entry points can be added without changing agent behaviour.

### Key Entities

- **Agent**: a named, configured behaviour (instructions, defaults, tools, handoffs, approval rules, retrieval settings).
- **Key**: a revocable personal credential for the gateway, tied to one user.
- **User credentials**: a person's secrets for external systems, stored encrypted, optional.
- **Conversation**: the visible messages plus hidden work (tool calls the gateway ran, the agent holding the conversation, any pending approval).
- **Pending approval**: a proposed write waiting on the user, bound to user, conversation, action and arguments, with an expiry.
- **Activity note**: a short statement that a handoff, retrieval or tool run happened.
- **Tool route**: the decision of who runs a given tool call.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An unmodified OpenAI SDK client completes a streamed agent chat with only the address and key changed.
- **SC-002**: A conformance suite of recorded requests produces the same response shapes for agents and for plain models, differing only in identifiers and text.
- **SC-003**: A non-agent request through the gateway adds no more than 50 ms median overhead over calling the model directly.
- **SC-004**: For agents with retrieval on, 100% of first answers are produced after a catalog lookup, with zero model-initiated search calls needed.
- **SC-005**: Across a scripted set of model mistakes, 95% of runs end in a correct answer with no user-visible error.
- **SC-006**: No write the gateway runs itself happens without a matching approval in 100% of tested cases, including altered, reused, expired and cross-user codes.
- **SC-007**: In a test with two users, zero cross-user reads of conversations, approvals or credentials.
- **SC-008**: A revoked key is refused within 5 seconds of revocation.
- **SC-009**: A new operator can add an agent by writing one file and restarting, in under 10 minutes.
- **SC-010**: At least four different clients complete the same agent task without per-client configuration of the gateway.

## Assumptions

- The underlying model service is OpenAI-compatible and already exists.
- The knowledge catalog and the tool servers (tickets, wiki) already exist and are reachable from the gateway only.
- Users are individually known to the operator, who issues their keys; self-service sign-up is out of scope.
- The gateway is reached over a network protected by the operator's transport security.
- Out of scope for the first version: Anthropic's message format (so Claude Code cannot connect directly yet), sub-agent delegation, autonomous or scheduled agents, other entry points besides the OpenAI-style one, a gateway-run web search, gateway-built artifacts and file search over uploads.
- Clients that have their own tools (web search, artifacts, tickets) use those; the gateway makes the agent aware of them.
- When a client runs a write itself, the gateway cannot enforce approval and accepts the client's permission prompt as the gate.
- Visible activity depends on the model following short instructions plus a neutral reasoning stream; exact rendering varies by client.
