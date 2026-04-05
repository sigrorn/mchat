# ------------------------------------------------------------------
# Component: ServicesContext
# Responsibility: A deliberately small bundle of the long-lived
#                 services and state objects that multiple controllers
#                 need. Passed into extracted modules instead of a
#                 full MainWindow reference so controllers can
#                 collaborate through a narrow, typed surface.
#                 The context intentionally does NOT include anything
#                 presentational (chat widget, sidebar, input widget);
#                 those stay out to keep the boundary clear.
# Collaborators: config, db, router, state
# ------------------------------------------------------------------
from __future__ import annotations

from dataclasses import dataclass

from mchat.config import Config
from mchat.db import Database
from mchat.router import Router
from mchat.ui.state import ConversationSession, ModelCatalog, ProviderSelectionState


@dataclass(frozen=True)
class ServicesContext:
    """Shared long-lived services + application state.

    Intentionally small. Adding anything to this dataclass should be
    a deliberate decision — the goal is to give extracted controllers
    a narrow collaboration root, not to create a new god object.

    Contents:
      * ``config``  — user-editable settings persisted to ~/.mchat/config.json
      * ``db``      — SQLite persistence layer
      * ``router``  — provider registry + parser. Selection state lives
                      in ``selection`` below; router.selection delegates
                      to it, so the two are consistent.
      * ``session`` — ConversationSession (active conversation + messages)
      * ``selection`` — ProviderSelectionState (which providers the next
                      send addresses)
      * ``model_catalog`` — ModelCatalog (cached model ids per provider)

    Notable omissions:
      * Chat widget, sidebar, input widget, provider panel — presentation,
        not services. Controllers that need to drive them should either
        receive an explicit widget-facing interface (see ``commands.host``)
        or emit signals that UI code observes.
      * Font size, window geometry — preferences, held by
        ``PreferencesAdapter`` rather than being globally shared.
    """

    config: Config
    db: Database
    router: Router | None
    session: ConversationSession
    selection: ProviderSelectionState
    model_catalog: ModelCatalog
