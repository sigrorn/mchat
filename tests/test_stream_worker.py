# ------------------------------------------------------------------
# Component: test_stream_worker
# Responsibility: Tests for retry logic and error classification
# Collaborators: workers.stream_worker
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.workers.stream_worker import _is_transient


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
