# ------------------------------------------------------------------
# Component: TitleGenerator
# Responsibility: Auto-title generation for conversations. Extracted
#                 from SendController (#159) so the title-generation
#                 state and logic live in a focused, independently
#                 testable module.
# Collaborators: db, session, sidebar, workers.title_worker
# ------------------------------------------------------------------
from __future__ import annotations

from typing import Any

from mchat.models.message import Message, Role
from mchat.ui.persona_target import PersonaTarget
from mchat.workers.title_worker import TitleWorker, clean_title


class TitleGenerator:
    """Manages LLM auto-title generation for conversations.

    Owns the once-per-conversation gate, fallback title tracking,
    TitleWorker lifecycle, and the apply-or-skip decision.
    """

    def __init__(
        self,
        db: Any,
        session: Any,
        sidebar: Any,
    ) -> None:
        self._db = db
        self._session = session
        self._sidebar = sidebar

        # Once-per-conversation gate so we never re-trigger.
        self._title_generation_attempted: set[int] = set()
        # Conversations whose title was set by the first-50-char fallback.
        self._fallback_title_by_conv: dict[int, str] = {}
        # Active TitleWorker references keyed by conv_id.
        self._title_workers: dict[int, TitleWorker] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def should_generate_title(self, conv_id: int) -> bool:
        """Return True if this conversation should get an LLM auto-title."""
        if conv_id in self._title_generation_attempted:
            return False
        conv = self._db.get_conversation(conv_id)
        if conv is None:
            return False
        if conv.title == "New Chat":
            return True
        return conv.title == self._fallback_title_by_conv.get(conv_id)

    def mark_attempted(self, conv_id: int) -> None:
        """Record that title generation was attempted (success or fail)."""
        self._title_generation_attempted.add(conv_id)

    def set_fallback_title(self, conv_id: int, title: str) -> None:
        """Record the first-50-char fallback title for a conversation."""
        self._fallback_title_by_conv[conv_id] = title

    def clear_fallback(self, conv_id: int) -> None:
        """Remove the fallback title record for a conversation."""
        self._fallback_title_by_conv.pop(conv_id, None)

    def maybe_start(
        self,
        conv_id: int,
        persisted: list[Message],
        last_target: PersonaTarget,
        router: Any,
    ) -> None:
        """Kick off a background TitleWorker for this conversation.

        Picks the first persisted assistant response's provider.
        Captures the first user message and that response as context.
        Marks as attempted immediately.
        """
        self._title_generation_attempted.add(conv_id)

        all_msgs = self._db.get_messages(conv_id)
        first_user_text = next(
            (m.content for m in all_msgs if m.role == Role.USER and not m.pinned),
            None,
        )
        if not first_user_text or not persisted:
            return
        first_assistant_text = persisted[0].content

        if router is None:
            return
        first_msg = persisted[0]
        provider = router._providers.get(first_msg.provider) if first_msg.provider else None
        if provider is None:
            return
        model = first_msg.model

        worker = TitleWorker(
            conv_id=conv_id,
            provider=provider,
            first_user_text=first_user_text,
            first_assistant_text=first_assistant_text,
            model=model,
        )
        worker.title_ready.connect(self._on_title_ready)
        worker.title_failed.connect(self._on_title_failed)
        if self._sidebar is not None and hasattr(self._sidebar, "set_conversation_title_pending"):
            self._sidebar.set_conversation_title_pending(conv_id, True)
        self._title_workers[conv_id] = worker
        worker.start()

    def apply_auto_title(self, conv_id: int, new_title: str) -> None:
        """Write the auto-generated title if still default or fallback.

        Wrapped in try/except — background nicety, must not crash (#129).
        """
        try:
            conv = self._db.get_conversation(conv_id)
            if conv is None:
                return
            is_default = conv.title == "New Chat"
            is_fallback = conv.title == self._fallback_title_by_conv.get(conv_id)
            if not (is_default or is_fallback):
                return
            self._db.update_conversation_title(conv_id, new_title)
            self._fallback_title_by_conv.pop(conv_id, None)
            if self._session is not None:
                current = self._session.current
                if current and current.id == conv_id:
                    self._session.set_title(new_title)
            if self._sidebar is not None:
                self._sidebar.update_conversation_title(conv_id, new_title)
        except Exception:
            pass

    def stop_all_workers(self, wait_ms: int = 2000) -> None:
        """Request all running TitleWorkers to stop and wait briefly."""
        for worker in list(self._title_workers.values()):
            try:
                if worker.isRunning():
                    worker.requestInterruption()
                    worker.quit()
                    worker.wait(wait_ms)
            except Exception:
                pass
        self._title_workers.clear()

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _on_title_ready(self, conv_id: int, raw_text: str) -> None:
        """Handle a successful TitleWorker completion (main thread)."""
        try:
            self._title_workers.pop(conv_id, None)
            if self._sidebar is not None and hasattr(self._sidebar, "set_conversation_title_pending"):
                self._sidebar.set_conversation_title_pending(conv_id, False)
            cleaned = clean_title(raw_text)
            if cleaned:
                self.apply_auto_title(conv_id, cleaned)
        except Exception:
            pass

    def _on_title_failed(self, conv_id: int) -> None:
        """Handle a TitleWorker error — silent fallback."""
        try:
            self._title_workers.pop(conv_id, None)
            if self._sidebar is not None and hasattr(self._sidebar, "set_conversation_title_pending"):
                self._sidebar.set_conversation_title_pending(conv_id, False)
        except Exception:
            pass
