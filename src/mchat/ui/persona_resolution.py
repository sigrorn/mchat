# ------------------------------------------------------------------
# Component: persona_resolution
# Responsibility: Shared "null-means-inherit" helpers that resolve a
#                 persona's effective prompt / model / colour at
#                 send time or render time. These are the D6b
#                 resolution helpers — every call site uses these
#                 functions rather than duplicating the fallback
#                 logic, so the inherit rules never drift.
# Collaborators: config, models.persona
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.config import Config
from mchat.models.persona import Persona


def resolve_persona_prompt(persona: Persona, config: Config) -> str:
    """Return the effective system prompt for a persona.

    Per D6: if ``persona.system_prompt_override`` is not None, use it
    (the "I've set a per-persona prompt" path). Otherwise fall through
    to ``config.get("system_prompt_<provider>")`` — the global provider
    default. This matches the behaviour synthetic default personas
    need (all override fields None → all calls resolve to the global
    default), so synthetic defaults and explicit inherit-everything
    personas are indistinguishable at this layer.
    """
    if persona.system_prompt_override is not None:
        return persona.system_prompt_override
    return config.get(f"system_prompt_{persona.provider.value}")


def resolve_persona_model(persona: Persona, config: Config) -> str:
    """Return the effective model id for a persona.

    Per D6: ``persona.model_override`` wins when set; otherwise
    ``config.get("<provider>_model")`` is the global default. Called
    per-send, per-persona at the moment the StreamWorker is created
    (Stage 2.6), so changing the global model in Settings takes effect
    on the next send for every persona whose override is None.
    """
    if persona.model_override is not None:
        return persona.model_override
    return config.get(f"{persona.provider.value}_model")


def resolve_persona_color(persona: Persona, config: Config) -> str:
    """Return the effective background colour for a persona.

    Per D6: ``persona.color_override`` wins when set; otherwise
    ``config.get("color_<provider>")`` is the global provider colour
    (which is itself what legacy messages without a persona use).
    Called from the chat widget's ``_color_for(message)`` path in
    Stage 3A.2 — the data is here from Stage 2.1 so the chat widget
    change is a trivial wire-up when 3A.2 lands.
    """
    if persona.color_override is not None:
        return persona.color_override
    return config.get(f"color_{persona.provider.value}")
