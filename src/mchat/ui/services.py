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
from mchat.ui.state import ConversationSession, ModelCatalog, SelectionState


# IMPORTANT: ServicesContext is intentionally NOT frozen.
#
# It's a long-lived container that every extracted controller holds
# a reference to. Most of the fields are stable for the lifetime of
# the application (config, db, session, selection, model_catalog),
# but ``router`` is rebuilt whenever the user adds or removes an API
# key via the Settings dialog.
#
# Rather than reallocate the ServicesContext on every provider
# rebuild (and then chase down every long-lived collaborator to
# rebind them to the new context), we mutate ``router`` in place.
# That keeps every existing reference in every controller
# automatically pointing at the current router — there is only one
# source of truth, and it's this object.
#
# This is the "option 1" fix from issue #59: context stable, router
# updated in place via ``ServicesContext.set_router()``.


@dataclass
class ServicesContext:
    """Shared long-lived services + application state.

    Intentionally small. Adding anything to this class should be a
    deliberate decision — the goal is to give extracted controllers
    a narrow collaboration root, not to create a new god object.

    Contents:
      * ``config``  — user-editable settings persisted to ~/.mchat/config.json
      * ``db``      — SQLite persistence layer
      * ``router``  — provider registry + parser. Reassignable via
                      ``set_router()`` when providers are reconfigured.
                      Selection state lives in ``selection`` below;
                      router.selection delegates to it.
      * ``session`` — ConversationSession (active conversation + messages)
      * ``selection`` — SelectionState (which personas the next send
                      addresses, as list[PersonaTarget]; renamed from
                      ProviderSelectionState in Stage 2.4 of the
                      personas feature)
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
    selection: SelectionState
    model_catalog: ModelCatalog

    def set_router(self, router: Router | None) -> None:
        """Replace the router reference in place. Called after
        _init_providers runs again (e.g. after the user saves Settings
        with new API keys). All long-lived controllers holding a
        reference to this ServicesContext see the update immediately.
        """
        self.router = router
