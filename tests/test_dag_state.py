# ------------------------------------------------------------------
# Component: test_dag_state
# Responsibility: Tests for the pure DagRunState — graph construction,
#                 status transitions, cascade-skip, retry-resume,
#                 induced subgraph, and conv-switch behavior.
# Collaborators: ui.dag_state, models.persona, ui.persona_target
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.models.message import Provider
from mchat.models.persona import Persona
from mchat.ui.dag_state import DagRunState, NodeStatus
from mchat.ui.persona_target import PersonaTarget


# ---- helpers ----

def _persona(pid: str, runs_after: str | None = None, conv_id: int = 1) -> Persona:
    return Persona(
        conversation_id=conv_id,
        id=pid,
        provider=Provider.CLAUDE,
        name=pid.upper(),
        name_slug=pid,
        runs_after=runs_after,
    )


def _target(pid: str) -> PersonaTarget:
    return PersonaTarget(persona_id=pid, provider=Provider.CLAUDE)


# ==================================================================
# Graph construction & induced subgraph
# ==================================================================

class TestBuild:
    def test_simple_chain(self):
        """A→B: A is root, B depends on A."""
        state = DagRunState(run_id=1)
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("a"), _target("b")]
        active_edges = {"b": "a"}
        roots = state.build(targets, personas, active_edges, conv_id=1)

        assert roots == ["a"]
        assert state.children["a"] == ["b"]
        assert state.ancestors["b"] == {"a"}
        assert state.ancestors["a"] == set()
        assert state.status["a"] == NodeStatus.PENDING
        assert state.status["b"] == NodeStatus.PENDING
        assert state.active is True

    def test_two_roots_one_child(self):
        """A(root), B(root), C→A: two parallel roots, one child of A."""
        state = DagRunState(run_id=1)
        personas = [
            _persona("a"), _persona("b"), _persona("c", runs_after="a")
        ]
        targets = [_target("a"), _target("b"), _target("c")]
        active_edges = {"c": "a"}
        roots = state.build(targets, personas, active_edges, conv_id=1)

        assert set(roots) == {"a", "b"}
        assert state.children["a"] == ["c"]
        assert state.children["b"] == []

    def test_induced_subgraph_parent_not_selected(self):
        """#172: If B depends on A but only B is selected, B becomes root."""
        state = DagRunState(run_id=1)
        # Full persona list includes A and B, but only B is targeted.
        # active_edges is empty because A is not in the target set.
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("b")]
        active_edges = {}  # "a" not in target set, so edge is inactive
        roots = state.build(targets, personas, active_edges, conv_id=1)

        assert roots == ["b"]
        assert state.status["b"] == NodeStatus.PENDING

    def test_deep_chain(self):
        """A→B→C→D: ancestors accumulate correctly."""
        state = DagRunState(run_id=1)
        personas = [
            _persona("a"),
            _persona("b", runs_after="a"),
            _persona("c", runs_after="b"),
            _persona("d", runs_after="c"),
        ]
        targets = [_target(p) for p in ("a", "b", "c", "d")]
        active_edges = {"b": "a", "c": "b", "d": "c"}
        roots = state.build(targets, personas, active_edges, conv_id=1)

        assert roots == ["a"]
        assert state.ancestors["d"] == {"a", "b", "c"}
        assert state.ancestors["c"] == {"a", "b"}
        assert state.ancestors["b"] == {"a"}


# ==================================================================
# Node status transitions
# ==================================================================

class TestStatusTransitions:
    def _setup_chain(self) -> DagRunState:
        """A→B→C chain."""
        state = DagRunState(run_id=1)
        personas = [
            _persona("a"),
            _persona("b", runs_after="a"),
            _persona("c", runs_after="b"),
        ]
        targets = [_target("a"), _target("b"), _target("c")]
        active_edges = {"b": "a", "c": "b"}
        state.build(targets, personas, active_edges, conv_id=1)
        return state

    def test_mark_running(self):
        state = self._setup_chain()
        state.mark_running("a")
        assert state.status["a"] == NodeStatus.RUNNING

    def test_mark_completed_returns_children(self):
        state = self._setup_chain()
        state.mark_running("a")
        launchable = state.mark_completed("a")
        assert launchable == ["b"]
        assert state.status["a"] == NodeStatus.COMPLETED
        assert state.status["b"] == NodeStatus.PENDING

    def test_mark_completed_only_pending_children(self):
        """Only PENDING children are returned — already-running/failed are not."""
        state = self._setup_chain()
        state.mark_running("a")
        state.status["b"] = NodeStatus.RUNNING  # force non-pending
        launchable = state.mark_completed("a")
        assert launchable == []


