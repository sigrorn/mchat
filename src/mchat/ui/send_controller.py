# ------------------------------------------------------------------
# Component: SendController
# Responsibility: Own the full message-send lifecycle — message
#                 submission, context building, multi-provider fan-out,
#                 completion/error handling, spend updates, retry
#                 stashing. Persists completed responses through the
#                 services context before asking MessageRenderer to
#                 display them.
#
#                 Data-layer access (db, router, session, selection)
#                 goes through the ServicesContext. Presentational
#                 side-effects go through a narrow SendHost Protocol
#                 — the concrete MainWindow type is never imported at
#                 runtime, only for type-checking.
# Collaborators: ServicesContext, SendHost (Protocol), workers.stream_worker,
#                ui.message_renderer, pricing
# ------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from PySide6.QtWidgets import QMessageBox

from mchat.models.message import Message, Provider, Role
from mchat.pricing import estimate_cost
from mchat.ui.message_renderer import (
    PROVIDER_DISPLAY,
    PROVIDER_ORDER,
    strip_echoed_heading,
)
from mchat.ui.services import ServicesContext
from mchat.workers.stream_worker import StreamWorker

if TYPE_CHECKING:
    from mchat.ui.main_window import MainWindow  # noqa: F401


class SendHost(Protocol):
    """The presentational surface SendController is allowed to touch.

    Lives alongside SendController (not as a runtime-enforced type)
    so every method the controller calls on its host is documented
    in one place. Python doesn't enforce Protocols at runtime — the
    real host is still MainWindow — but static type checkers use
    this to flag drift if someone adds a new call that reaches
    beyond the allowed surface.
    """

    # Presentation widgets SendController reads/writes
    _chat: Any
    _input: Any
    _sidebar: Any
    _renderer: Any
    _column_mode: bool

    # Callbacks SendController invokes
    def _handle_command(self, text: str) -> bool: ...
    def _handle_selection_adjust(self, text: str) -> bool: ...
    def _on_new_chat(self) -> None: ...
    def _selected_model(self, provider_id: Provider) -> str: ...
    def _build_context(self, provider_id: Provider) -> list[Message]: ...
    def _save_selection(self) -> None: ...
    def _sync_checkboxes_from_selection(self) -> None: ...
    def _update_input_placeholder(self) -> None: ...
    def _update_input_color(self) -> None: ...
    def _update_spend_labels(self) -> None: ...
    def _set_combo_waiting(self, p: Provider, waiting: bool) -> None: ...
    def _set_combo_retrying(self, p: Provider) -> None: ...


