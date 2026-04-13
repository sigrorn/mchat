# ------------------------------------------------------------------
# Component: PersonaService
# Responsibility: Service-level persona operations extracted from
#                 PersonaDialog (#160). Pure Python — no Qt dependency.
#                 Owns create, update, remove, reorder, import, export,
#                 and effective-value resolution for personas within
#                 a single conversation.
# Collaborators: db, config, models.persona, ui.persona_resolution
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Provider
from mchat.models.persona import (
    Persona,
    generate_persona_id,
    slugify_persona_name,
    validate_persona_name,
)
from mchat.ui.persona_resolution import (
    resolve_persona_color,
    resolve_persona_model,
    resolve_persona_prompt,
)


class PersonaImportError(ValueError):
    """Raised when a .md persona import file fails pre-flight
    validation. Message lists every offending row in one go so the
    user can fix them all at once (#140)."""


class PersonaService:
    """Service-level persona operations for one conversation.

    No Qt dependency — testable without a running event loop.
    PersonaDialog delegates to an instance of this class.
    """

    def __init__(
        self,
        db: Database,
        config: Config,
        conversation_id: int,
    ) -> None:
        self._db = db
        self._config = config
        self._conv_id = conversation_id

    @property
    def conversation_id(self) -> int:
        return self._conv_id

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_items(self) -> list[Persona]:
        """Return the active personas for this conversation, in their
        display order (sort_order, then id)."""
        return self._db.list_personas(self._conv_id)

    def create_persona(
        self,
        provider: Provider,
        name: str,
        system_prompt_override: str | None = None,
        model_override: str | None = None,
        color_override: str | None = None,
        created_at_message_index: int | None = None,
        _validate: bool = True,
    ) -> Persona:
        """Insert a new persona row for this conversation.

        #140: validates the name against the alphabet + reserved-token
        rules. Raises ValueError on violation, sqlite3.IntegrityError
        on slug collision.

        #155: pass _validate=False for import paths that accept reserved
        names so the user can rename them in the dialog.
        """
        if _validate:
            validate_persona_name(name)
        p = Persona(
            conversation_id=self._conv_id,
            id=generate_persona_id(),
            provider=provider,
            name=name,
            name_slug=slugify_persona_name(name),
            system_prompt_override=system_prompt_override,
            model_override=model_override,
            color_override=color_override,
            created_at_message_index=created_at_message_index,
            sort_order=self._db.next_persona_sort_order(self._conv_id),
        )
        self._db.create_persona(p)
        return p

    def update_persona(
        self,
        persona_id: str,
        system_prompt_override: str | None = ...,
        model_override: str | None = ...,
        color_override: str | None = ...,
    ) -> None:
        """Update an existing persona's override fields. A sentinel
        (...) means 'leave this field alone'; None means 'clear the
        override so it inherits from global'."""
        for p in self._db.list_personas(self._conv_id):
            if p.id == persona_id:
                if system_prompt_override is not ...:
                    p.system_prompt_override = system_prompt_override
                if model_override is not ...:
                    p.model_override = model_override
                if color_override is not ...:
                    p.color_override = color_override
                self._db.update_persona(p)
                return
        raise ValueError(f"persona {persona_id!r} not found")

    def remove_persona(self, persona_id: str) -> None:
        """Tombstone the persona (D3 — never hard-delete)."""
        self._db.tombstone_persona(self._conv_id, persona_id)

    # ------------------------------------------------------------------
    # Reordering
    # ------------------------------------------------------------------

    def move_persona_up(self, persona_id: str) -> None:
        """Swap sort_order with the persona above (lower sort_order)."""
        self._swap_sort_order(persona_id, direction=-1)

    def move_persona_down(self, persona_id: str) -> None:
        """Swap sort_order with the persona below (higher sort_order)."""
        self._swap_sort_order(persona_id, direction=1)

    def _swap_sort_order(self, persona_id: str, direction: int) -> None:
        """Swap sort_order between the target persona and its neighbor."""
        personas = self.list_items()
        idx = next((i for i, p in enumerate(personas) if p.id == persona_id), None)
        if idx is None:
            return
        neighbor_idx = idx + direction
        if neighbor_idx < 0 or neighbor_idx >= len(personas):
            return
        a, b = personas[idx], personas[neighbor_idx]
        a.sort_order, b.sort_order = b.sort_order, a.sort_order
        if a.sort_order == b.sort_order:
            a.sort_order = neighbor_idx
            b.sort_order = idx
        self._db.update_persona(a)
        self._db.update_persona(b)

    # ------------------------------------------------------------------
    # Export / Import
    # ------------------------------------------------------------------

    def export_personas_md(self) -> str:
        """Serialize all active personas to a human-readable .md string."""
        personas = self.list_items()
        lines: list[str] = ["# Personas", ""]
        for i, p in enumerate(personas):
            if i > 0:
                lines.append("---")
                lines.append("")
            lines.append(f"## {p.name}")
            lines.append(f"- Provider: {p.provider.value}")
            mode = "inherit" if p.created_at_message_index is None else "new"
            lines.append(f"- Mode: {mode}")
            lines.append(f"- Model override: {p.model_override or '(none)'}")
            lines.append(f"- Color override: {p.color_override or '(none)'}")
            lines.append("- Prompt:")
            lines.append("")
            lines.append(p.system_prompt_override or "(none)")
            lines.append("")
        return "\n".join(lines)

    def import_personas_md(self, md: str) -> None:
        """Parse a .md string and replace all active personas.

        Existing personas are tombstoned (not deleted). Structural
        errors abort entirely; reserved names are accepted so the
        UI can flag them for rename (#155).
        """
        import re

        from mchat.ui.persona_resolver import RESERVED_NAMES

        parsed: list[dict] = []
        errors: list[str] = []
        sections = re.split(r"\n---\n|\n(?=## )", md)
        for section in sections:
            section = section.strip()
            if not section or section.startswith("# Personas"):
                if "## " not in section:
                    continue
                idx = section.index("## ")
                section = section[idx:]

            name_match = re.match(r"^## (.+)$", section, re.MULTILINE)
            if not name_match:
                continue
            name = name_match.group(1).strip()

            def _field(label: str) -> str | None:
                m = re.search(
                    rf"^- {label}:\s*(.*)$", section, re.MULTILINE,
                )
                if m:
                    val = m.group(1).strip()
                    return None if val == "(none)" else val
                return None

            try:
                validate_persona_name(name)
            except ValueError as e:
                is_reserved = name.lower() in RESERVED_NAMES
                if not is_reserved:
                    errors.append(f"{name!r}: {e}")
                    continue

            provider_str = _field("Provider") or "claude"
            try:
                provider = Provider(provider_str)
            except ValueError:
                errors.append(
                    f"{name!r}: unknown provider {provider_str!r}"
                )
                continue

            mode = _field("Mode") or "inherit"
            model_override = _field("Model override")
            color_override = _field("Color override")

            prompt_match = re.search(
                r"^- Prompt:\s*\n\n(.*)", section, re.MULTILINE | re.DOTALL,
            )
            prompt = None
            if prompt_match:
                prompt = prompt_match.group(1).strip()
                if prompt == "(none)":
                    prompt = None

            cutoff = None if mode == "inherit" else 0

            parsed.append({
                "name": name,
                "provider": provider,
                "prompt": prompt,
                "model_override": model_override,
                "color_override": color_override,
                "cutoff": cutoff,
            })

        seen: dict[str, str] = {}
        for row in parsed:
            lower = row["name"].lower()
            if lower in seen:
                errors.append(
                    f"duplicate/case-collision: {seen[lower]!r} "
                    f"and {row['name']!r} resolve to the same slug"
                )
            else:
                seen[lower] = row["name"]

        if errors:
            raise PersonaImportError(
                "Cannot import — " + " | ".join(errors)
            )

        for p in self.list_items():
            self.remove_persona(p.id)

        for import_idx, row in enumerate(parsed):
            persona = self.create_persona(
                provider=row["provider"],
                name=row["name"],
                system_prompt_override=row["prompt"],
                model_override=row["model_override"],
                color_override=row["color_override"],
                created_at_message_index=row["cutoff"],
                _validate=False,
            )
            persona.sort_order = import_idx
            self._db.update_persona(persona)

    # ------------------------------------------------------------------
    # Effective-value resolution
    # ------------------------------------------------------------------

    def effective_prompt(self, persona: Persona) -> str:
        return resolve_persona_prompt(persona, self._config)

    def effective_model(self, persona: Persona) -> str:
        return resolve_persona_model(persona, self._config)

    def effective_color(self, persona: Persona) -> str:
        return resolve_persona_color(persona, self._config)
