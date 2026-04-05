# Plan: Named personas in a single chat

## Status

**Approved.** Next steps on exit from plan mode:
1. Copy this file to `docs/plans/personas.md` (or similar) in the repo so it's versioned alongside the code.
2. Create one GitHub issue per logical stage listed under "Implementation stages" (Stage 1.1 through 3B.3). Each issue references its stage section and carries its test discipline note.
3. Work the issues in order, starting with Stage 1.1.

## Context

Today a chat is 1:1 with the Provider enum — one Claude, one GPT, one Gemini, one Perplexity. The user's primary use case (Italian tutor: one persona plays the conversation partner, another evaluates the user's replies, a third gives word-level translations) requires **multiple independent personas in the same chat, potentially backed by the same provider**, each with its own system prompt and its own visible history.

This plan covers a phased implementation. Each phase is independently shippable and testable. Phase 1 lands the data model and rendering; Phase 2 lands routing; Phase 3 lands UX.

Key design decisions already settled with the user (earlier in this conversation):
- **Persona** is the chosen term (not "participant").
- Same-provider multiplicity is a **first-class requirement** from day one (three Claude personas in one chat is the primary use case, not an edge case).
- Router stays pure. A new **PersonaResolver** layer downstream of Router handles conversation-scoped custom names.
- **Mode per persona** (`inherit` vs `new`) controls whether the persona sees history from before it was added. Stored on the persona row as a `created_at_message_index` integer (None = "since forever"), with one-line support in the visibility filter.
- **Legacy string fields** (`conversation_spend.provider`, `messages.addressed_to`, `messages.pin_target`, `conversation.last_provider`, `conversation.visibility_matrix` keys) are **reinterpreted at read time** as "the default persona for that provider". No aggressive rewrite migrations.
- **Persona system prompts are persistent** — stored on the personas table, never lost to `//limit`. The `//addpersona` command ALSO creates a pinned user-visible note in the transcript so you can see in-chat what personas exist, but the note is decorative — the persona's actual behaviour comes from the table row.

## Semantic decisions (locked in from Codex review)

These are called out separately because they affect multiple phases and must not drift during implementation.

### D1. Synthetic default personas

Every provider has a **synthetic default persona** per conversation. It is not stored in the `personas` table — it exists virtually, with:
- `id = provider.value` (e.g. `"claude"`, `"openai"`)
- `name = provider display name`
- `provider = <the provider>`
- `system_prompt_override = None` (inherits the global `system_prompt_<provider>` config per D6)
- `model_override = None` (inherits the global `<provider>_model` config per D6)
- `color_override = None` (inherits the global `color_<provider>` config per D6)
- `created_at_message_index = None` (full history — the synthetic default always sees everything in its scope)

Provider shorthand prefixes in user input (`claude,`, `gpt,`) **always** resolve to the synthetic default persona for that provider, no matter how many explicit personas exist in the chat. There is no ambiguity error; the synthetic default is always the target when the user uses the bare provider shorthand. If the user wants to address an explicit persona, they use its name (`partner,`).

Legacy conversations behave identically to today because every message implicitly belongs to the synthetic default persona for its provider. No migration required on the message side.

### D2. Persona id scheme (opaque, not slug)

A persona row has three distinct fields:
- `id: str` — **opaque, stable forever**. Format: `"p_<random 8 char base36>"`, generated at creation time. Never derived from the name, never changes.
- `name: str` — display name shown in the UI and transcript ("Evaluator", "Conversation Partner"). Can be renamed freely.
- `name_slug: str` — lowercased, whitespace-normalised version of `name`, used for prefix matching in user input (`partner,` matches `name_slug="partner"`). Unique within a conversation. Regenerated whenever `name` changes.

Message rows reference personas via `persona_id` (the opaque id). Renames never break message linkage. Slug collisions on rename are rejected at edit time with a clear error.

Synthetic default personas use `id = provider.value` (e.g. `"claude"`) as a deliberate exception — those ids are also opaque from the user's perspective but are hardcoded so legacy messages don't need a migration.

### D3. Tombstone, never hard-delete

`//removepersona` sets a `deleted_at` timestamp on the row. The persona disappears from the UI and from routing, but the row stays in the `personas` table so existing messages that reference its `persona_id` can still render with the correct historical label. Exports and re-renders of old chats continue to show the right persona name for messages sent before the removal.

`deleted_at IS NULL` is the filter used by all UI-facing queries (`list_personas`, resolver, dialog, matrix). Renderer/export queries use `list_personas_including_deleted` for labelling.

### D4. PersonaTarget dataclass

```python
@dataclass(frozen=True)
class PersonaTarget:
    persona_id: str
    provider: Provider
```

Every place that currently holds `Provider` in routing/selection/send flow threads `PersonaTarget` instead. `SelectionState.selection` is `list[PersonaTarget]`. `PersonaResolver.resolve()` returns `list[PersonaTarget]`. `SendController` iterates `PersonaTarget`s. Not a tuple.

### D5. Legacy visibility rule inheritance

When a conversation with a legacy `visibility_matrix` (keyed by provider values like `"claude"`, `"openai"`) gains its first explicit persona for a given provider, the legacy rules apply **only to the synthetic default persona** for that provider. Explicit personas start with **full visibility** (no matrix entry) unless the user configures them otherwise.

Same rule for legacy `pin_target` and `addressed_to` values: they target only the synthetic default persona for the named provider.

### D6. Null-means-inherit: uniform rule for override fields

The three **override fields** on the Persona dataclass use exactly the same semantics:

- **`NULL` means inherit**: the persona uses the provider-level default, resolved at send time / render time. Changing the global default in Settings affects every persona whose override is `NULL` on the next resolution. No retroactive re-rendering of past messages.
- **Any non-null value is an override**: the persona pins to that specific value. Global changes in Settings are ignored for this persona. The user has explicitly opted out of the default.

This applies uniformly to:

| Field | `NULL` means | Non-null means |
|---|---|---|
| `system_prompt_override` | Inherit `config.get("system_prompt_<provider>")` | Replace the global provider prompt entirely |
| `model_override` | Inherit `config.get("<provider>_model")` | Pin to this exact model id |
| `color_override` | Inherit the provider's global colour | Render this persona with this colour |

**No empty-string sentinels.** Every resolution site uses `if persona.<field> is not None: … else: <global lookup>`. One rule, no branching in the code, no confusion in the tests.

**`created_at_message_index` is not an override field** — it's a history-scope marker (see the `mode` discussion earlier), conceptually separate from the inherit-vs-override pattern above. Its semantics are:
- `NULL` → the persona sees full history (the natural state for personas created at chat start).
- Non-null integer → the persona sees only messages with index ≥ this value (the natural state for personas added mid-chat with `new` mode).

There is no "provider-level default" to inherit from; the value is purely conversation-local. It's called out here only because it shares the same `NULL` sentinel convention.

### D6b. Shared resolution helpers

To keep the fallback logic in exactly one place and prevent drift across context_builder / send_controller / chat_widget, Phase 2 introduces a small shared module (likely `src/mchat/ui/persona_resolution.py` or a set of helpers on the Persona dataclass itself) with three pure functions:

```python
def resolve_persona_prompt(persona: Persona, config: Config) -> str:
    if persona.system_prompt_override is not None:
        return persona.system_prompt_override
    return config.get(f"system_prompt_{persona.provider.value}")

def resolve_persona_model(persona: Persona, config: Config) -> str:
    if persona.model_override is not None:
        return persona.model_override
    return config.get(f"{persona.provider.value}_model")

def resolve_persona_color(persona: Persona, config: Config) -> str:
    if persona.color_override is not None:
        return persona.color_override
    return config.get(f"color_{persona.provider.value}")
```

Every resolution site (context builder, send controller's model lookup, chat widget's colour lookup, and the dialog's "current effective value" preview in Phase 3A) calls these helpers. **Never duplicate the fallback logic.** Synthetic default personas — which have every override field as `None` — naturally return the global value from each helper, unifying the synthetic-default and explicit-persona code paths.

**The rationale**: inheriting by default is safe (it matches current behaviour for users with no personas), and opting into a specific model/prompt/colour is always a deliberate, visible decision that the user made in the dialog. This keeps the Italian-tutor scenario cheap — `translator` on haiku, `evaluator` on opus, `partner` on sonnet — without needing three separate Provider entries or a confusing global model combo per persona.

**System prompt override is replace-not-merge**: when `system_prompt_override` is non-null, the global provider prompt is **not** prepended in addition. The persona prompt replaces it entirely. The conversation-wide `conv.system_prompt` is still appended afterwards as today. Replace semantics are more predictable than hidden concatenation and match the mental model that "setting a system prompt on the persona means this persona has its own system prompt". The alternative (always concatenate as a baseline) is flagged as FE2 below for future revisit if real usage shows a need.

**Implication for Phase 2 send flow**: `SendController.send_multi` currently reads the model from the panel combo via `host._selected_model(provider_id)`. That becomes a call to `resolve_persona_model(persona, config)` from the shared helper module (D6b). The resolution happens per-send, per-persona, at the moment the `StreamWorker` is created. **Runtime resolution for `model_override` is core Phase 2 work**, not deferred.

**Implication for Phase 2 commands**: `//addpersona` and `//editpersona` in Phase 2 expose `system_prompt_override` only (via the prompt text at the end of the command). They do **not** expose `model_override` or `color_override` in the command path — those are dialog-only concerns, deferred to Phase 3A. Personas created via commands in Phase 2 get `model_override = None` (inherit) as the default. This is a staged UX decision: the data and runtime layers support all override fields from day one, but the command syntax stays simple.

## Command syntax (settled)

```
//addpersona <provider> as "<name>" [inherit|new] <system prompt text>
//editpersona "<name>" <new system prompt text>
//removepersona "<name>"
//personas                       — list all personas in the current chat
```

- `<provider>` is one of the existing PREFIX_TO_PROVIDER tokens (`claude`, `gpt`, `gemini`, `perplexity`, `pplx`).
- `"<name>"` is quoted so names can contain spaces and to disambiguate from the mode keyword. Names are case-insensitive, must be unique within a conversation, and reserved words (`all`, `flipped`, provider shorthands) are rejected at add time.
- `inherit` / `new` are bare keywords, only recognised in the slot immediately after the quoted name. Default: `new` when added mid-chat (the persona starts fresh from the current message count), effectively `inherit` when added at chat start (there's nothing to inherit). A persona created with `new` stores `created_at_message_index = len(conversation.messages)`; a persona created with `inherit` stores `None`.

Addressing from the input line uses the persona name directly, just like providers do today:
```
partner, ciao
evaluator,
translator,
partner, gpt, compare your answers
```

## Phase 1 — Data model + rendering

**Goal**: the data layer can store personas and messages that belong to them. Rendering and export label by persona name when present. Routing is unchanged — every send still goes via `Provider`. No new commands yet. Existing behaviour is identical for legacy conversations because every existing message gets `persona_id = NULL` and falls back to `provider`.

### 1.1 Data model (`src/mchat/models/persona.py`, new)

```python
@dataclass
class Persona:
    conversation_id: int
    id: str                              # opaque, stable forever: "p_<base36>"
    provider: Provider                   # backing provider for API calls
    name: str                            # display name ("Evaluator")
    name_slug: str                       # lowercased slug for prefix matching
    system_prompt_override: str | None   # None = inherit global provider prompt
    model_override: str | None           # None = inherit global provider model
    color_override: str | None           # None = inherit provider colour
    created_at_message_index: int | None # None = full history; int = start from message N
    sort_order: int = 0
    deleted_at: datetime | None = None   # tombstone marker
```

Every `*_override` field uses the same **null-means-inherit** rule (D6). See D2 and D3 for the id/slug/tombstone rationale. A small helper module generates opaque ids (`"p_" + secrets.token_urlsafe(6)[:8].lower()`).

`model_override` is stored in the Phase 1 schema from day one so the data layer is complete even before Phase 2 adds the runtime resolution and Phase 3A adds the UI to edit it. Phase 1 can set it only via direct DB manipulation (tests); the user-facing paths land later.

### 1.2 Message model ([src/mchat/models/message.py](src/mchat/models/message.py))

Add one field:
```python
persona_id: str | None = None
```

`provider` stays — per Codex, keeping it on the row makes rendering/migration/debugging much easier. `persona_id` is the new identity; `provider` is a redundant convenience.

### 1.3 DB schema ([src/mchat/db.py](src/mchat/db.py))

New migration `_migration_2_personas`:

```sql
CREATE TABLE IF NOT EXISTS personas (
    conversation_id INTEGER NOT NULL,
    id TEXT NOT NULL,                    -- opaque, e.g. "p_a3f8k2r9"
    provider TEXT NOT NULL,
    name TEXT NOT NULL,
    name_slug TEXT NOT NULL,             -- for prefix matching
    system_prompt_override TEXT,         -- NULL = inherit global provider prompt
    model_override TEXT,                 -- NULL = inherit global provider model
    color_override TEXT,                 -- NULL = inherit provider colour
    created_at_message_index INTEGER,    -- NULL = inherit full history
    sort_order INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,                     -- NULL = active; ISO-8601 timestamp = tombstoned
    PRIMARY KEY (conversation_id, id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

ALTER TABLE messages ADD COLUMN persona_id TEXT;  -- NULL for legacy rows
```

The three override columns (`system_prompt_override`, `model_override`, `color_override`) are all `NULL`-able with the uniform "NULL means inherit from the global provider default" rule (D6). No `''` sentinels, no branching in the resolution code — everything goes through the shared `resolve_persona_*` helpers in D6b. `created_at_message_index` also uses `NULL` (meaning full history) but is conceptually a history-scope marker, not an inherit-from-provider-default override.

Slug uniqueness (D2 — no two active personas in the same chat share a slug) is enforced via a partial unique index rather than a table-level `UNIQUE` constraint, so tombstoned rows (`deleted_at IS NOT NULL`) are excluded from the uniqueness check:

```sql
CREATE UNIQUE INDEX idx_personas_active_slug
    ON personas (conversation_id, name_slug)
    WHERE deleted_at IS NULL;
```

This lets the user create a new persona with the same name as a previously-removed one without tripping on the old tombstoned row.

`CURRENT_SCHEMA_VERSION` bumps to 2. The migration is purely additive — no rewrite of legacy provider strings, no change to `conversation_spend` PK. Everything that currently keys by `provider.value` keeps working.

New DB helpers:
- `create_persona(persona: Persona) -> Persona`
- `list_personas(conv_id: int) -> list[Persona]` — active only (`deleted_at IS NULL`)
- `list_personas_including_deleted(conv_id: int) -> list[Persona]` — used by renderer/export so tombstoned personas still get their historical labels
- `update_persona(persona: Persona) -> None`
- `tombstone_persona(conv_id: int, persona_id: str) -> None` — sets `deleted_at`, never hard-deletes (D3)
- `add_message` and `get_messages` carry `persona_id`.

### 1.4 Rendering ([src/mchat/ui/message_renderer.py](src/mchat/ui/message_renderer.py), [src/mchat/ui/html_exporter.py](src/mchat/ui/html_exporter.py))

- Label lookup becomes: "if `msg.persona_id` is set, look it up via `db.list_personas_including_deleted(conv_id)` and use `persona.name`; else fall back to provider display name". Tombstoned personas still resolve so historical messages keep their original labels in transcripts and exports. The existing `_label_for` helpers in html_exporter and the renderer's "X's take:" heading path take a small helper function.
- **Group detection** in `MessageRenderer.display_messages` switches its equality key from `msg.provider` to `(msg.persona_id or msg.provider.value)`. Two messages with the same persona_id collapse into one column-group slot; two messages with distinct persona_ids (even if same backing provider) render as separate columns. Solves Codex's same-provider grouping concern.
- Colour lookup per D6: if `persona.color_override is not None`, use that; otherwise fall back to the existing provider colour lookup (`self._colors[provider.value]`). No changes to colour storage; the override is read from the personas table at render time. The actual wiring of this into `ChatWidget._color_for` is Phase 3A work (the field exists in the Phase 1 schema; the resolution happens in 3A to avoid touching chat_widget.py in two separate phases).

### 1.5 Tests

- `test_db.py::TestPersonas` — round-trip for each CRUD helper, cascade delete with conversation, migration idempotency.
- `test_message_renderer.py` — add cases: two messages with distinct `persona_id` but same provider render as two columns; persona-labelled messages show the persona name; legacy messages (`persona_id=None`) render unchanged.
- `test_html_exporter.py` — persona name in export labels.

Phase 1 ships without any user-visible change. The data model exists, rendering is persona-aware, but no code path sets `persona_id` on a message yet.

## Phase 2 — PersonaResolver + routing + commands

**Goal**: `//addpersona` / `//editpersona` / `//removepersona` / `//personas` work. Personas are addressable by name from the input line. Selection state, visibility matrix, and pinning all accept persona identifiers.

### 2.1 PersonaResolver ([src/mchat/ui/persona_resolver.py](src/mchat/ui/persona_resolver.py), new)

Runs downstream of Router. Takes:
- The user input text.
- The current conversation's active persona list (via `db.list_personas(conv_id)`).
- The Router instance (for delegating provider-shorthand parsing).

Returns `tuple[list[PersonaTarget], str]` — the list of targets and the cleaned message text.

Resolution rules (per D1):

1. **Explicit persona name prefix** (`partner,` when an active persona has `name_slug == "partner"`) → `PersonaTarget(persona_id=<that persona's opaque id>, provider=<that persona's provider>)`. Reserved words (`all`, `flipped`) and provider shorthands (`claude`, `gpt`, `gemini`, `perplexity`, `pplx`) are rejected as persona names at creation time, so there is never a collision between a persona name and a provider shorthand.

2. **Provider shorthand prefix** (`claude,`) → **always** resolves to the synthetic default persona for that provider: `PersonaTarget(persona_id="claude", provider=Provider.CLAUDE)`. This holds regardless of how many explicit Claude personas exist in the chat. The synthetic default is never ambiguous.

3. **`all,`** → every active persona in the current chat, expanded in stable order (sort_order then name). If no explicit personas exist, this falls back to the synthetic defaults for every configured provider — identical to today's behaviour.

4. **`flipped,`** → the complement of the current selection, computed over the same active-persona set.

5. **Multi-prefix** (`partner, evaluator,`) → the resolver iterates prefixes greedily, matching each token against (in order) persona names, then provider shorthands, then reserved words. Stops at the first token that matches none of these. The remainder is the message text.

Router stays pure. The resolver runs a pre-pass that strips leading persona-name prefixes and collects their PersonaTargets, then hands the remaining text to Router for any provider-shorthand prefixes (which also produce PersonaTargets, via the synthetic default rule).

**Implementation note on synthetic defaults**: the resolver does not query the database for synthetic defaults. It constructs them on the fly from the Provider enum. A helper `synthetic_default(provider: Provider) -> PersonaTarget` returns `PersonaTarget(persona_id=provider.value, provider=provider)`. Every downstream consumer (context builder, renderer, visibility filter, spend) handles `persona_id = provider.value` as a known sentinel that means "synthetic default for that provider" — no table row needed.

### 2.2 ProviderSelectionState → SelectionState ([src/mchat/ui/state.py](src/mchat/ui/state.py))

Rename and generalise: `list[Provider]` → `list[PersonaTarget]` (D4). `PersonaTarget` is a frozen dataclass defined in the same module or in a small new `persona_target.py`. Existing call sites that read `.selection` get back a list of `PersonaTarget`s; `PersonaTarget.provider` gives them the backward-compatible Provider enum for anything that still needs it. The state object also gains a helper `providers_only() -> list[Provider]` for code that genuinely only cares about which providers are active (e.g. the send fan-out in SendController still needs a Provider for the StreamWorker instantiation).

Signal name stays `selection_changed` but the signal payload becomes `list[PersonaTarget]`.

### 2.3 Context building ([src/mchat/ui/context_builder.py](src/mchat/ui/context_builder.py))

`build_context(conv, persona_target, db, config) -> list[Message]`:

1. **System prompt block**, emitted first, before any history:
   - Resolve the persona's effective prompt via `resolve_persona_prompt(persona, config)` (the shared helper from D6b). This returns the persona's `system_prompt_override` if it is not `None`, otherwise the global `system_prompt_<provider>`. Synthetic default personas (no table row, all override fields `None`) naturally fall through to the global value.
   - Conversation-wide `conv.system_prompt` appended after.
2. **//limit slice** — unchanged.
3. **Pin rescue** — unchanged; `pin_target` values are now interpreted via the resolver (a persona name is a valid target, a provider shorthand resolves to the synthetic default persona for that provider per D1).
4. **Persona history cutoff**: if the persona has a non-null `created_at_message_index`, messages before that index are dropped unconditionally (this runs *after* the `//limit` slice, so `//limit` and the persona cutoff stack).
5. **Visibility filter** — same logic as today, but the matrix key is `persona_id`, not `provider.value`. Legacy matrix entries keyed by provider values are resolved at read time to the synthetic default persona for that provider (D5 — no data rewrite).
6. **Prefix stripping** — unchanged.

### 2.4 Commands

**Scope note**: The Phase 2 command path edits **only `system_prompt_override`**. `model_override` and `color_override` exist in the schema (Phase 1) and are honoured at runtime (Phase 2 send flow and Phase 3A render), but editing them is dialog-only (Phase 3A). Personas created via `//addpersona` always start with `model_override = None` and `color_override = None`, inheriting the global provider defaults. This is a deliberate staging decision to keep the command syntax simple; users who want per-persona models or colours wait for the Phase 3A dialog.

**New handlers** in [src/mchat/ui/commands/personas.py](src/mchat/ui/commands/personas.py):

- `handle_addpersona(arg, host)`
  - Parse: `<provider> as "<name>" [inherit|new] <prompt>`.
  - Validate: provider is known, name is non-empty and not reserved (rejects `all`, `flipped`, and every PREFIX_TO_PROVIDER key), no active persona with the same slug already exists in the conversation.
  - Compute `created_at_message_index`: explicit if `inherit`/`new` given, else default (`new` mid-chat, `None` at chat start).
  - Insert persona row via `db.create_persona` with an opaque id (`p_<base36>`), `system_prompt_override = <prompt>` (empty/whitespace prompt → `None`, meaning "inherit the global provider prompt" per D6), `model_override = None`, `color_override = None`. Model and colour overrides are dialog-only (Phase 3A), not exposed in the command path per D6's staging decision.
  - Create a pinned user-visible note in the transcript (`Message(role=USER, pinned=True, pin_target="all", content=f'Added persona "{name}" ({provider}, {mode}): {prompt}')`) so the setup is visible in the chat history and survives `//limit`. The pinned message is informational — the persona's actual behaviour comes from the table row, not from the note.
  - Re-render chat.

- `handle_editpersona(arg, host)`
  - Parse: `"<name>" <new prompt text>`.
  - Update the persona row's `system_prompt_override` in place (other override fields — model, colour — are not editable from the command line; those require the Phase 3A dialog).
  - Create a second pinned note (`Edited persona "<name>": <new prompt>`) so the change is visible in the transcript.

- `handle_removepersona(arg, host)`
  - Parse: `"<name>"`.
  - Tombstone the persona via `db.tombstone_persona(conv_id, persona_id)` — sets `deleted_at` to the current timestamp (D3). Existing messages with `persona_id = <that id>` are not touched; the renderer uses `list_personas_including_deleted` so historical labels still resolve correctly.
  - Create a pinned note (`Removed persona "<name>"`).

- `handle_personas(host)` — list all **active** personas in the current chat, including their mode, provider, and a snippet of their system prompt. Same style as `//pins`. Tombstoned personas are not listed.

Dispatch in [src/mchat/ui/commands/__init__.py](src/mchat/ui/commands/__init__.py) adds four entries.

### 2.5 Send flow integration

[src/mchat/ui/send_controller.py](src/mchat/ui/send_controller.py) picks up the resolver:

1. Before `router.parse()`, call `persona_resolver.resolve(text, conv, router)` which returns `(targets: list[PersonaTarget], cleaned_text: str)` (D4).
2. Each element of `targets` has both `persona_id` and `provider`, so the existing per-target loop that builds context, starts workers, stores responses, all continues to work — it just threads `PersonaTarget` instead of `Provider`.
3. **Model resolution per D6**: for each target, the worker is started with `model = persona.model_override if persona.model_override is not None else config.get(f"{persona.provider.value}_model")`. For synthetic default personas (`persona_id == provider.value`, no table row), there is no `model_override` to consult and the global provider model is used directly — behaviour identical to today.
4. `addressed_to` on the stored user message becomes a comma-separated list of persona ids (not provider values). Legacy "all" special case is preserved.
5. On response completion, the persisted Message gets `persona_id = target.persona_id` and `provider = target.provider`.

### 2.6 Visibility matrix, pinning, last_provider, spend

All of these are keyed by string. Interpret at read time:
- **Matrix key** is a persona id. Legacy matrices with provider-value keys are interpreted as "rule for the default persona of that provider".
- **`pin_target`** accepts persona ids. Legacy pins with provider-value targets keep working via the default-persona interpretation.
- **`addressed_to`** same treatment.
- **`conversation_spend.provider`** stays as provider.value — spend is genuinely per-provider (it's what you're billed for). Per-persona spend breakdown is a Phase 3 nice-to-have and would be a separate column, not a PK change.
- **`conversation.last_provider`** gets renamed to `last_selection` and stores comma-separated persona ids, falling back to provider values for legacy rows.

### 2.7 Tests

- `test_persona_resolver.py` — disambiguation, collision, legacy fallback, `all,`/`flipped,` expansion, mixed persona+provider prefixes.
- `test_context_builder.py` — persona system prompt takes precedence; `created_at_message_index` cuts off inherit=new personas; matrix still works with persona keys; legacy matrix still works with provider-value keys.
- `test_commands_personas.py` — add/edit/remove/list round-trips; reserved-word rejection; uniqueness check; pinned-note creation.
- `test_send_controller.py` (new) — smoke test that a send through a persona produces a message with the correct `persona_id` and that its context excludes messages before its `created_at_message_index`.

Phase 2 ships the feature as text-command-driven. No UI beyond the commands themselves.

## Phase 3A — Dialog + colour sync (low-risk polish)

**Goal**: personas are discoverable and editable via a dialog, and their colours render correctly. The bar and matrix still show providers. This phase ships even if we later decide not to do Phase 3B.

### 3A.1 Persona editor dialog ([src/mchat/ui/persona_dialog.py](src/mchat/ui/persona_dialog.py), new)

Modal dialog reached from a new "Personas..." sidebar action on the current chat. Lists active personas; lets the user create, edit, rename, remove (tombstone), and reorder them. Fields per persona: name, provider, system prompt (`system_prompt_override`), model (`model_override` — dropdown populated from the ModelCatalog for the persona's provider, with a "— use global default —" sentinel for `None`), colour (`color_override` — colour picker with a "— use provider colour —" sentinel for `None`), mode (inherit/new at creation time only — editing doesn't retroactively change history), sort order. Clicking "Add persona" at the bottom of the list opens the same form pre-filled with defaults (all overrides `None`).

The dialog is strictly an alternative UX for the data-layer operations landed earlier. Service methods called from the dialog are the same ones the Phase 2 commands use, plus new ones for `model_override` and `color_override` editing that didn't exist at the command layer. There's one code path per operation.

For each field the dialog also displays the **currently effective value** beside the override input, computed via the shared helpers `resolve_persona_prompt`/`resolve_persona_model`/`resolve_persona_color` (D6b). This makes "inherit" semantics concrete — the user can see "current prompt: <whatever the global provider prompt is>" right next to the (empty) override field.

### 3A.2 Chat view colour assignment ([src/mchat/ui/chat_widget.py](src/mchat/ui/chat_widget.py))

Extend the `_color_for(message)` path to call `resolve_persona_color(persona, config)` from D6b when `message.persona_id` is set (and looks up to an active or tombstoned persona via `list_personas_including_deleted`). Synthetic default personas have `color_override = None` so the helper returns the global provider colour — identical to today's behaviour for messages without explicit personas.

`ChatWidget` receives a small `PersonaColorResolver` helper through `ServicesContext` alongside config/db. The resolver caches per-conversation persona lookups and invalidates on persona add/edit/remove to avoid hitting the DB on every repaint.

### 3A.3 Tests

- `test_persona_dialog.py` (pytest-qt) — round-trip of the dialog against a fresh conversation; create/edit/tombstone/reorder.
- `test_chat_widget.py` — colour override takes precedence over provider colour when persona has one.

## Phase 3B — Panel expansion (higher UX risk, ship only if Phase 3A proves the feature)

**Goal**: the provider bar and matrix panel become persona-aware. This is the most visible change and carries the most UX risk (crowded bars, weird spend display, matrix grid growing unbounded). Kept separate so the decision to ship it can be made after real usage of Phase 3A.

### 3B.1 ProviderPanel → SelectionPanel ([src/mchat/ui/provider_panel.py](src/mchat/ui/provider_panel.py))

The bar becomes one row per **active persona** in the current chat. Spend is per-provider under the hood but displayed per persona by sharing the provider's total among its personas (shown in italics with a note that it's shared). Rebuilt on conversation switch and on persona add/remove/edit.

If a chat has only synthetic default personas (no explicit `//addpersona`), the bar shows exactly what it shows today — one row per configured provider, using the synthetic default id as the row key. The visual layout is unchanged for the common case.

Collapse behaviour: if more than 4 personas are active, the bar switches to a compact mode (icons + truncated names, with a popover for details). This needs a mockup before implementation; it's the riskiest UX piece in the plan.

### 3B.2 MatrixPanel keyed by persona ([src/mchat/ui/matrix_panel.py](src/mchat/ui/matrix_panel.py))

The N×N visibility grid rebuilds with N = active-persona count. Same underlying logic, just resolving rows and columns from `db.list_personas(conv.id)` instead of `list(Provider)`. Matrix entries are stored keyed by persona id in new conversations. Legacy matrices keyed by provider values continue to work via D5 — only the synthetic default rows inherit them.

Scroll-into-view when N > 4 to keep the panel's footprint bounded.

### 3B.3 Tests

- `test_matrix_panel.py` — rebuild with persona-count rows including synthetic defaults.
- `test_provider_panel.py` — rebuild with mixed synthetic-default and explicit personas; spend display.
- Updates to `test_main_window.py` to use personas in one smoke test.

## Files to modify

| Phase | File | Nature |
|-------|------|--------|
| 1 | [src/mchat/models/persona.py](src/mchat/models/persona.py) | **new** — dataclass + opaque id helper |
| 1 | [src/mchat/models/message.py](src/mchat/models/message.py) | add `persona_id` field |
| 1 | [src/mchat/db.py](src/mchat/db.py) | migration 2, CRUD helpers (incl. `list_personas_including_deleted`, `tombstone_persona`), add_message/get_messages carry `persona_id` |
| 1 | [src/mchat/ui/message_renderer.py](src/mchat/ui/message_renderer.py) | label by persona name, group by `(persona_id or provider.value)` |
| 1 | [src/mchat/ui/html_exporter.py](src/mchat/ui/html_exporter.py) | label by persona name (incl. tombstoned personas) |
| 1 | `tests/test_db.py`, `tests/test_message_renderer.py`, `tests/test_html_exporter.py` | new cases |
| 2 | [src/mchat/ui/persona_target.py](src/mchat/ui/persona_target.py) | **new** — PersonaTarget frozen dataclass + synthetic_default helper |
| 2 | [src/mchat/ui/persona_resolver.py](src/mchat/ui/persona_resolver.py) | **new** |
| 2 | [src/mchat/ui/state.py](src/mchat/ui/state.py) | ProviderSelectionState → SelectionState, now `list[PersonaTarget]` |
| 2 | [src/mchat/ui/context_builder.py](src/mchat/ui/context_builder.py) | persona system prompt, `created_at_message_index` cutoff, matrix keying by persona_id |
| 2 | [src/mchat/ui/commands/personas.py](src/mchat/ui/commands/personas.py) | **new** — add/edit/remove/list |
| 2 | [src/mchat/ui/commands/__init__.py](src/mchat/ui/commands/__init__.py) | dispatch entries, help text |
| 2 | [src/mchat/ui/commands/help.py](src/mchat/ui/commands/help.py) | examples showing `//addpersona ... as "name" inherit ...` syntax |
| 2 | [src/mchat/ui/send_controller.py](src/mchat/ui/send_controller.py) | resolver integration, persona_id on new messages |
| 2 | [src/mchat/ui/visibility.py](src/mchat/ui/visibility.py) | persona-id keying with legacy-provider-value fallback (D5) |
| 2 | `tests/test_persona_resolver.py`, `tests/test_context_builder.py`, `tests/test_commands_personas.py`, `tests/test_send_controller.py` | new |
| 3A | [src/mchat/ui/persona_dialog.py](src/mchat/ui/persona_dialog.py) | **new** — modal editor |
| 3A | [src/mchat/ui/chat_widget.py](src/mchat/ui/chat_widget.py) | colour lookup with persona override layer |
| 3A | [src/mchat/ui/sidebar.py](src/mchat/ui/sidebar.py) | "Personas..." action |
| 3A | `tests/test_persona_dialog.py`, `tests/test_chat_widget.py` | new/updated |
| 3B | [src/mchat/ui/provider_panel.py](src/mchat/ui/provider_panel.py) | rebuild per-persona, compact mode for N>4 |
| 3B | [src/mchat/ui/matrix_panel.py](src/mchat/ui/matrix_panel.py) | persona-keyed grid, scrollable for large N |
| 3B | `tests/test_matrix_panel.py`, `tests/test_provider_panel.py`, `tests/test_main_window.py` | new/updated |

## Implementation stages

The four phases above describe **what** gets built in each stage. This section describes **how** — the concrete commit sequence, the test-first workflow at each step, and the points where work can pause and ship independently.

### Test discipline (applies to every stage)

The repo's existing workflow (from `CLAUDE.md`) is test-first, test-committed-separately. Every stage in the sequence below follows the same pattern unless explicitly noted:

1. **Write failing tests** for the new behaviour. Run the suite and confirm the new tests fail for the *right reason* (not a typo, not an import error). Commit the failing tests as a standalone commit tagged `[personas/<stage>.tests]`.
2. **Implement** the minimum code to make those tests pass. Do not touch the test files in this step. Commit as `[personas/<stage>]`.
3. **Run the full suite**, not just the new file. Every pre-existing test must still pass. If anything else broke, the implementation is wrong — fix it in a third commit before moving on.

This means **every stage in the list below is actually two commits in git**: `[personas/<stage>.tests]` then `[personas/<stage>]`. The line count estimates at the bottom of this section count implementation LOC only; test LOC is separate.

The ordering rule for the **implementation** commit: **every commit must leave the suite green and the app runnable**. A failing-tests commit is the only exception — its job is to prove the new tests fail meaningfully before the implementation lands.

### Stages that are NOT test-first

A handful of stages don't fit the failing-tests-first pattern cleanly:

- **Stage 1.1 (`Persona` dataclass)** — pure data class with no behaviour. Tests confirm field defaults and a `generate_persona_id()` helper's output format, but they're trivial. Still committed alongside the dataclass in one commit, not split.
- **Stage 2.4 (`ProviderSelectionState` → `SelectionState` rename)** — this is a refactor, not a behaviour change. Existing tests update in the same commit as the rename. No new failing tests; the pre-existing tests are the guarantee.
- **Stage 3A.3 (sidebar context-menu action)** — trivial wiring; the smoke test lives with the dialog tests in 3A.1.

Every other stage is test-first.

### Stage 1 — Phase 1: data layer

Goal: personas can be stored, loaded, and carried on messages. Nothing routes through personas yet; existing users see no change.

**Stage 1.1 — Persona dataclass** *(not split, see exceptions)*
- **Code**: new `src/mchat/models/persona.py` with the `Persona` dataclass (matching §1.1 schema) and a `generate_persona_id()` helper producing `p_<8 base36 chars>`.
- **Tests**: `tests/test_persona_model.py` — default values, id format, slug helper on a few sample names. ~5 tests.
- **Commit**: single commit, no test-first split.

**Stage 1.2 — DB migration 2 + CRUD** *(test-first)*
- **Tests first**: `tests/test_db.py::TestPersonas` — round-trip for every CRUD helper; tombstone never hard-deletes; cascade delete with conversation; partial unique index prevents active-slug collisions but allows reuse of tombstoned slugs; re-running migration on an already-migrated DB is a no-op; `add_message`/`get_messages` carry `persona_id`; legacy messages (`persona_id=NULL`) continue to load correctly. ~15 tests.
- **Code**: `src/mchat/db.py` — `_migration_2_personas` (CREATE TABLE + CREATE UNIQUE INDEX + ALTER TABLE messages ADD COLUMN persona_id); new helpers `create_persona`, `list_personas`, `list_personas_including_deleted`, `update_persona`, `tombstone_persona`; update `add_message` / `get_messages` signatures. Bump `CURRENT_SCHEMA_VERSION = 2`.

  **Rollback point**: after this commit, the app runs identically to today. Any existing conversation loads and saves correctly. Personas table is empty. This is the natural "abort the feature" point if the product decision is reversed.

**Stage 1.3 — Rendering by persona** *(test-first)*
- **Tests first**: `tests/test_message_renderer.py` — two messages with distinct `persona_id` but same provider render as separate column-groups; messages with `persona_id` show the persona name in their label; legacy messages (`persona_id=None`) render unchanged; tombstoned personas still resolve their historical labels (via `list_personas_including_deleted`). `tests/test_html_exporter.py` — persona name appears in export labels. ~10 tests.
- **Code**: `src/mchat/ui/message_renderer.py` — group-detection key changes from `msg.provider` to `(msg.persona_id or msg.provider.value)`; label helper accepts a persona lookup function. `src/mchat/ui/html_exporter.py` — label helper mirrors the renderer change.

  **State after Stage 1**: the data model is complete, rendering handles persona labels, no user-facing change has shipped. Phase 1 is done. Suite at ~210 tests (Stage 1 adds ~30 new tests across three stages).

### Stage 2 — Phase 2: resolution + routing + commands

Goal: personas become addressable via commands and user input. This is the biggest phase; split into sub-stages to keep each commit small.

**Stage 2.1 — Resolution helpers (D6b)** *(test-first)*
- **Tests first**: `tests/test_persona_resolution.py` — for each of `resolve_persona_prompt` / `resolve_persona_model` / `resolve_persona_color`: (a) persona with override returns the override; (b) persona with `None` returns the global config value; (c) synthetic default persona (no table row, all overrides `None`) returns the global value identically. ~9 tests.
- **Code**: new `src/mchat/ui/persona_resolution.py` with three pure functions. No Qt, no state. Nothing calls these yet — library add.

**Stage 2.2 — PersonaTarget dataclass** *(test-first)*
- **Tests first**: `tests/test_persona_target.py` — frozen dataclass equality, `synthetic_default(provider)` returns `PersonaTarget(persona_id=provider.value, provider=provider)`. ~4 tests.
- **Code**: new `src/mchat/ui/persona_target.py` with the dataclass + helper. Nothing calls it yet.

**Stage 2.3 — PersonaResolver** *(test-first)*
- **Tests first**: `tests/test_persona_resolver.py` — every D1 rule: explicit name match (`partner,`); provider shorthand (`claude,`) resolves to synthetic default even when explicit Claude personas exist; `all,` expands to every active persona plus synthetic defaults for providers with none; `flipped,` complement; multi-prefix iteration (`partner, evaluator,`); reserved-word rejection (a persona cannot be named `all`, `flipped`, `claude`, etc.); legacy conversations with no personas fall back to synthetic defaults for provider shorthands; unknown names produce an explicit error; empty input. ~20 tests.
- **Code**: new `src/mchat/ui/persona_resolver.py`. Pure function `resolve(text, conv, router) → (list[PersonaTarget], str)`. No Qt, no state, trivially testable.

**Stage 2.4 — ProviderSelectionState → SelectionState rename** *(refactor, not test-first)*
- **Tests**: `tests/test_state.py::TestProviderSelectionState` is renamed to `TestSelectionState` and updated to construct `PersonaTarget` instances. No new failing tests — the existing behaviour-level assertions are the regression guarantee. Any test that constructed `ProviderSelectionState([Provider.CLAUDE])` now says `SelectionState([PersonaTarget("claude", Provider.CLAUDE)])`.
- **Code**: `src/mchat/ui/state.py` — rename the class, change the payload from `list[Provider]` to `list[PersonaTarget]`, add `providers_only()` helper. Every call site updates in the same commit. Signal name stays `selection_changed` to preserve wiring.

  This is the riskiest Stage 2 commit because it's the widest diff. The signal-based selection fan-out from #58 means the wiring is already loose, but every place that reads `.selection` has to learn the new payload shape. If it gets messy, split into two commits: (a) introduce `PersonaTarget` alongside `Provider` (both accepted transitionally), (b) flip everything to `PersonaTarget`-only.

**Stage 2.5 — context_builder personas** *(test-first)*
- **Tests first**: `tests/test_context_builder.py` — target parameter type is `PersonaTarget`; `resolve_persona_prompt` is called for the system-prompt block; non-null `created_at_message_index` slices prior history correctly; matrix lookup keys by `persona_id`; a legacy matrix keyed by `"claude"` continues to filter the synthetic default Claude persona per D5; a persona with `model_override` set directly in the DB produces context identical to one without (model is a send-time concern, not a context-building one — context_builder doesn't emit it). ~12 tests.
- **Code**: `src/mchat/ui/context_builder.py` — swap `target: Provider` for `target: PersonaTarget`, use the D6b helpers for prompt resolution, add the `created_at_message_index` cutoff step after the `//limit` slice, update visibility-matrix filtering to key by `persona_id`.

**Stage 2.6 — send_controller personas** *(test-first)*
- **Tests first**: `tests/test_send_controller.py` — a send through PersonaResolver produces a persisted message with the expected `persona_id` and `provider`; `resolve_persona_model` is called for the StreamWorker model; a persona with `model_override` set to haiku sends with haiku even if the global Claude model is sonnet; synthetic default personas use the global model. `tests/test_main_window.py` — add one persona-aware smoke test. ~10 tests.
- **Code**: `src/mchat/ui/send_controller.py` — pipe inputs through `PersonaResolver` before `router.parse`, thread `PersonaTarget` through the send loop, call `resolve_persona_model(persona, config)` for `StreamWorker`, tag persisted messages with `persona_id` and `provider`. The prefix-only selection path (fixed in #60) stays correct because `PersonaResolver` runs before `router.parse` — the pre-parse-selection snapshot pattern still applies.

**Stage 2.7 — visibility filter personas** *(test-first)*
- **Tests first**: `tests/test_visibility.py` — add cases: matrix keyed by persona id filters correctly; legacy matrix keyed by provider value continues to apply, but **only** to the synthetic default persona for that provider (per D5); explicit personas start with full visibility unless explicitly restricted; `addressed_to` strings containing persona ids work; legacy `addressed_to` values containing provider values continue to work via the synthetic-default fallback. ~10 tests.
- **Code**: `src/mchat/ui/visibility.py` — update `filter_for_provider` → `filter_for_persona` (or similar rename) to key by `persona_id` with the D5 legacy fallback.

**Stage 2.8 — persona commands** *(test-first)*
- **Tests first**: `tests/test_commands_personas.py` — `//addpersona` round-trip (parse, create row, create pinned note, re-render); `//addpersona` rejects reserved names (`all`, `flipped`, every provider shorthand); `//addpersona` rejects duplicate slugs in the same chat; `//addpersona` honours `inherit`/`new` and the default (`new` mid-chat, `None` at chat start); empty prompt → `system_prompt_override = None`; `//editpersona` updates only `system_prompt_override`; `//removepersona` tombstones but does not hard-delete; `//personas` lists active personas only; tombstoned personas still label historical messages after removal. ~15 tests.
- **Code**: new `src/mchat/ui/commands/personas.py` with `handle_addpersona` / `handle_editpersona` / `handle_removepersona` / `handle_personas`. Dispatch entries in `commands/__init__.py`. Help text update in `commands/help.py` with an Italian-tutor worked example to teach the syntax.

  **State after Stage 2**: the feature works end-to-end via commands. The Italian-tutor scenario is fully executable from the command line. Phase 2 is done. Suite at ~290 tests (Stage 2 adds ~80 new tests across eight stages).

  **Rollback point**: Stage 2 commits are reversible individually except for 2.4 (the state rename), which is hard to un-ship once downstream code expects `PersonaTarget`. If the feature is in doubt at 2.4, pause there.

### Stage 3 — Phase 3A: dialog + colour wiring

Goal: personas are editable via a dialog, including model and colour overrides (which the command layer doesn't expose). Chat widget colours resolve through the persona layer.

**Stage 3A.1 — Persona editor dialog** *(test-first with pytest-qt)*
- **Tests first**: `tests/test_persona_dialog.py` — dialog opens against a fresh conversation; create a persona via the form → DB row appears with correct values; edit the system prompt → row updates; edit the model override → row updates and runtime resolution picks the new value on the next simulated send; edit the colour override → row updates; remove → row tombstones; reorder → `sort_order` updates; the "currently effective value" label next to each override input displays the correct resolved value via `resolve_persona_*` (verifies that `None` shows the global value, a set override shows the override). ~12 pytest-qt tests.
- **Code**: new `src/mchat/ui/persona_dialog.py`. The dialog's service methods for add/edit/tombstone reuse the ones behind the Phase 2 commands; the new dialog-only methods are for `model_override` and `color_override` editing.

**Stage 3A.2 — Chat widget colour resolution** *(test-first with pytest-qt)*
- **Tests first**: `tests/test_chat_widget.py` — messages from a persona with `color_override` render in that colour; messages from a persona with `color_override = None` render in the provider default; legacy messages without `persona_id` render in the provider default (unchanged from today); `PersonaColorResolver` cache invalidates when a persona's colour override changes. ~6 pytest-qt tests.
- **Code**: `src/mchat/ui/chat_widget.py` — wire `_color_for` through `resolve_persona_color`. Introduce `PersonaColorResolver` helper with per-conversation caching and cache invalidation on persona-change signals.

**Stage 3A.3 — Sidebar action** *(not test-first — trivial wiring)*
- **Tests**: updated `tests/test_sidebar.py` (pytest-qt) — context menu on a conversation item shows "Personas..." and triggers the dialog's open callback. ~2 tests.
- **Code**: `src/mchat/ui/sidebar.py` — add the context-menu action. Single commit.

  **State after Stage 3**: the feature is fully usable by non-power-users. Phase 3A is done. Suite at ~310 tests (Stage 3A adds ~20 new tests).

### Stage 4 — Phase 3B: panel expansion (optional)

Goal: the provider panel and visibility matrix become persona-aware. This stage is **optional** — it ships only if Stage 3's usage shows the provider-bar layout is actually confusing for multi-persona chats. Otherwise, the Phase 3A dialog is enough.

**Stage 3B.1 — Provider panel → Selection panel** *(test-first with pytest-qt)*
- **Tests first**: `tests/test_provider_panel.py` — bar renders one row per active persona; synthetic-default-only chats render exactly as today (regression guard); bar rebuilds on persona add/edit/remove signals; compact mode kicks in when N > 4. ~8 pytest-qt tests.
- **Code**: `src/mchat/ui/provider_panel.py` — rebuild per active persona.

**Stage 3B.2 — Matrix panel persona keying** *(test-first with pytest-qt)*
- **Tests first**: `tests/test_matrix_panel.py` — N×N grid has N = active persona count; rebuild on persona add/remove; scrollable when N > 4; legacy matrices keyed by provider values still apply to synthetic defaults per D5. ~6 pytest-qt tests.
- **Code**: `src/mchat/ui/matrix_panel.py` — persona-keyed grid.

**Stage 3B.3 — Main window smoke** *(test update)*
- **Tests**: `tests/test_main_window.py` — add a smoke test that exercises the full persona flow end-to-end through the UI: open the dialog, create three personas, verify the panel and matrix rebuild, simulate sending to each. ~3 pytest-qt tests.
- **Code**: none, or trivial wiring to make the smoke test pass.

  **State after Stage 4**: the feature is fully integrated into the window chrome. Suite at ~330 tests (Stage 3B adds ~17 new tests).

### Decision checkpoints

Between stages, there are natural "should we continue?" moments:

- **After Stage 1.2** (DB migration landed, nothing uses it yet): cheapest possible pause. Abandon by reverting the commit. Zero user-visible change.
- **After Stage 1.3** (rendering persona-aware, still no user-visible change): data layer and render layer both done. Pause here if the product direction shifts — personas won't exist yet but the plumbing is ready.
- **After Stage 2.8** (feature works via commands): this is the smallest shippable useful state. Real users can use `//addpersona` to solve the Italian-tutor scenario. Stop here if Phase 3A/3B aren't worth the effort.
- **After Stage 3A.3** (dialog ships): the feature has both power-user (command) and normal-user (dialog) entry points. Model and colour overrides are accessible. Likely the actual release target.
- **Stage 4 is conditional** — only ship if post-3A usage reveals a need.

### Commit message conventions for this feature

Each commit in this sequence should have a tag prefix identifying its stage — makes the plan easy to track against `git log`:

- `[personas/1.1]` through `[personas/1.3]` — Phase 1 stages
- `[personas/2.1]` through `[personas/2.8]` — Phase 2 stages
- `[personas/3a.1]` through `[personas/3a.3]` — Phase 3A stages
- `[personas/3b.1]` through `[personas/3b.3]` — Phase 3B stages (optional)

Commit bodies describe what changed; the tag identifies where in the sequence the change lives.

### Total budget estimate

Every stage except the three noted exceptions (1.1, 2.4, 3A.3) is **two commits** in git: a failing-tests commit followed by the implementation commit. Stage counts below are logical stages; multiply by roughly 1.8 for actual commit count.

Based on the effort profile of the state-layer refactor (issues #55–#58, which landed in ~12 commits and ~30 new tests):

| Stage | Logical stages | Git commits | New tests | Implementation LOC | Notes |
|---|---|---|---|---|---|
| 1 (Phase 1) | 3 | ~5 | ~30 | ~250 | 0 breaking changes |
| 2 (Phase 2) | 8 | ~15 | ~80 | ~500 | 1 rename with wide blast radius (2.4) |
| 3A (Phase 3A) | 3 | ~5 | ~20 | ~400 | 0 breaking changes |
| 3B (Phase 3B) | 3 | ~5 | ~17 | ~200 | optional; moderate UX risk |

**Total without Stage 3B**: ~17 logical stages, ~25 git commits, ~130 new tests, ~1150 implementation LOC + roughly ~650 test LOC. Roughly 2× the state-layer refactor in code volume, and ~4× in test volume because every behaviour is covered.

**Running test-count target by stage**: suite goes from **189** (current baseline) → **~220** (end of Stage 1) → **~300** (end of Stage 2) → **~320** (end of Stage 3A) → **~340** (end of Stage 3B if shipped).

If the suite count drifts materially from these targets during implementation, that's a signal to stop and check whether tests are being skipped or whether the stage is doing more than it should.

## Verification

### Phase 1
1. Unit: every DB helper round-trips; migration on a legacy DB leaves existing rows untouched and bumps `user_version` to 2.
2. Unit: renderer labels by persona name when `persona_id` is set; legacy messages render unchanged.
3. Unit: group detection with `(persona_id="partner", provider=CLAUDE)` and `(persona_id="evaluator", provider=CLAUDE)` produces two columns, not one.
4. Manual: open a legacy conversation, verify nothing changed visually.

### Phase 2
1. Unit: resolver disambiguates `partner,` vs `claude,` correctly, handles `all,` expansion, rejects unknown names.
2. Unit: context_builder emits persona system prompt first; `created_at_message_index` slices prior history; pins targeted at a persona name reach the persona.
3. Unit: **model resolution per D6**. A persona with `model_override = None` sends using `config.get("<provider>_model")`; the same persona after `model_override = "claude-haiku-4-5"` is written directly to the DB sends using haiku regardless of the global setting. Global model change in config with `model_override = None` takes effect on the next send.
4. Unit: synthetic default personas (no table row, `persona_id = provider.value`) resolve to the global provider model, mirroring today's behaviour exactly.
5. Manual: the Italian-tutor scenario end-to-end with a single API key — `//addpersona claude as "partner" new Start an Italian conversation...`, `//addpersona claude as "evaluator" new Analyse my replies...`, `//addpersona claude as "translator" new Give word translations...`. Type `partner, Ciao!` and verify the partner responds; type `evaluator,` (prefix-only selection), type a normal message and verify only the evaluator runs; confirm each persona has its own isolated history through the visibility matrix.
6. Manual: `//editpersona "evaluator" ...`, `//removepersona "evaluator"` (tombstones, does not hard-delete — verify tombstoned persona still labels historical messages correctly), `//personas` all work.
7. Manual: `//limit last` does not erase persona setup prompts (they're pinned and also stored in the personas table).

### Phase 3A
1. Manual: create/edit/remove personas from the dialog; verify they stay in sync with the command path.
2. Manual: set a `model_override` on a persona in the dialog, verify the next send goes through that specific model, verify the global provider model still applies to other personas with `model_override = None`.
3. Manual: set a `color_override` on a persona, verify messages from that persona render in the override colour while other personas on the same provider keep the provider default.

### Phase 3B
1. Manual: provider panel rebuilds with per-persona rows after `//addpersona`; returns to the provider-only layout when all explicit personas are tombstoned.
2. Manual: matrix panel rebuilds with one row/column per active persona after `//addpersona`; scrollable when N>4.
3. Manual: mid-chat persona add with `inherit` vs `new` shows the right history in the persona editor's scope indicator.

## Open questions (deferred, not blockers)

All architectural decisions are locked in (D1–D6). Remaining items are purely product/scope choices that don't affect the data model or code path:

- **Compact ProviderPanel layout for N>4 personas** (Phase 3B): needs a small UX mockup before implementation. Not a blocker for Phase 1/2/3A.
- **Should `//help` get a dedicated examples section for `//addpersona`?** Leaning yes — the command's syntax is dense enough that showing a working example (e.g. the Italian-tutor scenario from verification) in `//help` output would help discovery. Small polish item for Phase 2.

## Future extensions (explicitly out of scope for this plan — tracked as follow-up issues)

These are real concerns that will matter once the feature is in daily use, but blocking the current work on them would delay the core value. Each one should become a GitHub issue before implementation starts, so they're visible and tracked.

### FE1. Persona dialog UX for `model_override` and `color_override`

**The feature itself is NOT deferred** — D6 makes `model_override` a core part of the data model (Phase 1) and the send-flow resolution (Phase 2). What's deferred is only the **user-facing path for editing it**: the Phase 3A persona dialog adds dropdowns for model and colour alongside the system-prompt field, so users can say "evaluator = opus, translator = haiku" via UI rather than hand-editing the DB.

The Phase 3B provider panel does **not** grow one combo per persona (that way lies UI crowding); overrides are a per-persona dialog concern, with the provider panel continuing to show one combo per provider as the default that personas inherit via `model_override = None`.

Between Phase 2 shipping and Phase 3A shipping, the `model_override` field exists in the schema and is respected at send time, but is only settable via direct DB manipulation (tests do this; the user can't). That's a deliberate staging choice — it keeps the command syntax simple while the data layer is complete.

Motivating scenario: Italian-tutor with a single Claude API key. The user wants `translator` on haiku (cheap, fast, sufficient for single-word translations) and `evaluator` on opus (slow, expensive, but worth it for critique). Without per-persona models, the user pays opus prices for every translation or gets haiku-quality critiques — both bad.

**Issue title**: "Persona dialog UX for model and colour overrides"
**Blocks**: nothing. Data model and runtime are already in Phase 1/2.

### FE2. Global vs persona system prompt: merge or override?

D6 locks in "persona prompt replaces global provider prompt" as the default semantics. The alternative — always concatenate the global prompt as a baseline, then append the persona prompt — has merit for users whose global prompts are generic guardrails they want on every persona. A user configuration toggle ("persona prompts inherit global provider prompts") could expose both behaviours without committing to either.

**Issue title**: "Provider prompt inheritance mode for personas (replace vs merge)"
**Blocks**: nothing. Can revisit after 3A ships and real usage patterns emerge.

### FE3. Per-persona spend breakdown

Spend is per-provider today and stays per-provider through Phase 2 (the billing unit is genuinely the provider — you're billed by Anthropic/OpenAI/Google, not by "the evaluator persona"). But users with multiple same-provider personas will eventually ask "which persona is burning my Claude budget?" — a pure display concern that could be added as a derived column in the spend panel without changing the billing model.

**Issue title**: "Per-persona spend breakdown display"
**Blocks**: nothing. Display-layer only, no DB migration needed.

### FE4. Provider shorthand becoming an alias

Currently (D1) `claude,` always resolves to the synthetic default persona for Claude. A power user might want to redefine this — "make `claude,` point at my `evaluator` persona by default in this chat". That's a small aliasing feature that could be added with a single field on the Persona row (`is_default_for_provider: bool`) without changing the resolution rules elsewhere. Not worth doing until someone asks for it.

**Issue title**: "Optional per-chat default persona per provider"
**Blocks**: nothing.

### FE5. Persona templates / presets

If the same persona setup (e.g. the three Italian-tutor personas) is reused across many chats, creating them by hand each time is tedious. A "save current personas as a template" + "start new chat from template" feature would address this. Purely additive — the personas table stays the same, templates are a new store.

**Issue title**: "Persona templates for repeated chat setups"
**Blocks**: nothing.
