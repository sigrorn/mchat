# ------------------------------------------------------------------
# Component: DagRunState
# Responsibility: Pure-data DAG execution state — graph construction,
#                 node status tracking, ancestor/children queries,
#                 cascade-skip logic, and retry-resume decisions.
#                 No Qt, no I/O, no side-effects.
# Collaborators: services.persona_service, ui.persona_target
# ------------------------------------------------------------------
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mchat.models.persona import Persona
    from mchat.ui.persona_target import PersonaTarget


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DagRunState:
    """Pure-data graph state for one DAG send.

    Built by ``build()`` from a target set and persona list.
    All mutations are explicit method calls that return lists of
    side-effect descriptors rather than performing I/O directly.
    """

    run_id: int = 0
    conv_id: int | None = None
    active: bool = False

    status: dict[str, NodeStatus] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)
    ancestors: dict[str, set[str]] = field(default_factory=dict)
    targets: dict[str, PersonaTarget] = field(default_factory=dict)

    # Per-persona dag_run_id at time of failure (for retry matching)
    retry_run_ids: dict[str, int] = field(default_factory=dict)

    def clear(self) -> None:
        self.status.clear()
        self.children.clear()
        self.ancestors.clear()
        self.targets.clear()
        self.active = False
        self.conv_id = None
        # retry_run_ids intentionally NOT cleared — they survive across
        # DAG state clears so a RETRY mode send can still match.

    def build(
        self,
        targets: list[PersonaTarget],
        personas: list[Persona],
        active_edges: dict[str, str],
        conv_id: int | None,
    ) -> list[str]:
        """Build the induced DAG over targets. Returns root persona_ids."""
        from mchat.services.persona_service import get_ancestor_persona_ids

        self.conv_id = conv_id
        self.active = True

        target_ids = {t.persona_id for t in targets}
        self.targets = {t.persona_id: t for t in targets}

        # Build children map
        self.children = {pid: [] for pid in target_ids}
        for child_id, parent_id in active_edges.items():
            self.children.setdefault(parent_id, []).append(child_id)

        # Compute ancestors for each target (only within target set)
        for pid in target_ids:
            self.ancestors[pid] = get_ancestor_persona_ids(
                pid, [p for p in personas if p.id in target_ids]
            )

        # Identify roots (no active parent in target set)
        roots = [pid for pid in target_ids if pid not in active_edges]

        # Set initial statuses
        for pid in target_ids:
            self.status[pid] = NodeStatus.PENDING

        return roots

    def visible_set(self, persona_id: str) -> set[str]:
        """Return {self} ∪ {ancestors} for context filtering."""
        return {persona_id} | self.ancestors.get(persona_id, set())

    def mark_running(self, persona_id: str) -> None:
        self.status[persona_id] = NodeStatus.RUNNING

    def mark_completed(self, persona_id: str) -> list[str]:
        """Mark a node completed. Returns child persona_ids that should
        be launched (those currently PENDING)."""
        self.status[persona_id] = NodeStatus.COMPLETED
        launchable: list[str] = []
        for child_pid in self.children.get(persona_id, []):
            if self.status.get(child_pid) == NodeStatus.PENDING:
                launchable.append(child_pid)
        return launchable

    def mark_failed(self, persona_id: str) -> list[str]:
        """Mark a node failed. Returns list of all descendant persona_ids
        that were cascade-skipped (recursively)."""
        self.status[persona_id] = NodeStatus.FAILED
        self.retry_run_ids[persona_id] = self.run_id
        return self._cascade_skip(persona_id)

    def _cascade_skip(self, parent_pid: str) -> list[str]:
        """Recursively skip all pending descendants. Returns skipped pids."""
        skipped: list[str] = []
        for child_pid in self.children.get(parent_pid, []):
            if self.status.get(child_pid) == NodeStatus.PENDING:
                self.status[child_pid] = NodeStatus.SKIPPED
                skipped.append(child_pid)
                skipped.extend(self._cascade_skip(child_pid))
        return skipped

    def mark_skipped_on_conv_switch(self) -> list[str]:
        """Mark all PENDING nodes as SKIPPED (conversation was switched).
        Returns the list of skipped persona_ids."""
        skipped: list[str] = []
        for pid, s in self.status.items():
            if s == NodeStatus.PENDING:
                self.status[pid] = NodeStatus.SKIPPED
                skipped.append(pid)
        return skipped

    def is_done(self) -> bool:
        """True when no nodes are RUNNING or PENDING."""
        return not any(
            s in (NodeStatus.RUNNING, NodeStatus.PENDING)
            for s in self.status.values()
        )

    def retry_resume(self, persona_id: str) -> list[str]:
        """After a successful retry of a failed node, mark it completed
        and return child persona_ids to launch.

        Returns empty list if the retry doesn't match the current run_id
        (stale retry from a previous send).
        """
        stored_run_id = self.retry_run_ids.get(persona_id)
        if stored_run_id is None or stored_run_id != self.run_id:
            return []

        self.status[persona_id] = NodeStatus.COMPLETED
        # Promote skipped children back to pending, then return them
        launchable: list[str] = []
        for child_pid in self.children.get(persona_id, []):
            if self.status.get(child_pid) == NodeStatus.SKIPPED:
                self.status[child_pid] = NodeStatus.PENDING
                launchable.append(child_pid)
        return launchable
