# ------------------------------------------------------------------
# Component: TitleWorker
# Responsibility: Background one-shot LLM call that asks a provider to
#                 summarize a conversation's intent in <=25 chars, used
#                 to auto-title brand-new conversations after the first
#                 user→assistant exchange. Independent from StreamWorker
#                 so it doesn't interact with the main send pipeline.
# Collaborators: PySide6 (QThread/Signal), providers.base, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from mchat.models.message import Message, Role
from mchat.providers.base import BaseProvider

# Hard cap on the title we accept from the LLM (post-cleanup).
MAX_TITLE_CHARS = 25

# The instruction we send to the LLM. Keep terse — the model still
# tends to overshoot, so the cleaner does the final enforcement.
TITLE_PROMPT = (
    "Summarize the intent of this conversation in at most "
    f"{MAX_TITLE_CHARS} characters. Reply with ONLY the summary "
    "text — no quotes, no punctuation, no preamble, no explanation. "
    "Just the topic."
)


def clean_title(raw: str) -> str:
    """Normalize an LLM title response.

    - Take the first non-empty line
    - Strip surrounding whitespace and matched quotes
    - Drop a single trailing period
    - Hard-truncate to MAX_TITLE_CHARS
    """
    if not raw:
        return ""
    # First non-empty line
    line = ""
    for candidate in raw.splitlines():
        candidate = candidate.strip()
        if candidate:
            line = candidate
            break
    if not line:
        return ""
    # Strip matched quote pairs
    if len(line) >= 2 and line[0] == line[-1] and line[0] in ('"', "'", "`"):
        line = line[1:-1].strip()
    # Drop a trailing period (but not other punctuation like '?' or '!')
    if line.endswith("."):
        line = line[:-1].rstrip()
    # Hard-truncate
    return line[:MAX_TITLE_CHARS]


class TitleWorker(QThread):
    """One-shot worker that asks a provider to summarize a conversation
    into a short title. Emits ``title_ready(conv_id, raw_text)`` on
    success or ``title_failed(conv_id)`` on any error.
    """

    title_ready = Signal(int, str)
    title_failed = Signal(int)

    def __init__(
        self,
        conv_id: int,
        provider: BaseProvider,
        first_user_text: str,
        first_assistant_text: str,
        model: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._conv_id = conv_id
        self._provider = provider
        self._first_user_text = first_user_text
        self._first_assistant_text = first_assistant_text
        self._model = model

    def run(self) -> None:
        try:
            messages = [
                Message(
                    role=Role.USER,
                    content=(
                        f"{TITLE_PROMPT}\n\n"
                        f"User said: {self._first_user_text}\n\n"
                        f"Assistant replied: {self._first_assistant_text}"
                    ),
                ),
            ]
            full_text = ""
            for token in self._provider.stream(messages, self._model):
                # #129: Honor interruption requests so closeEvent/test
                # teardown can stop this worker cleanly before the
                # parent Python object is gc'd.
                if self.isInterruptionRequested():
                    return
                full_text += token
            if self.isInterruptionRequested():
                return
            self.title_ready.emit(self._conv_id, full_text)
        except Exception:
            # Background nicety — never surface errors to the user.
            if not self.isInterruptionRequested():
                self.title_failed.emit(self._conv_id)
