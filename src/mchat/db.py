# ------------------------------------------------------------------
# Component: Database
# Responsibility: SQLite persistence for conversations and messages
# Collaborators: models.conversation, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mchat.config import DEFAULT_CONFIG_DIR
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role

DEFAULT_DB_PATH = DEFAULT_CONFIG_DIR / "mchat.db"

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
        """Add columns that may be missing from older databases."""
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

    def list_conversations(self) -> list[Conversation]:
        cursor = self._conn.execute(
            "SELECT id, title, system_prompt, last_provider, "
            "spend_claude, spend_openai, limit_mark, created_at, updated_at "
            "FROM conversations ORDER BY updated_at DESC"
        )
        return [
            Conversation(
                id=row[0],
                title=row[1],
                system_prompt=row[2] or "",
                last_provider=row[3] or "",
                spend_claude=row[4] or 0.0,
                spend_openai=row[5] or 0.0,
                limit_mark=row[6],
                created_at=datetime.fromisoformat(row[7]),
                updated_at=datetime.fromisoformat(row[8]),
            )
            for row in cursor.fetchall()
        ]

    def update_conversation_title(self, conv_id: int, title: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conv_id),
        )
        self._conn.commit()

    def update_conversation_last_provider(self, conv_id: int, provider: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET last_provider = ? WHERE id = ?",
            (provider, conv_id),
        )
        self._conn.commit()

    def add_conversation_spend(self, conv_id: int, provider: str, amount: float) -> None:
        self._conn.execute(
            "INSERT INTO conversation_spend (conversation_id, provider, amount) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(conversation_id, provider) DO UPDATE SET amount = amount + ?",
            (conv_id, provider, amount, amount),
        )
        self._conn.commit()

    def get_conversation_spend(self, conv_id: int) -> dict[str, float]:
        """Return {provider: total_spend} for a conversation."""
        cursor = self._conn.execute(
            "SELECT provider, amount FROM conversation_spend WHERE conversation_id = ?",
            (conv_id,),
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

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
            "INSERT INTO messages (conversation_id, role, provider, content, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                msg.conversation_id,
                msg.role.value,
                msg.provider.value if msg.provider else None,
                msg.content,
                msg.model,
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

    def get_messages(self, conversation_id: int) -> list[Message]:
        cursor = self._conn.execute(
            "SELECT id, conversation_id, role, provider, content, model, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
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
                created_at=datetime.fromisoformat(row[6]),
            )
            for row in cursor.fetchall()
        ]
