# ------------------------------------------------------------------
# Component: test_stream_worker
# Responsibility: Tests for retry logic, error classification, and
#                 interruption behavior.
# Collaborators: workers.stream_worker  (external: PySide6)
# ------------------------------------------------------------------
from __future__ import annotations

from unittest.mock import MagicMock

from mchat.workers.stream_worker import StreamWorker, _is_transient


class TestIsTransient:
    def test_rate_limit_429(self):
        assert _is_transient(Exception("Error 429: Rate limit exceeded"))

    def test_service_unavailable_503(self):
        assert _is_transient(Exception("503 Service Unavailable"))

    def test_overloaded_529(self):
        assert _is_transient(Exception("529 Overloaded"))

    def test_timeout(self):
        assert _is_transient(Exception("Connection timeout"))

    def test_connection_error(self):
        assert _is_transient(Exception("Connection refused"))

    def test_too_many_requests(self):
        assert _is_transient(Exception("Too many requests"))

    def test_auth_error_not_transient(self):
        assert not _is_transient(Exception("401 Unauthorized"))

    def test_bad_request_not_transient(self):
        assert not _is_transient(Exception("400 Bad Request"))

    def test_generic_error_not_transient(self):
        assert not _is_transient(Exception("Something went wrong"))


class TestInterruption:
    """#175 — StreamWorker must exit cleanly on requestInterruption."""

    def test_interruption_before_stream_emits_nothing(self, qtbot):
        """A worker that's interrupted before streaming should exit
        without emitting stream_complete or stream_error."""
        provider = MagicMock()
        # stream() yields tokens — but we interrupt before it runs
        provider.stream.return_value = iter(["hello"])

        worker = StreamWorker(provider, [], model="test")
        worker.requestInterruption()

        complete_spy = []
        error_spy = []
        worker.stream_complete.connect(lambda *a: complete_spy.append(a))
        worker.stream_error.connect(lambda *a: error_spy.append(a))

        worker.start()
        worker.wait(3000)
        assert not complete_spy
        assert not error_spy

    def test_interruption_during_retry_sleep_exits_quickly(self, qtbot):
        """A worker in retry sleep should exit within ~1s of interruption,
        not block for the full RETRY_DELAY_S."""
        import time

        provider = MagicMock()
        call_count = [0]

        def slow_stream(msgs, model):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("429 Rate limit")
            return iter(["ok"])  # pragma: no cover

        provider.stream.side_effect = slow_stream

        worker = StreamWorker(provider, [], model="test")
        complete_spy = []
        error_spy = []
        worker.stream_complete.connect(lambda *a: complete_spy.append(a))
        worker.stream_error.connect(lambda *a: error_spy.append(a))

        worker.start()
        # Give it time to enter retry sleep
        time.sleep(0.3)
        t0 = time.monotonic()
        worker.requestInterruption()
        worker.wait(3000)
        elapsed = time.monotonic() - t0
        # Should exit well under the full RETRY_DELAY_S (5s)
        assert elapsed < 2.0
        # Should not have emitted completion or error
        assert not complete_spy
        assert not error_spy
