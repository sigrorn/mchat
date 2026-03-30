# ------------------------------------------------------------------
# Component: StreamWorker
# Responsibility: Background thread for streaming LLM responses
# Collaborators: PySide6, providers.base, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from mchat.models.message import Message, Provider
from mchat.providers.base import BaseProvider


class StreamWorker(QThread):
    token_received = Signal(str)
    stream_complete = Signal(str, int, int)  # full text, input_tokens, output_tokens
    stream_error = Signal(str)

    def __init__(
        self,
        provider: BaseProvider,
        messages: list[Message],
        model: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._messages = messages
        self._model = model

    def run(self) -> None:
        full_text = ""
        try:
            for token in self._provider.stream(self._messages, self._model):
                full_text += token
                self.token_received.emit(token)
            usage = self._provider.last_usage or (0, 0)
            self.stream_complete.emit(full_text, usage[0], usage[1])
        except Exception as e:
            self.stream_error.emit(str(e))
