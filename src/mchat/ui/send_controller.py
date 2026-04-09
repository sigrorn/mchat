# ------------------------------------------------------------------
# Component: SendController
# Responsibility: Own the full message-send lifecycle — message
#                 submission, persona resolution, context building,
#                 multi-persona fan-out, completion/error handling,
#                 spend updates, retry stashing. Persists completed
#                 responses through the services context before asking
#                 MessageRenderer to display them.
#
#                 Data-layer access (db, router, session, selection)
#                 goes through the ServicesContext. Presentational
#                 side-effects go through a narrow SendHost Protocol
#                 — the concrete MainWindow type is never imported at
#                 runtime, only for type-checking.
#
#                 Transient state (_multi_workers, _column_buffer,
#                 retry stash) is keyed by persona_id (str) rather
#                 than Provider so two same-provider personas can
#                 coexist in a single send group without clobbering
#                 each other's state. See docs/plans/personas.md
#                 § Stage 2.6.
# Collaborators: ServicesContext, SendHost (Protocol), workers.stream_worker,
#                ui.message_renderer, ui.persona_resolver,
#                ui.persona_resolution, ui.persona_target, pricing
# ------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from PySide6.QtWidgets import QMessageBox

from mchat.models.message import Message, Provider, Role
from mchat.pricing import estimate_cost
from mchat.ui.context_builder import load_persona_for_target
from mchat.ui.message_renderer import (
    PROVIDER_DISPLAY,
    PROVIDER_ORDER,
    strip_echoed_heading,
)
from mchat.ui.persona_resolution import resolve_persona_model
from mchat.ui.persona_resolver import PersonaResolver, ResolveError
from mchat.ui.persona_target import PersonaTarget, synthetic_default
from mchat.ui.services import ServicesContext
from mchat.workers.stream_worker import StreamWorker

if TYPE_CHECKING:
    from mchat.ui.main_window import MainWindow  # noqa: F401


class SendHost(Protocol):
    """The presentational surface SendController is allowed to touch."""

    # Presentation widgets SendController reads/writes
    _chat: Any
    _input: Any
    _sidebar: Any
    _renderer: Any
    _provider_panel: Any
    _column_mode: bool

    # Callbacks SendController invokes
    def _handle_command(self, text: str) -> bool: ...
    def _handle_selection_adjust(self, text: str) -> bool: ...
    def _on_new_chat(self) -> None: ...
    def _selected_model(self, provider_id: Provider) -> str: ...
    def _build_context(self, target: Any) -> list[Message]: ...
    def _save_selection(self) -> None: ...
    def _sync_checkboxes_from_selection(self) -> None: ...
    def _update_input_placeholder(self) -> None: ...
    def _update_input_color(self) -> None: ...
    def _update_spend_labels(self) -> None: ...
    def _set_combo_waiting(self, p: Provider, waiting: bool) -> None: ...
    def _set_combo_retrying(self, p: Provider) -> None: ...


# Type aliases for the transient state dicts. All keyed by persona_id (str).
_ColumnBufferEntry = tuple[str, str, str, int, int, bool, PersonaTarget]
# (label, full_text, model, input_tokens, output_tokens, estimated, target)


