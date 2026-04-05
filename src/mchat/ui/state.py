# ------------------------------------------------------------------
# Component: state
# Responsibility: Explicit application-state objects that replace the
#                 previous pattern of stashing state on MainWindow and
#                 having controllers reach back for it through a host
#                 reference. Each class is a QObject with named
#                 mutation methods and Qt signals — the rest of the
#                 codebase is already organised around signals, so
#                 controllers can subscribe directly instead of being
#                 imperatively called from the window.
# Collaborators: PySide6 (QObject/Signal), models.conversation,
#                models.message
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider


class ConversationSession(QObject):
    """The single source of truth for which conversation is currently
    active and what messages it contains.

    The API is deliberately narrow — named mutation methods, no field
    poking — so callers that reach for the underlying conversation
    object for reads still work, but every write goes through a
    method that emits the right signal.
    """

    # Fired when the active conversation changes (including to None).
    conversation_changed = Signal(object)  # Conversation | None
    # Fired when the list of messages on the active conversation changes,
    # either via full replacement or append/remove.
    messages_changed = Signal()
    # Fired when the active conversation's title changes. Sidebar
    # listeners can use this instead of being called imperatively.
    title_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current: Conversation | None = None

    # -- Read access -------------------------------------------------

    @property
    def current(self) -> Conversation | None:
        return self._current

    @property
    def messages(self) -> list[Message]:
        return self._current.messages if self._current else []

    def is_active(self) -> bool:
        return self._current is not None

    # -- Mutations ---------------------------------------------------

    def set_current(
        self,
        conversation: Conversation | None,
        messages: list[Message] | None = None,
    ) -> None:
        """Switch the active conversation.

        If ``messages`` is provided, it replaces the conversation's
        ``messages`` list before the signal fires. Passing ``None`` here
        leaves whatever list is already on the Conversation object in
        place — callers that have already attached a loaded list can
        skip the argument.
        """
        self._current = conversation
        if conversation is not None and messages is not None:
            conversation.messages = messages
        self.conversation_changed.emit(conversation)
        if conversation is not None:
            self.messages_changed.emit()

    def clear(self) -> None:
        """Drop the active conversation entirely."""
        self._current = None
        self.conversation_changed.emit(None)

    def set_messages(self, messages: list[Message]) -> None:
        if self._current is None:
            return
        self._current.messages = messages
        self.messages_changed.emit()

    def append_message(self, message: Message) -> None:
        if self._current is None:
            return
        self._current.messages.append(message)
        self.messages_changed.emit()

    def set_title(self, title: str) -> None:
        if self._current is None:
            return
        self._current.title = title
        self.title_changed.emit(title)

    def set_limit_mark(self, mark: str | None) -> None:
        if self._current is None:
            return
        self._current.limit_mark = mark
        # Limit affects what's sent, not the message list itself, but
        # rendering needs to know — callers typically follow this with
        # a display refresh. We emit messages_changed to keep the model
        # honest about "something that affects display has changed."
        self.messages_changed.emit()

    def set_visibility_matrix(self, matrix: dict[str, list[str]]) -> None:
        if self._current is None:
            return
        self._current.visibility_matrix = matrix

    def set_last_provider(self, value: str) -> None:
        if self._current is None:
            return
        self._current.last_provider = value


class SelectionState(QObject):
    """The mutable "which personas does the next send address?" state.

    Holds a list of PersonaTarget objects — one per addressed persona.
    Before Stage 2.4 this was ``ProviderSelectionState`` holding a
    list of Provider enum members; generalising to PersonaTarget lets
    same-provider personas coexist in the selection (the Italian-tutor
    scenario where three Claude personas can all be addressed at once).

    Subscribers connect to ``selection_changed`` instead of being
    imperatively notified from MainWindow. Legacy callers that just
    want "which providers are in the selection right now" can use
    ``providers_only()`` to get a deduplicated list[Provider].
    """

    selection_changed = Signal(list)  # list[PersonaTarget]

    def __init__(
        self,
        default: list | None = None,  # list[PersonaTarget]
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._selection: list = list(default) if default else []

    @property
    def selection(self) -> list:
        """Return a copy of the current selection as a list[PersonaTarget]."""
        return list(self._selection)

    def providers_only(self) -> list[Provider]:
        """Return the deduplicated list of Providers referenced by the
        current selection, in order of first appearance. Used by code
        that genuinely only cares about providers (e.g. Router's
        public .selection property, which preserves its historical
        list[Provider] interface for back-compat)."""
        seen: list[Provider] = []
        for t in self._selection:
            if t.provider not in seen:
                seen.append(t.provider)
        return seen

    def set(self, targets: list) -> None:
        """Replace the full selection with ``targets`` (list[PersonaTarget]).
        No-op if the list is empty — callers that want to 'clear' should
        handle the empty-selection case at the UI layer (we never want
        to send with nothing selected)."""
        if not targets:
            return
        new = list(targets)
        if new == self._selection:
            return
        self._selection = new
        self.selection_changed.emit(list(self._selection))


# Backwards-compatibility alias. Some call sites and tests still import
# the old name; removing those is mechanical and can happen in a
# follow-up cleanup commit if desired. The alias lets the rename land
# without touching every spelling in the same commit.
ProviderSelectionState = SelectionState


class ModelCatalog(QObject):
    """Per-provider cache of known model ids.

    Replaces the previous pattern where MainWindow harvested combo-box
    contents as a models cache when opening Settings. The catalog is
    the source of truth; widgets render from it (or from direct
    provider queries in the background-refresh path).
    """

    # Fired whenever a provider's model list is replaced.
    models_changed = Signal(Provider)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._models: dict[Provider, list[str]] = {}

    def get(self, provider: Provider) -> list[str]:
        return list(self._models.get(provider, []))

    def all(self) -> dict[Provider, list[str]]:
        return {p: list(m) for p, m in self._models.items()}

    def set(self, provider: Provider, models: list[str]) -> None:
        new = list(models)
        if self._models.get(provider) == new:
            return
        self._models[provider] = new
        self.models_changed.emit(provider)

    def clear(self) -> None:
        providers = list(self._models.keys())
        self._models.clear()
        for p in providers:
            self.models_changed.emit(p)
