# ------------------------------------------------------------------
# Component: Database
# Responsibility: SQLite persistence for conversations and messages
# Collaborators: models.conversation, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mchat.config import DEFAULT_CONFIG_DIR
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.models.persona import Persona

DEFAULT_DB_PATH = DEFAULT_CONFIG_DIR / "mchat.db"

# Schema version stored in PRAGMA user_version. Bump this and append a
# new _migration_N method whenever the schema changes. Each migration
# runs exactly once per database, in ascending order, and must be
# idempotent on partially-migrated legacy DBs where safe.
CURRENT_SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'New Chat',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    provider TEXT,
    content TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversation_spend (
    conversation_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0.0,
    estimated INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (conversation_id, provider),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS marks (
    conversation_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    message_count INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, name),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or DEFAULT_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Run every schema migration whose version number is newer than
        the DB's current ``user_version``, then stamp the new version.

        Each migration is a dedicated method (``_migration_N``) and runs
        exactly once per database. Migrations must be written to be safe
        on both fresh and partially-migrated legacy databases.
        """
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        migrations = [
            (1, self._migration_1_initial),
            (2, self._migration_2_personas),
            (3, self._migration_3_send_mode),
        ]
        for version, fn in migrations:
            if current < version:
                fn()
                # user_version is an int pragma — can't be parameterised
                self._conn.execute(f"PRAGMA user_version = {version}")

    def _migration_1_initial(self) -> None:
        """Initial catch-all migration.

        Covers every schema change made before explicit versioning was
        introduced. Written to be idempotent because legacy DBs may be
        at any point along the historical migration timeline.
        """
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(conversations)")
        }
        if "system_prompt" not in cols:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''"
            )
        if "last_provider" not in cols:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN last_provider TEXT NOT NULL DEFAULT ''"
            )
        if "spend_claude" not in cols:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN spend_claude REAL NOT NULL DEFAULT 0.0"
            )
        if "spend_openai" not in cols:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN spend_openai REAL NOT NULL DEFAULT 0.0"
            )
        if "limit_mark" not in cols:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN limit_mark TEXT"
            )
        if "visibility_matrix" not in cols:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN visibility_matrix TEXT NOT NULL DEFAULT '{}'"
            )

        # Migrate spend data from old columns to conversation_spend table
        if "spend_claude" in cols:
            self._conn.execute(
                "INSERT OR IGNORE INTO conversation_spend (conversation_id, provider, amount) "
                "SELECT id, 'claude', spend_claude FROM conversations WHERE spend_claude > 0"
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO conversation_spend (conversation_id, provider, amount) "
                "SELECT id, 'openai', spend_openai FROM conversations WHERE spend_openai > 0"
            )

        # Add estimated column to conversation_spend if missing
        spend_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(conversation_spend)")
        }
        if spend_cols and "estimated" not in spend_cols:
            self._conn.execute(
                "ALTER TABLE conversation_spend ADD COLUMN estimated INTEGER NOT NULL DEFAULT 0"
            )

        # Add hidden column to messages if missing
        msg_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(messages)")
        }
        if "hidden" not in msg_cols:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
            )
        if "display_mode" not in msg_cols:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN display_mode TEXT"
            )
        if "pinned" not in msg_cols:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
            )
        if "pin_target" not in msg_cols:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN pin_target TEXT"
            )
        if "addressed_to" not in msg_cols:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN addressed_to TEXT"
            )

        # Strip legacy "**X's take:**\n\n" prefix from assistant messages
        # (one-time migration — these were stored with the heading before the
        # display-time heading fix)
        for display_name in ("Claude", "GPT", "Gemini", "Perplexity"):
            prefix = f"**{display_name}'s take:**\n\n"
            self._conn.execute(
                "UPDATE messages SET content = SUBSTR(content, ?) "
                "WHERE role = 'assistant' AND content LIKE ?",
                (len(prefix) + 1, f"**{display_name}'s take:**%"),
            )

    def _migration_2_personas(self) -> None:
        """Add the personas table and the messages.persona_id column.

        See docs/plans/personas.md § Stage 1.2. The migration is
        purely additive:
          * new `personas` table keyed by (conversation_id, id) with
            a partial unique index on (conversation_id, name_slug)
            WHERE deleted_at IS NULL so tombstoned rows don't block
            slug reuse.
          * new `messages.persona_id` column, NULL for legacy rows.

        Legacy conversations continue to work unchanged — their
        messages have persona_id=NULL and the upper layers fall back
        to the existing `provider` field for identity/rendering.
        """
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS personas (
                conversation_id INTEGER NOT NULL,
                id TEXT NOT NULL,
                provider TEXT NOT NULL,
                name TEXT NOT NULL,
                name_slug TEXT NOT NULL,
                system_prompt_override TEXT,
                model_override TEXT,
                color_override TEXT,
                created_at_message_index INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                PRIMARY KEY (conversation_id, id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
            """
        )
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_personas_active_slug
                ON personas (conversation_id, name_slug)
                WHERE deleted_at IS NULL
            """
        )

        # Add persona_id column to messages — only if not already present,
        # so re-running the migration against a partially-migrated DB is
        # safe. (SQLite doesn't support `ADD COLUMN IF NOT EXISTS`.)
        msg_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(messages)")
        }
        if "persona_id" not in msg_cols:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN persona_id TEXT"
            )

    def _migration_3_send_mode(self) -> None:
        """Add the conversations.send_mode column.

        Per-conversation send mode (parallel | sequential) so the
        choice persists per chat instead of being a single global
        flag on SendController. New chats default to 'parallel'.
        """
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(conversations)")
        }
        if "send_mode" not in cols:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN send_mode "
                "TEXT NOT NULL DEFAULT 'parallel'"
            )

    def close(self) -> None:
        self._conn.close()

    # -- Conversations --

    def create_conversation(self, title: str = "New Chat", system_prompt: str = "") -> Conversation:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "INSERT INTO conversations (title, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, system_prompt, now, now),
        )
        self._conn.commit()
        return Conversation(
            id=cursor.lastrowid,
            title=title,
            system_prompt=system_prompt,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    @staticmethod
    def _decode_visibility(raw: str | None) -> dict[str, list[str]]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {
                    str(k): [str(s) for s in v]
                    for k, v in data.items()
                    if isinstance(v, list)
                }
        except (json.JSONDecodeError, TypeError):
            pass
        return {}

    def get_conversation(self, conv_id: int) -> Conversation | None:
        """Fetch a single conversation by ID."""
        row = self._conn.execute(
            "SELECT id, title, system_prompt, last_provider, "
            "limit_mark, visibility_matrix, send_mode, created_at, updated_at "
            "FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()
        if not row:
            return None
        return Conversation(
            id=row[0],
            title=row[1],
            system_prompt=row[2] or "",
            last_provider=row[3] or "",
            limit_mark=row[4],
            visibility_matrix=self._decode_visibility(row[5]),
            send_mode=row[6] or "parallel",
            created_at=datetime.fromisoformat(row[7]),
            updated_at=datetime.fromisoformat(row[8]),
        )

    def list_conversations(self) -> list[Conversation]:
        cursor = self._conn.execute(
            "SELECT id, title, system_prompt, last_provider, "
            "limit_mark, visibility_matrix, send_mode, created_at, updated_at "
            "FROM conversations ORDER BY updated_at DESC"
        )
        return [
            Conversation(
                id=row[0],
                title=row[1],
                system_prompt=row[2] or "",
                last_provider=row[3] or "",
                limit_mark=row[4],
                visibility_matrix=self._decode_visibility(row[5]),
                send_mode=row[6] or "parallel",
                created_at=datetime.fromisoformat(row[7]),
                updated_at=datetime.fromisoformat(row[8]),
            )
            for row in cursor.fetchall()
        ]

    def set_visibility_matrix(
        self, conv_id: int, matrix: dict[str, list[str]]
    ) -> None:
        """Persist the per-conversation visibility matrix."""
        self._conn.execute(
            "UPDATE conversations SET visibility_matrix = ? WHERE id = ?",
            (json.dumps(matrix), conv_id),
        )
        self._conn.commit()

    def update_conversation_title(self, conv_id: int, title: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conv_id),
        )
        self._conn.commit()

    def update_conversation_send_mode(self, conv_id: int, send_mode: str) -> None:
        """Persist the per-conversation send mode ('parallel' or 'sequential')."""
        self._conn.execute(
            "UPDATE conversations SET send_mode = ? WHERE id = ?",
            (send_mode, conv_id),
        )
        self._conn.commit()

    def update_conversation_last_provider(self, conv_id: int, provider: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET last_provider = ? WHERE id = ?",
            (provider, conv_id),
        )
        self._conn.commit()

    def add_conversation_spend(
        self, conv_id: int, provider: str, amount: float, estimated: bool = False
    ) -> None:
        # If any contribution is estimated, mark the whole row as estimated
        est = 1 if estimated else 0
        self._conn.execute(
            "INSERT INTO conversation_spend (conversation_id, provider, amount, estimated) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(conversation_id, provider) DO UPDATE SET "
            "amount = amount + ?, estimated = MAX(estimated, ?)",
            (conv_id, provider, amount, est, amount, est),
        )
        self._conn.commit()

    def get_conversation_spend(self, conv_id: int) -> dict[str, tuple[float, bool]]:
        """Return {provider: (total_spend, estimated)} for a conversation."""
        cursor = self._conn.execute(
            "SELECT provider, amount, estimated FROM conversation_spend "
            "WHERE conversation_id = ?",
            (conv_id,),
        )
        return {row[0]: (row[1], bool(row[2])) for row in cursor.fetchall()}

    def set_conversation_limit(self, conv_id: int, limit_mark: str | None) -> None:
        self._conn.execute(
            "UPDATE conversations SET limit_mark = ? WHERE id = ?",
            (limit_mark, conv_id),
        )
        self._conn.commit()

    # -- Marks --

    def set_mark(self, conv_id: int, name: str, message_count: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO marks (conversation_id, name, message_count) "
            "VALUES (?, ?, ?)",
            (conv_id, name, message_count),
        )
        self._conn.commit()

    def get_mark(self, conv_id: int, name: str) -> int | None:
        """Return the message_count stored for a mark, or None if not found."""
        row = self._conn.execute(
            "SELECT message_count FROM marks WHERE conversation_id = ? AND name = ?",
            (conv_id, name),
        ).fetchone()
        return row[0] if row else None

    def list_marks(self, conv_id: int) -> list[tuple[str, int]]:
        """Return all marks for a conversation as (name, message_count) pairs."""
        cursor = self._conn.execute(
            "SELECT name, message_count FROM marks WHERE conversation_id = ? "
            "ORDER BY message_count ASC",
            (conv_id,),
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]

    def delete_conversation(self, conv_id: int) -> None:
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self._conn.commit()

    # -- Messages --

    def add_message(self, msg: Message) -> Message:
        now = msg.created_at.isoformat()
        cursor = self._conn.execute(
            "INSERT INTO messages (conversation_id, role, provider, content, model, display_mode, pinned, pin_target, addressed_to, persona_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg.conversation_id,
                msg.role.value,
                msg.provider.value if msg.provider else None,
                msg.content,
                msg.model,
                msg.display_mode,
                1 if msg.pinned else 0,
                msg.pin_target,
                msg.addressed_to,
                msg.persona_id,
                now,
            ),
        )
        # Touch the conversation's updated_at
        self._conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, msg.conversation_id),
        )
        self._conn.commit()
        msg.id = cursor.lastrowid
        return msg

    def get_messages(self, conversation_id: int, include_hidden: bool = False) -> list[Message]:
        if include_hidden:
            where = "conversation_id = ?"
        else:
            where = "conversation_id = ? AND (hidden = 0 OR hidden IS NULL)"
        cursor = self._conn.execute(
            f"SELECT id, conversation_id, role, provider, content, model, display_mode, pinned, pin_target, addressed_to, persona_id, created_at "
            f"FROM messages WHERE {where} ORDER BY created_at ASC",
            (conversation_id,),
        )
        return [
            Message(
                id=row[0],
                conversation_id=row[1],
                role=Role(row[2]),
                provider=Provider(row[3]) if row[3] else None,
                content=row[4],
                model=row[5] if row[5] else None,
                display_mode=row[6],
                pinned=bool(row[7]),
                pin_target=row[8],
                addressed_to=row[9],
                persona_id=row[10],
                created_at=datetime.fromisoformat(row[11]),
            )
            for row in cursor.fetchall()
        ]

    def delete_messages(self, msg_ids: list[int]) -> None:
        """Delete messages by their IDs."""
        if not msg_ids:
            return
        placeholders = ",".join("?" for _ in msg_ids)
        self._conn.execute(
            f"DELETE FROM messages WHERE id IN ({placeholders})", msg_ids
        )
        self._conn.commit()

    def update_message_content(
        self,
        msg_id: int,
        content: str,
        display_mode: str | None = None,
    ) -> None:
        """Replace a message's content (and optionally its display_mode)
        in place, without touching its id or position.

        Used by //retry (#130) so a successful retry replaces the
        original error message's text in the same transcript slot
        instead of appending a new message.

        Passing ``display_mode=None`` (default) leaves the existing
        display_mode untouched; pass an explicit string (e.g. ``"cols"``)
        to update it alongside the content.
        """
        if display_mode is None:
            self._conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                (content, msg_id),
            )
        else:
            self._conn.execute(
                "UPDATE messages SET content = ?, display_mode = ? WHERE id = ?",
                (content, display_mode, msg_id),
            )
        self._conn.commit()

    def hide_messages(self, msg_ids: list[int]) -> None:
        """Mark messages as hidden."""
        if not msg_ids:
            return
        placeholders = ",".join("?" for _ in msg_ids)
        self._conn.execute(
            f"UPDATE messages SET hidden = 1 WHERE id IN ({placeholders})", msg_ids
        )
        self._conn.commit()

    def set_pinned(self, msg_id: int, pinned: bool, target: str | None) -> None:
        """Set or clear the pinned state and target of a message."""
        self._conn.execute(
            "UPDATE messages SET pinned = ?, pin_target = ? WHERE id = ?",
            (1 if pinned else 0, target if pinned else None, msg_id),
        )
        self._conn.commit()

    def unhide_all_messages(self, conv_id: int) -> None:
        """Unhide all hidden messages in a conversation."""
        self._conn.execute(
            "UPDATE messages SET hidden = 0 WHERE conversation_id = ? AND hidden = 1",
            (conv_id,),
        )
        self._conn.commit()

    # -- Personas --

    def _row_to_persona(self, row) -> Persona:
        """Build a Persona from a row tuple matching the SELECT below."""
        deleted_at = datetime.fromisoformat(row[10]) if row[10] else None
        return Persona(
            conversation_id=row[0],
            id=row[1],
            provider=Provider(row[2]),
            name=row[3],
            name_slug=row[4],
            system_prompt_override=row[5],
            model_override=row[6],
            color_override=row[7],
            created_at_message_index=row[8],
            sort_order=row[9],
            deleted_at=deleted_at,
        )

    _PERSONA_COLS = (
        "conversation_id, id, provider, name, name_slug, "
        "system_prompt_override, model_override, color_override, "
        "created_at_message_index, sort_order, deleted_at"
    )

    def create_persona(self, persona: Persona) -> Persona:
        """Insert a new persona row. Raises sqlite3.IntegrityError if
        the (conversation_id, name_slug) pair collides with an active
        persona — the partial unique index on idx_personas_active_slug
        enforces D2 uniqueness.
        """
        deleted_at_str = (
            persona.deleted_at.isoformat() if persona.deleted_at else None
        )
        self._conn.execute(
            f"INSERT INTO personas ({self._PERSONA_COLS}) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                persona.conversation_id,
                persona.id,
                persona.provider.value,
                persona.name,
                persona.name_slug,
                persona.system_prompt_override,
                persona.model_override,
                persona.color_override,
                persona.created_at_message_index,
                persona.sort_order,
                deleted_at_str,
            ),
        )
        self._conn.commit()
        return persona

    def list_personas(self, conv_id: int) -> list[Persona]:
        """Return active (non-tombstoned) personas for a conversation,
        ordered by sort_order then id for stability."""
        cursor = self._conn.execute(
            f"SELECT {self._PERSONA_COLS} FROM personas "
            f"WHERE conversation_id = ? AND deleted_at IS NULL "
            f"ORDER BY sort_order ASC, id ASC",
            (conv_id,),
        )
        return [self._row_to_persona(row) for row in cursor.fetchall()]

    def list_personas_including_deleted(self, conv_id: int) -> list[Persona]:
        """Return every persona for a conversation, including tombstoned
        ones. Used by the renderer/exporter so historical messages can
        resolve to their original persona label even after the persona
        has been removed."""
        cursor = self._conn.execute(
            f"SELECT {self._PERSONA_COLS} FROM personas "
            f"WHERE conversation_id = ? "
            f"ORDER BY sort_order ASC, id ASC",
            (conv_id,),
        )
        return [self._row_to_persona(row) for row in cursor.fetchall()]

    def update_persona(self, persona: Persona) -> None:
        """Update every mutable field on an existing persona row.
        ``deleted_at`` is preserved as-passed — use ``tombstone_persona``
        for the remove path rather than updating deleted_at manually.
        """
        deleted_at_str = (
            persona.deleted_at.isoformat() if persona.deleted_at else None
        )
        self._conn.execute(
            "UPDATE personas SET "
            "provider = ?, name = ?, name_slug = ?, "
            "system_prompt_override = ?, model_override = ?, color_override = ?, "
            "created_at_message_index = ?, sort_order = ?, deleted_at = ? "
            "WHERE conversation_id = ? AND id = ?",
            (
                persona.provider.value,
                persona.name,
                persona.name_slug,
                persona.system_prompt_override,
                persona.model_override,
                persona.color_override,
                persona.created_at_message_index,
                persona.sort_order,
                deleted_at_str,
                persona.conversation_id,
                persona.id,
            ),
        )
        self._conn.commit()

    def tombstone_persona(self, conv_id: int, persona_id: str) -> None:
        """Mark a persona as deleted without removing the row. Historical
        messages referring to this persona continue to resolve via
        ``list_personas_including_deleted`` so their labels survive.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE personas SET deleted_at = ? "
            "WHERE conversation_id = ? AND id = ?",
            (now, conv_id, persona_id),
        )
        self._conn.commit()