class SendController:
    """Owns the send/retry flow. Stage 2.6+ threads PersonaTargets
    through instead of bare Providers."""

    def __init__(self, host: SendHost, services: ServicesContext) -> None:
        self._host = host
        self._services = services
        # PersonaResolver maps user input → PersonaTargets, honouring
        # the current conversation's active persona list.
        self._resolver = (
            PersonaResolver(services.router) if services.router is not None else None
        )

        # Send mode: parallel (default) or sequential
        self._sequential_mode: bool = False
        # Sequential chain state
        self._seq_queue: list[PersonaTarget] = []
        self._seq_context_override: dict[str, list[Message]] | None = None
        self._seq_conv_id: int | None = None
        self._conv_switched: bool = False

        # Transient per-send state, keyed by persona_id (str) so two
        # same-provider personas can coexist in one send group.
        self._multi_workers: dict[str, StreamWorker] = {}
        self._column_buffer: dict[str, _ColumnBufferEntry] = {}

        # Retry stash — all keyed by persona_id (str). _retry_targets
        # holds the full PersonaTarget so the retry command can
        # reconstruct the send without re-parsing the user input.
        self._retry_targets: dict[str, PersonaTarget] = {}
        self._retry_contexts: dict[str, list[Message]] = {}
        self._retry_models: dict[str, str] = {}
        self._retry_failed: dict[str, tuple[str, bool]] = {}
        self._retry_error_msg_ids: dict[str, int | None] = {}
        # Display labels (persona name or provider display name) for
        # the retry command's user-facing notes, so the retry handler
        # doesn't need to re-query the DB.
        self._retry_labels: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Retry-stash helpers (consumed by commands.handle_retry)
    # ------------------------------------------------------------------

    def clear_retry_stash(self) -> None:
        self._retry_targets.clear()
        self._retry_contexts.clear()
        self._retry_models.clear()
        self._retry_failed.clear()
        self._retry_error_msg_ids.clear()
        self._retry_labels.clear()

    @property
    def retry_contexts(self) -> dict[str, list[Message]]:
        return self._retry_contexts

    @property
    def retry_failed(self) -> dict[str, tuple[str, bool]]:
        return self._retry_failed

    @property
    def retry_error_msg_ids(self) -> dict[str, int | None]:
        return self._retry_error_msg_ids

    @property
    def retry_targets(self) -> dict[str, PersonaTarget]:
        return self._retry_targets

    @property
    def retry_labels(self) -> dict[str, str]:
        return self._retry_labels

    def rebuild_resolver(self) -> None:
        """Called when the router is rebuilt (e.g. after settings change).
        The old resolver holds a stale Router reference."""
        self._resolver = (
            PersonaResolver(self._services.router)
            if self._services.router is not None
            else None
        )

    # ------------------------------------------------------------------
    # Submission entry point
    # ------------------------------------------------------------------

    def on_message_submitted(self, text: str) -> None:
        host = self._host
        svc = self._services

        # Guard: reject if a send is already in progress
        if self._multi_workers or self._seq_queue:
            host._chat.add_note("Send in progress — please wait")
            return

        # Edit mode: intercept before normal command/resolve paths
        edit_state = getattr(host, "_edit_state", None)
        if edit_state is not None:
            self._handle_edit_submit(text, edit_state)
            return

        if text.strip().startswith("//"):
            host._handle_command(text)
            return

        # Single-/ typo guard: /command looks like a mistyped //command
        if text.strip().startswith("/") and not text.strip().startswith("//"):
            word = text.strip().split()[0] if text.strip() else ""
            if len(word) > 1 and word[1:].isalpha():
                host._chat.add_note(
                    f"Did you mean //{word[1:]}? Commands use // prefix"
                )
                return

        # +provider / -provider selection adjustment
        stripped = text.strip()
        if len(stripped) > 1 and stripped[0] in ("+", "-"):
            if host._handle_selection_adjust(stripped):
                return

        if svc.router is None or self._resolver is None:
            QMessageBox.warning(
                host, "No API Keys",
                "Please configure at least one API key in Settings.",
            )
            return

        if svc.session.current is None:
            host._on_new_chat()

        conv = svc.session.current
        if conv is None:
            return

        # Capture selection *before* resolve() — resolve writes the new
        # selection into SelectionState as a side effect, so we can't
        # use svc.router.selection after the call to detect whether
        # the input was a prefix-only selection change. See #60.
        pre_parse_selection = list(svc.router.selection)

        # Route message through PersonaResolver. This replaces the
        # direct router.parse call — PersonaResolver internally uses
        # the router for provider-shorthand parsing but also handles
        # explicit persona names and returns list[PersonaTarget].
        try:
            targets, cleaned_text = self._resolver.resolve(text, conv.id, svc.db)
        except ResolveError as e:
            host._chat.add_note(f"Error: {e}")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: host._input._text_edit.setPlainText(text))
            return

        # Stage 3A.4: empty selection with no prefix → no targets.
        # Show a user-facing hint instead of silently doing nothing.
        if not targets and cleaned_text.strip() == text.strip():
            host._chat.add_note(
                "No personas selected \u2014 use //addpersona or select a provider first"
            )
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: host._input._text_edit.setPlainText(text))
            return

        # Prefix-only input: no content left after consuming prefixes.
        # Treat as a selection change, not a send. Guard against the
        # post-mutation router state per #60 by comparing to the
        # pre-parse snapshot.
        if not cleaned_text.strip():
            post_parse_selection = svc.router.selection
            if list(post_parse_selection) != pre_parse_selection:
                host._save_selection()
                names = ", ".join(
                    PROVIDER_DISPLAY.get(p, p.value) for p in post_parse_selection
                )
                host._chat.add_note(f"selected: {names}")
            return

        # Validate every target's provider is configured.
        configured_providers = set(svc.router._providers.keys())
        missing_targets = [
            t for t in targets if t.provider not in configured_providers
        ]
        targets = [t for t in targets if t.provider in configured_providers]
        if missing_targets:
            names = ", ".join(
                PROVIDER_DISPLAY.get(t.provider, t.provider.value)
                for t in missing_targets
            )
            host._chat.add_note(f"{names} not configured — skipped")
        if not targets:
            QMessageBox.warning(
                host, "No Provider Available",
                "None of the target providers have API keys configured.",
            )
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: host._input._text_edit.setPlainText(text))
            return

        # addressed_to is a comma-separated list of persona_ids so
        # the visibility filter can key on them (Stage 2.7). Synthetic
        # defaults use persona_id = provider.value (D1 exception), so
        # legacy chats continue to produce the same strings they did
        # before personas existed. We no longer emit the "all" shortcut
        # because "every Claude persona" != "every persona", and the
        # filter's "all" branch is retained only for legacy rows.
        seen_pids: list[str] = []
        for t in targets:
            if t.persona_id not in seen_pids:
                seen_pids.append(t.persona_id)
        addressed_to = ",".join(seen_pids)

        user_msg = Message(
            role=Role.USER,
            content=text,
            conversation_id=conv.id,
            addressed_to=addressed_to,
        )
        svc.db.add_message(user_msg)
        svc.session.append_message(user_msg)
        host._chat.add_message(user_msg)

        # Auto-title on first message
        if len(conv.messages) == 1:
            title = text[:50] + ("..." if len(text) > 50 else "")
            svc.db.update_conversation_title(conv.id, title)
            svc.session.set_title(title)
            host._sidebar.update_conversation_title(conv.id, title)

        host._input.set_enabled(False)
        host._save_selection()
        # sync/placeholder/color already fan out from the selection
        # state change triggered by the resolver.
        self.clear_retry_stash()

        # Deduplicate targets by persona_id — prevents the double-run
        # bug where both synthetic defaults and explicit personas end up
        # in the target list.
        seen: set[str] = set()
        deduped: list[PersonaTarget] = []
        for t in targets:
            if t.persona_id not in seen:
                seen.add(t.persona_id)
                deduped.append(t)
        targets = deduped

        # Sanity check: warn if target count exceeds expected maximum
        expected_max = len(svc.db.list_personas(conv.id)) or len(
            svc.router._providers if svc.router else {}
        )
        import mchat.debug_logger as _dl
        if _dl.enabled:
            _dl.log_outgoing(
                "_SEND_",
                f"targets={[(t.persona_id, t.provider.value) for t in targets]} "
                f"(expected_max={expected_max})"
            )
        if len(targets) > max(expected_max, 1):
            host._chat.add_note(
                f"Warning: {len(targets)} targets but only {expected_max} "
                f"personas configured — possible duplicate send"
            )

        if len(targets) == 1:
            self.send_single(targets[0])
        else:
            self.send_multi(targets)

    # ------------------------------------------------------------------
    # Edit-mode submit
    # ------------------------------------------------------------------

    def _handle_edit_submit(self, text: str, edit_state: dict) -> None:
        """Handle a submit while in edit mode (set by //edit).

        Empty text → remove the original message, continue replay.
        // command → exit edit mode, dispatch as command.
        Non-empty → send to the original recipients, then queue
        subsequent user messages for review.
        """
        host = self._host
        svc = self._services
        original_msg = edit_state["original_msg"]

        # If the user typed a // command while editing, exit edit mode
        # and dispatch it normally instead of sending it as a message.
        if text.strip().startswith("//"):
            host._edit_state = None
            host._input._edit_mode = False
            host._handle_command(text)
            return

        if not text:
            # Empty submit → remove the message, continue replay chain
            if original_msg.id is not None:
                svc.db.delete_messages([original_msg.id])
            host._chat.add_note("message removed")
            conv = svc.session.current
            if conv:
                conv.messages = svc.db.get_messages(conv.id)
                host._display_messages(conv.messages)
            # Advance to the next queued message (don't break the chain)
            self._advance_edit_replay()
            return

        conv = svc.session.current
        if conv is None:
            host._edit_state = None
            return

        # Parse original addressed_to to build targets
        addressed = original_msg.addressed_to or ""
        if addressed and addressed != "all":
            tokens = [t.strip() for t in addressed.split(",") if t.strip()]
        else:
            # Fall back to current selection
            tokens = [t.persona_id for t in svc.selection.selection]

        # Build PersonaTargets from the tokens
        targets: list[PersonaTarget] = []
        for token in tokens:
            # Check if token is a provider value (synthetic default)
            provider_match = None
            for p in Provider:
                if p.value == token:
                    provider_match = p
                    break
            if provider_match:
                targets.append(synthetic_default(provider_match))
            else:
                # Explicit persona — look up provider from DB
                personas = svc.db.list_personas(conv.id)
                persona = next((p for p in personas if p.id == token), None)
                if persona:
                    targets.append(PersonaTarget(persona_id=persona.id, provider=persona.provider))
                else:
                    targets.append(PersonaTarget(persona_id=token, provider=Provider.CLAUDE))

        if not targets:
            host._chat.add_note("Error: no targets for edit re-send")
            host._edit_state = None
            return

        # Build addressed_to for the new message
        seen_pids: list[str] = []
        for t in targets:
            if t.persona_id not in seen_pids:
                seen_pids.append(t.persona_id)
        addressed_to = ",".join(seen_pids)

        # Persist the new user message
        user_msg = Message(
            role=Role.USER,
            content=text,
            conversation_id=conv.id,
            addressed_to=addressed_to,
        )
        svc.db.add_message(user_msg)
        svc.session.append_message(user_msg)
        host._chat.add_message(user_msg)

        host._input.set_enabled(False)
        self.clear_retry_stash()

        if len(targets) == 1:
            self.send_single(targets[0])
        else:
            self.send_multi(targets)

    def _advance_edit_replay(self) -> None:
        """Called after a send completes in edit mode. Loads the next
        user message from the replay queue into the input, or exits
        edit mode if the queue is exhausted."""
        host = self._host
        edit_state = getattr(host, "_edit_state", None)
        if edit_state is None:
            return

        queue = edit_state["replay_queue"]
        idx = edit_state["replay_index"]

        if idx >= len(queue):
            # Queue exhausted — return to normal mode
            host._edit_state = None
            host._chat.add_note("edit replay complete")
            return

        next_msg = queue[idx]
        edit_state["replay_index"] = idx + 1
        edit_state["original_msg"] = next_msg

        host._input._text_edit.setPlainText(next_msg.content)
        host._input._edit_mode = True
        targets_label = next_msg.addressed_to or "current selection"
        host._chat.add_note(
            f"replaying message → {targets_label} — edit or submit as-is (empty to remove)"
        )

    # ------------------------------------------------------------------
    # Fan-out
    # ------------------------------------------------------------------

    def send_single(self, target: PersonaTarget) -> None:
        """Send to a single persona (kept distinct for clarity)."""
        self._host._set_combo_waiting(target.persona_id, True)
        self.send_multi([target])

    def send_multi(
        self,
        targets: list[PersonaTarget],
        context_override: dict[str, list[Message]] | None = None,
    ) -> None:
        """Send to multiple personas; parallel or sequential per mode.

        ``context_override`` is keyed by persona_id — used by the //retry
        command to re-send the same context for failed targets.
        """
        # Capture the conversation id so the sequential chain can detect
        # if the user switched conversations mid-chain.
        conv = self._services.session.current
        self._seq_conv_id = conv.id if conv else None

        if self._sequential_mode and len(targets) > 1:
            self._seq_queue = list(targets[1:])
            self._seq_context_override = context_override
            # Mark queued personas as gray
            for t in self._seq_queue:
                self._host._provider_panel.set_combo_queued(t.persona_id)
            self._send_parallel([targets[0]], context_override)
        else:
            self._seq_queue = []
            self._send_parallel(targets, context_override)

    def _send_parallel(
        self,
        targets: list[PersonaTarget],
        context_override: dict[str, list[Message]] | None = None,
    ) -> None:
        """Send to the given targets simultaneously."""
        host = self._host
        svc = self._services
        self._multi_workers.clear()
        self._column_buffer.clear()

        conv = svc.session.current

        for target in targets:
            persona = load_persona_for_target(conv, target, svc.db)
            model = resolve_persona_model(persona, svc.config)
            provider = svc.router.get_provider(target.provider)
            host._set_combo_waiting(target.persona_id, True)

            if context_override and target.persona_id in context_override:
                context_messages = context_override[target.persona_id]
            else:
                context_messages = host._build_context(target)

            # Stash for //retry (all keyed by persona_id)
            pid = target.persona_id
            self._retry_targets[pid] = target
            self._retry_contexts[pid] = context_messages
            self._retry_models[pid] = model
            # Display label for retry command: persona name if explicit,
            # provider display name for synthetic defaults.
            if persona.id == target.provider.value:
                self._retry_labels[pid] = PROVIDER_DISPLAY.get(
                    target.provider, target.provider.value
                )
            else:
                self._retry_labels[pid] = persona.name

            worker = StreamWorker(
                provider, context_messages, model,
                persona_name=persona.name,
            )
            worker.stream_complete.connect(
                lambda full_text, inp, out, est, t=target, mdl=model: (
                    self._on_complete(t, mdl, full_text, inp, out, est)
                )
            )
            worker.stream_error.connect(
                lambda error, t=target: self._on_error(t, error)
            )
            worker.retrying.connect(
                lambda attempt, mx, t=target: host._set_combo_retrying(t.persona_id)
            )
            self._multi_workers[target.persona_id] = worker
            worker.start()

    # ------------------------------------------------------------------
    # Completion / error callbacks (main thread)
    # ------------------------------------------------------------------

    def _on_complete(
        self,
        target: PersonaTarget,
        model: str,
        full_text: str,
        input_tokens: int,
        output_tokens: int,
        estimated: bool = False,
    ) -> None:
        host = self._host
        svc = self._services
        host._set_combo_waiting(target.persona_id, False)

        # Check if the user switched conversations since this send started.
        # If so, we still persist to the ORIGINAL conversation (via
        # _seq_conv_id) but skip rendering into the chat widget.
        current_conv = svc.session.current
        self._conv_switched = (
            self._seq_conv_id is not None
            and (current_conv is None or current_conv.id != self._seq_conv_id)
        )
        self._multi_workers.pop(target.persona_id, None)

        # Per-persona spend tracking — always to the original conversation
        cost = estimate_cost(model, input_tokens, output_tokens)
        if cost is not None and self._seq_conv_id is not None:
            svc.db.add_conversation_spend(
                self._seq_conv_id, target.persona_id, cost, estimated
            )
        if not self._conv_switched:
            host._update_spend_labels()

        # Buffer and render once every worker has finished.
        label = self._retry_labels.get(
            target.persona_id,
            PROVIDER_DISPLAY.get(target.provider, target.provider.value),
        )
        self._column_buffer[target.persona_id] = (
            label, full_text, model, input_tokens, output_tokens, estimated, target,
        )

        if not self._multi_workers:
            # Use "seq" display_mode for sequential chain responses so
            # the reload grouping can detect send-group boundaries.
            mode = "seq" if self._sequential_mode else (
                "cols" if host._column_mode else "lines"
            )
            persisted = self._persist_buffered(mode)
            # Only render if we're still on the same conversation;
            # otherwise the response is persisted to the original conv
            # and will appear when the user switches back.
            if not self._conv_switched:
                if host._column_mode:
                    host._renderer.render_column_responses(persisted)
                else:
                    host._renderer.render_list_responses(persisted)
            self._column_buffer.clear()

            # Sequential chain: continue if still on same conversation.
            # If switched, stop the chain — completed responses are
            # already persisted to the original conversation.
            if self._seq_queue and not self._conv_switched:
                next_target = self._seq_queue.pop(0)
                host._provider_panel.apply_combo_style(next_target.persona_id)
                self._send_parallel([next_target], self._seq_context_override)
                return

            if self._seq_queue and self._conv_switched:
                for t in self._seq_queue:
                    host._provider_panel.apply_combo_style(t.persona_id)
                self._seq_queue.clear()

            host._input.set_enabled(True)
            host._update_input_placeholder()
            host._update_input_color()
            # If in edit mode, advance the replay queue
            self._advance_edit_replay()

    def _on_error(self, target: PersonaTarget, error: str) -> None:
        host = self._host
        svc = self._services
        host._set_combo_waiting(target.persona_id, False)
        worker = self._multi_workers.pop(target.persona_id, None)
        transient = worker.last_error_transient if worker else False

        current = svc.session.current
        error_msg = Message(
            role=Role.ASSISTANT,
            content=f"[Error from {target.provider.value}: {error}]",
            provider=target.provider,
            persona_id=target.persona_id,
            conversation_id=current.id if current else None,
        )
        svc.db.add_message(error_msg)
        svc.session.append_message(error_msg)
        host._chat.add_message(error_msg)

        # Stash for //retry (keyed by persona_id).
        self._retry_failed[target.persona_id] = (error, transient)
        self._retry_error_msg_ids[target.persona_id] = error_msg.id

        if not self._multi_workers:
            # On error, clear the sequential queue (chain stops)
            for t in self._seq_queue:
                host._provider_panel.apply_combo_style(t.persona_id)
            self._seq_queue.clear()
            host._input.set_enabled(True)
            host._update_input_placeholder()
            host._update_input_color()

    # ------------------------------------------------------------------
    # Persistence of buffered responses
    # ------------------------------------------------------------------

    def _persist_buffered(self, display_mode: str) -> list[Message]:
        """Save every buffered response to the DB + conversation and
        return the new Message objects in stable provider order.

        Each Message is tagged with both provider and persona_id so
        the renderer and exporter can label by persona name and
        group by persona_id.
        """
        svc = self._services
        # Use the original conversation id (captured at send time) to
        # ensure responses go to the right conversation even if the
        # user switched chats mid-send.
        conv_id = self._seq_conv_id
        current = svc.session.current
        # Sort by persona sort_order for stability.
        def sort_key(pid: str) -> tuple[int, str]:
            _label, _text, _model, _inp, _out, _est, target = self._column_buffer[pid]
            order = (
                PROVIDER_ORDER.index(target.provider)
                if target.provider in PROVIDER_ORDER
                else 99
            )
            return (order, pid)

        persona_ids = sorted(self._column_buffer.keys(), key=sort_key)
        persisted: list[Message] = []
        for pid in persona_ids:
            _label, full_text, model, _inp, _out, _est, target = self._column_buffer[pid]
            msg = Message(
                role=Role.ASSISTANT,
                content=strip_echoed_heading(full_text),
                provider=target.provider,
                model=model,
                display_mode=display_mode,
                persona_id=target.persona_id,
                conversation_id=conv_id,
            )
            svc.db.add_message(msg)
            if not self._conv_switched:
                svc.session.append_message(msg)
            persisted.append(msg)
        return persisted