# ==================================================================
# Error cascade
# ==================================================================

class TestErrorCascade:
    def test_parent_failure_skips_descendants(self):
        """A→B→C: A fails → B and C are skipped."""
        state = DagRunState(run_id=1)
        personas = [
            _persona("a"),
            _persona("b", runs_after="a"),
            _persona("c", runs_after="b"),
        ]
        targets = [_target("a"), _target("b"), _target("c")]
        active_edges = {"b": "a", "c": "b"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")

        skipped = state.mark_failed("a")
        assert set(skipped) == {"b", "c"}
        assert state.status["a"] == NodeStatus.FAILED
        assert state.status["b"] == NodeStatus.SKIPPED
        assert state.status["c"] == NodeStatus.SKIPPED

    def test_mid_chain_failure(self):
        """A→B→C: A completes, B fails → only C skipped."""
        state = DagRunState(run_id=1)
        personas = [
            _persona("a"),
            _persona("b", runs_after="a"),
            _persona("c", runs_after="b"),
        ]
        targets = [_target("a"), _target("b"), _target("c")]
        active_edges = {"b": "a", "c": "b"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_completed("a")
        state.mark_running("b")

        skipped = state.mark_failed("b")
        assert skipped == ["c"]
        assert state.status["a"] == NodeStatus.COMPLETED
        assert state.status["b"] == NodeStatus.FAILED
        assert state.status["c"] == NodeStatus.SKIPPED

    def test_failure_records_retry_run_id(self):
        state = DagRunState(run_id=42)
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("a"), _target("b")]
        active_edges = {"b": "a"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_failed("a")

        assert state.retry_run_ids["a"] == 42

    def test_is_done_after_failure_cascade(self):
        """After failure + cascade, no pending/running → is_done."""
        state = DagRunState(run_id=1)
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("a"), _target("b")]
        active_edges = {"b": "a"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_failed("a")

        assert state.is_done()

    def test_parallel_branches_independent(self):
        """A(root), B(root), C→A, D→B: A fails → C skipped, B+D unaffected."""
        state = DagRunState(run_id=1)
        personas = [
            _persona("a"), _persona("b"),
            _persona("c", runs_after="a"), _persona("d", runs_after="b"),
        ]
        targets = [_target(p) for p in ("a", "b", "c", "d")]
        active_edges = {"c": "a", "d": "b"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_running("b")

        skipped = state.mark_failed("a")
        assert skipped == ["c"]
        assert state.status["b"] == NodeStatus.RUNNING
        assert state.status["d"] == NodeStatus.PENDING


# ==================================================================
# Retry auto-resume (#172)
# ==================================================================

class TestRetryResume:
    def test_retry_resumes_skipped_children(self):
        """A→B: A fails (B skipped), retry A → B promoted to PENDING."""
        state = DagRunState(run_id=5)
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("a"), _target("b")]
        active_edges = {"b": "a"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_failed("a")
        assert state.status["b"] == NodeStatus.SKIPPED

        # Simulate successful retry
        launchable = state.retry_resume("a")
        assert launchable == ["b"]
        assert state.status["a"] == NodeStatus.COMPLETED
        assert state.status["b"] == NodeStatus.PENDING

    def test_retry_deep_chain(self):
        """A→B→C: A fails (B,C skipped), retry A → only B promoted (not C)."""
        state = DagRunState(run_id=5)
        personas = [
            _persona("a"),
            _persona("b", runs_after="a"),
            _persona("c", runs_after="b"),
        ]
        targets = [_target("a"), _target("b"), _target("c")]
        active_edges = {"b": "a", "c": "b"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_failed("a")

        launchable = state.retry_resume("a")
        assert launchable == ["b"]  # Only direct children
        assert state.status["a"] == NodeStatus.COMPLETED
        assert state.status["b"] == NodeStatus.PENDING
        assert state.status["c"] == NodeStatus.SKIPPED  # Still skipped until B runs

    def test_stale_retry_returns_empty(self):
        """Retry from a previous run_id should not resume anything."""
        state = DagRunState(run_id=5)
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("a"), _target("b")]
        active_edges = {"b": "a"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_failed("a")

        # Simulate a new send that increments the run_id
        state.run_id = 6
        launchable = state.retry_resume("a")
        assert launchable == []
        # Status should NOT have changed
        assert state.status["a"] == NodeStatus.FAILED

    def test_non_dag_retry_returns_empty(self):
        """Retry with no recorded retry_run_id (non-DAG failure)."""
        state = DagRunState(run_id=5)
        # No build() — simulating a non-DAG context
        launchable = state.retry_resume("a")
        assert launchable == []


# ==================================================================
# Conversation switch
# ==================================================================

class TestConvSwitch:
    def test_conv_switch_skips_all_pending(self):
        """All PENDING nodes become SKIPPED on conv switch."""
        state = DagRunState(run_id=1)
        personas = [
            _persona("a"), _persona("b", runs_after="a"),
            _persona("c", runs_after="b"),
        ]
        targets = [_target("a"), _target("b"), _target("c")]
        active_edges = {"b": "a", "c": "b"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")

        skipped = state.mark_skipped_on_conv_switch()
        assert set(skipped) == {"b", "c"}
        assert state.status["a"] == NodeStatus.RUNNING  # still running
        assert state.status["b"] == NodeStatus.SKIPPED
        assert state.status["c"] == NodeStatus.SKIPPED


# ==================================================================
# visible_set (for context filtering)
# ==================================================================

class TestVisibleSet:
    def test_root_visible_set_is_self_only(self):
        state = DagRunState(run_id=1)
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("a"), _target("b")]
        active_edges = {"b": "a"}
        state.build(targets, personas, active_edges, conv_id=1)

        assert state.visible_set("a") == {"a"}

    def test_child_visible_set_includes_ancestors(self):
        state = DagRunState(run_id=1)
        personas = [
            _persona("a"),
            _persona("b", runs_after="a"),
            _persona("c", runs_after="b"),
        ]
        targets = [_target("a"), _target("b"), _target("c")]
        active_edges = {"b": "a", "c": "b"}
        state.build(targets, personas, active_edges, conv_id=1)

        assert state.visible_set("b") == {"a", "b"}
        assert state.visible_set("c") == {"a", "b", "c"}


# ==================================================================
# is_done
# ==================================================================

class TestIsDone:
    def test_not_done_while_running(self):
        state = DagRunState(run_id=1)
        personas = [_persona("a")]
        targets = [_target("a")]
        state.build(targets, personas, {}, conv_id=1)
        state.mark_running("a")
        assert not state.is_done()

    def test_done_when_all_completed(self):
        state = DagRunState(run_id=1)
        personas = [_persona("a"), _persona("b")]
        targets = [_target("a"), _target("b")]
        state.build(targets, personas, {}, conv_id=1)
        state.mark_running("a")
        state.mark_completed("a")
        state.mark_running("b")
        state.mark_completed("b")
        assert state.is_done()

    def test_done_with_mixed_completed_failed_skipped(self):
        state = DagRunState(run_id=1)
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("a"), _target("b")]
        active_edges = {"b": "a"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_failed("a")
        assert state.is_done()


# ==================================================================
# clear()
# ==================================================================

class TestClear:
    def test_clear_preserves_retry_run_ids(self):
        """retry_run_ids survive clear() so RETRY-mode sends can match."""
        state = DagRunState(run_id=5)
        personas = [_persona("a"), _persona("b", runs_after="a")]
        targets = [_target("a"), _target("b")]
        active_edges = {"b": "a"}
        state.build(targets, personas, active_edges, conv_id=1)
        state.mark_running("a")
        state.mark_failed("a")

        state.clear()
        assert state.retry_run_ids == {"a": 5}  # preserved
        assert state.status == {}  # cleared
        assert state.active is False
