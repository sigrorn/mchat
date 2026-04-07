# ------------------------------------------------------------------
# Component: StreamWorker
# Responsibility: Background thread for streaming LLM responses
# Collaborators: PySide6, providers.base, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from mchat.models.message import Message, Provider
from mchat.providers.base import BaseProvider

# HTTP status codes considered transient (worth retrying)
_TRANSIENT_CODES = {429, 503, 529}

MAX_RETRIES = 3
RETRY_DELAY_S = 5


def _is_transient(exc: Exception) -> bool:
    """Check if an exception is a transient/retryable error."""
    exc_str = str(exc).lower()
    # Check for HTTP status codes in the exception
    for code in _TRANSIENT_CODES:
        if str(code) in str(exc):
            return True
    # Connection / timeout errors
    for keyword in ("timeout", "connection", "temporarily", "overloaded", "rate limit", "rate_limit", "too many requests"):
        if keyword in exc_str:
            return True
    return False


class StreamWorker(QThread):
    token_received = Signal(str)
    # full text, input_tokens, output_tokens, estimated
    stream_complete = Signal(str, int, int, bool)
    stream_error = Signal(str)
    # Emitted when entering retry mode (attempt number, max retries)
    retrying = Signal(int, int)

    def __init__(
        self,
        provider: BaseProvider,
        messages: list[Message],
        model: str | None = None,
        persona_name: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._messages = messages
        self._model = model
        self._persona_name = persona_name
        self.last_error_transient: bool = False

    def run(self) -> None:
        import mchat.debug_logger as debug_logger
        last_exc: Exception | None = None
        persona = self._persona_name or self._provider.display_name

        # Log outgoing context
        if debug_logger.enabled:
            for m in self._messages:
                role = m.role.value if hasattr(m.role, "value") else str(m.role)
                debug_logger.log_outgoing(persona, f"[{role}] {m.content}")

        for attempt in range(1, MAX_RETRIES + 1):
            full_text = ""
            try:
                for token in self._provider.stream(self._messages, self._model):
                    full_text += token
                    self.token_received.emit(token)
                # Log incoming response
                if debug_logger.enabled:
                    debug_logger.log_incoming(persona, full_text)
                usage = self._provider.last_usage or (0, 0)
                estimated = self._provider.last_usage_estimated
                self.stream_complete.emit(full_text, usage[0], usage[1], estimated)
                return  # success
            except Exception as e:
                last_exc = e
                if _is_transient(e) and attempt < MAX_RETRIES:
                    self.last_error_transient = True
                    self.retrying.emit(attempt, MAX_RETRIES)
                    time.sleep(RETRY_DELAY_S)
                    continue
                else:
                    break

        # All retries exhausted or non-transient error
        self.last_error_transient = _is_transient(last_exc) if last_exc else False
        self.stream_error.emit(str(last_exc))
