# ------------------------------------------------------------------
# Component: stats
# Responsibility: Pure size-statistics helpers for a conversation —
#                 compute whole-chat and limited character counts per
#                 persona (via build_context) plus an "all visibility"
#                 baseline row. Consumed by the //stats command
#                 handler; no Qt dependency so the logic is easy to
#                 test in isolation.
# Collaborators: models.conversation, models.persona, ui.context_builder,
#                ui.persona_target, db, config
# ------------------------------------------------------------------
from __future__ import annotations


# Stub — implementation arrives after the failing tests land.
