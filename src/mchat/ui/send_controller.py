# ------------------------------------------------------------------
# Component: SendController
# Responsibility: Own the full message-send lifecycle — message
#                 submission, context building, multi-provider fan-out,
#                 completion/error handling, spend updates, retry
#                 stashing. Persists completed responses through the
#                 host before asking MessageRenderer to display them.
# Collaborators: MainWindow (host), ui.message_renderer, workers.stream_worker,
#                db, router, pricing
# ------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from mchat.models.message import Message, Provider, Role
from mchat.pricing import estimate_cost
from mchat.ui.message_renderer import (
    PROVIDER_DISPLAY,
    PROVIDER_ORDER,
    strip_echoed_heading,
)
from mchat.workers.stream_worker import StreamWorker

if TYPE_CHECKING:
    from mchat.ui.main_window import MainWindow


class SendController:
    """Owns the send/retry flow. Lives as ``MainWindow._send``.

    The controller holds its own transient state (active workers,
    column buffer, retry stash) but reaches into ``host`` for shared
    services: router, db, chat, input widget, combos, conversation,
    and the message renderer.
    """

    def __init__(self, host: "MainWindow") -> None:
        self._host = host
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

        if text.strip().startswith("//"):
            host._handle_command(text)
            return

        # +provider / -provider selection adjustment
        stripped = text.strip()
        if len(stripped) > 1 and stripped[0] in ("+", "-"):
            if host._handle_selection_adjust(stripped):
                return

        if not host._router:
            QMessageBox.warning(
                host, "No API Keys",
                "Please configure at least one API key in Settings.",
            )
            return

        if not host._current_conv:
            host._on_new_chat()

        # Route message
        targets, cleaned_text = host._router.parse(text)

        # If provider prefixes consumed everything, treat as selection change
        if not cleaned_text.strip() and targets != host._router.selection:
            host._sync_checkboxes_from_selection()
            host._update_input_placeholder()
            host._update_input_color()
            host._save_selection()
            names = ", ".join(PROVIDER_DISPLAY[p] for p in targets)
            host._chat.add_note(f"selected: {names}")
            return

        # Validate all targets are configured
        configured = set(host._router._providers.keys())
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

        user_msg = Message(
            role=Role.USER,
            content=text,
            conversation_id=host._current_conv.id,
            addressed_to=addressed_to,
        )
        host._db.add_message(user_msg)
        host._current_conv.messages.append(user_msg)
        host._chat.add_message(user_msg)

        # Auto-title on first message
        if len(host._current_conv.messages) == 1:
            title = text[:50] + ("..." if len(text) > 50 else "")
            host._db.update_conversation_title(host._current_conv.id, title)
            host._sidebar.update_conversation_title(host._current_conv.id, title)

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
        self._multi_workers.clear()
        self._column_buffer.clear()

        for provider_id in targets:
            model = host._selected_model(provider_id)
            provider = host._router.get_provider(provider_id)
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
        host._set_combo_waiting(provider_id, False)
        self._multi_workers.pop(provider_id, None)

        # Update spend
        cost = estimate_cost(model, input_tokens, output_tokens)
        if cost is not None and host._current_conv:
            host._db.add_conversation_spend(
                host._current_conv.id, provider_id.value, cost, estimated
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
        host._set_combo_waiting(provider_id, False)
        worker = self._multi_workers.pop(provider_id, None)
        transient = worker.last_error_transient if worker else False

        error_msg = Message(
            role=Role.ASSISTANT,
            content=f"[Error from {provider_id.value}: {error}]",
            provider=provider_id,
            conversation_id=host._current_conv.id,
        )
        host._db.add_message(error_msg)
        host._current_conv.messages.append(error_msg)
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
        host = self._host
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
                conversation_id=host._current_conv.id,
            )
            host._db.add_message(msg)
            host._current_conv.messages.append(msg)
            persisted.append(msg)
        return persisted