class SendController:
    """Owns the send/retry flow.

    Takes a ServicesContext for all data-layer access and a SendHost
    for the presentational side-effects. The controller holds its own
    transient state (active workers, column buffer, retry stash).
    """

    def __init__(self, host: SendHost, services: ServicesContext) -> None:
        self._host = host
        self._services = services
        self._multi_workers: dict[Provider, StreamWorker] = {}
        self._column_buffer: dict[Provider, tuple[str, str, str, int, int, bool]] = {}
        self._retry_contexts: dict[Provider, list[Message]] = {}
        self._retry_models: dict[Provider, str] = {}
        # provider -> (error, transient)
        self._retry_failed: dict[Provider, tuple[str, bool]] = {}
        # provider -> message DB id of the stored error (so //retry can hide it)
        self._retry_error_msg_ids: dict[Provider, int | None] = {}

    # ------------------------------------------------------------------
    # Retry-stash helpers (also consumed by commands._handle_retry)
    # ------------------------------------------------------------------

    def clear_retry_stash(self) -> None:
        self._retry_contexts.clear()
        self._retry_models.clear()
        self._retry_failed.clear()
        self._retry_error_msg_ids.clear()

    @property
    def retry_contexts(self) -> dict[Provider, list[Message]]:
        return self._retry_contexts

    @property
    def retry_failed(self) -> dict[Provider, tuple[str, bool]]:
        return self._retry_failed

    @property
    def retry_error_msg_ids(self) -> dict[Provider, int | None]:
        return self._retry_error_msg_ids

    # ------------------------------------------------------------------
    # Submission entry point
    # ------------------------------------------------------------------

    def on_message_submitted(self, text: str) -> None:
        host = self._host
        svc = self._services

        if text.strip().startswith("//"):
            host._handle_command(text)
            return

        # +provider / -provider selection adjustment
        stripped = text.strip()
        if len(stripped) > 1 and stripped[0] in ("+", "-"):
            if host._handle_selection_adjust(stripped):
                return

        if svc.router is None:
            QMessageBox.warning(
                host, "No API Keys",
                "Please configure at least one API key in Settings.",
            )
            return

        if svc.session.current is None:
            host._on_new_chat()

        # Route message
        targets, cleaned_text = svc.router.parse(text)

        # If provider prefixes consumed everything, treat as selection change
        if not cleaned_text.strip() and targets != svc.router.selection:
            host._sync_checkboxes_from_selection()
            host._update_input_placeholder()
            host._update_input_color()
            host._save_selection()
            names = ", ".join(PROVIDER_DISPLAY[p] for p in targets)
            host._chat.add_note(f"selected: {names}")
            return

        # Validate all targets are configured
        configured = set(svc.router._providers.keys())
        missing = [p for p in targets if p not in configured]
        targets = [p for p in targets if p in configured]
        if missing:
            names = ", ".join(PROVIDER_DISPLAY[p] for p in missing)
            host._chat.add_note(f"{names} not configured — skipped")
        if not targets:
            QMessageBox.warning(
                host, "No Provider Available",
                "None of the target providers have API keys configured.",
            )
            return

        # Determine addressed_to: "all" if the user broadcast to every
        # configured provider, otherwise a comma-separated list of values.
        if set(targets) == configured:
            addressed_to = "all"
        else:
            addressed_to = ",".join(p.value for p in targets)

        current = svc.session.current
        user_msg = Message(
            role=Role.USER,
            content=text,
            conversation_id=current.id,
            addressed_to=addressed_to,
        )
        svc.db.add_message(user_msg)
        svc.session.append_message(user_msg)
        host._chat.add_message(user_msg)

        # Auto-title on first message
        if len(current.messages) == 1:
            title = text[:50] + ("..." if len(text) > 50 else "")
            svc.db.update_conversation_title(current.id, title)
            svc.session.set_title(title)
            host._sidebar.update_conversation_title(current.id, title)

        host._input.set_enabled(False)
        host._save_selection()
        host._sync_checkboxes_from_selection()
        self.clear_retry_stash()

        if len(targets) == 1:
            self.send_single(targets[0])
        else:
            self.send_multi(targets)

    # ------------------------------------------------------------------
    # Fan-out
    # ------------------------------------------------------------------

    def send_single(self, provider_id: Provider) -> None:
        """Send to a single provider."""
        # Kept as a distinct entry point for clarity; delegates to send_multi
        # so state handling stays in one place.
        self._host._set_combo_waiting(provider_id, True)
        self.send_multi([provider_id])

    def send_multi(
        self,
        targets: list[Provider],
        context_override: dict[Provider, list[Message]] | None = None,
    ) -> None:
        """Send to multiple providers simultaneously; render when all done."""
        host = self._host
        svc = self._services
        self._multi_workers.clear()
        self._column_buffer.clear()

        for provider_id in targets:
            model = host._selected_model(provider_id)
            provider = svc.router.get_provider(provider_id)
            host._set_combo_waiting(provider_id, True)
            if context_override and provider_id in context_override:
                context_messages = context_override[provider_id]
            else:
                context_messages = host._build_context(provider_id)

            # Stash for //retry
            self._retry_contexts[provider_id] = context_messages
            self._retry_models[provider_id] = model

            worker = StreamWorker(provider, context_messages, model)
            worker.stream_complete.connect(
                lambda full_text, inp, out, est, pid=provider_id, mdl=model: (
                    self._on_complete(pid, mdl, full_text, inp, out, est)
                )
            )
            worker.stream_error.connect(
                lambda error, pid=provider_id: self._on_error(pid, error)
            )
            worker.retrying.connect(
                lambda attempt, mx, pid=provider_id: host._set_combo_retrying(pid)
            )
            self._multi_workers[provider_id] = worker
            worker.start()

    # ------------------------------------------------------------------
    # Completion / error callbacks (main thread)
    # ------------------------------------------------------------------

    def _on_complete(
        self,
        provider_id: Provider,
        model: str,
        full_text: str,
        input_tokens: int,
        output_tokens: int,
        estimated: bool = False,
    ) -> None:
        host = self._host
        svc = self._services
        host._set_combo_waiting(provider_id, False)
        self._multi_workers.pop(provider_id, None)

        # Update spend
        cost = estimate_cost(model, input_tokens, output_tokens)
        current = svc.session.current
        if cost is not None and current is not None:
            svc.db.add_conversation_spend(
                current.id, provider_id.value, cost, estimated
            )
        host._update_spend_labels()

        # Buffer and render once every worker has finished.
        label = PROVIDER_DISPLAY[provider_id]
        self._column_buffer[provider_id] = (
            label, full_text, model, input_tokens, output_tokens, estimated
        )

        if not self._multi_workers:
            if host._column_mode:
                persisted = self._persist_buffered("cols")
                host._renderer.render_column_responses(persisted)
            else:
                persisted = self._persist_buffered("lines")
                host._renderer.render_list_responses(persisted)
            self._column_buffer.clear()
            host._input.set_enabled(True)
            host._update_input_placeholder()
            host._update_input_color()

    def _on_error(self, provider_id: Provider, error: str) -> None:
        host = self._host
        svc = self._services
        host._set_combo_waiting(provider_id, False)
        worker = self._multi_workers.pop(provider_id, None)
        transient = worker.last_error_transient if worker else False

        current = svc.session.current
        error_msg = Message(
            role=Role.ASSISTANT,
            content=f"[Error from {provider_id.value}: {error}]",
            provider=provider_id,
            conversation_id=current.id if current else None,
        )
        svc.db.add_message(error_msg)
        svc.session.append_message(error_msg)
        host._chat.add_message(error_msg)

        # Stash for //retry
        self._retry_failed[provider_id] = (error, transient)
        self._retry_error_msg_ids[provider_id] = error_msg.id

        if not self._multi_workers:
            host._input.set_enabled(True)
            host._update_input_placeholder()
            host._update_input_color()

    # ------------------------------------------------------------------
    # Persistence of buffered responses
    # ------------------------------------------------------------------

    def _persist_buffered(self, display_mode: str) -> list[Message]:
        """Save every buffered response to the DB + conversation and
        return the new Message objects in stable provider order."""
        svc = self._services
        current = svc.session.current
        providers = [p for p in PROVIDER_ORDER if p in self._column_buffer]
        persisted: list[Message] = []
        for p in providers:
            _label, full_text, model, _inp, _out, _est = self._column_buffer[p]
            msg = Message(
                role=Role.ASSISTANT,
                content=strip_echoed_heading(full_text),
                provider=p,
                model=model,
                display_mode=display_mode,
                conversation_id=current.id if current else None,
            )
            svc.db.add_message(msg)
            svc.session.append_message(msg)
            persisted.append(msg)
        return persisted
