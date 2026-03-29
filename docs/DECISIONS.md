# Architecture Decisions

## 2026-03-29: Python + PySide6 for desktop chat UI

**Summary:** Chose Python with PySide6 over Java for the multi-provider chat application.

**Rationale:**
- First-class Anthropic and OpenAI SDKs in Python
- PySide6 (LGPL) provides native cross-platform UI without licensing concerns
- Faster iteration for a UI-driven app with planned extensions
- Extension/plugin systems are straightforward in Python

## 2026-03-29: Shared transcript by default for multi-provider conversations

**Summary:** When routing a message to one provider, the full conversation transcript (including other providers' responses) is sent as context.

**Rationale:**
- Enables the core feature: `claude, what do you think about that?` after GPT responds
- Other providers' messages are folded into `user` role messages with attribution to maintain API contract (user/assistant alternation)
- Simpler than maintaining separate isolated contexts per provider

## 2026-03-29: Stateless API with client-side conversation management

**Summary:** Both Claude and OpenAI APIs are stateless — the app stores conversation history and sends the full transcript with each request.

**Rationale:**
- Both APIs work identically (stateless, full-history-per-request)
- Client-side storage gives full control over history, editing, branching
- SQLite for local persistence — simple, no server needed
