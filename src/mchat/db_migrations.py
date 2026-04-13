# ------------------------------------------------------------------
# Component: db_migrations
# Responsibility: Schema constants and migration functions extracted
#                 from Database (#161). Each migration is a standalone
#                 function taking a sqlite3.Connection. The MIGRATIONS
#                 list and run_migrations() dispatcher are the public
#                 interface.
# Collaborators: sqlite3
# ------------------------------------------------------------------
from __future__ import annotations

import sqlite3

# Schema version stored in PRAGMA user_version. Bump this and append a
# new migration function whenever the schema changes.
CURRENT_SCHEMA_VERSION = 5

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


# ------------------------------------------------------------------
# Migration functions — each takes a sqlite3.Connection
# ------------------------------------------------------------------

def _migration_1_initial(conn: sqlite3.Connection) -> None:
    """Initial catch-all migration. Covers every schema change made
    before explicit versioning was introduced."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "system_prompt" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''")
    if "last_provider" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN last_provider TEXT NOT NULL DEFAULT ''")
    if "spend_claude" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN spend_claude REAL NOT NULL DEFAULT 0.0")
    if "spend_openai" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN spend_openai REAL NOT NULL DEFAULT 0.0")
    if "limit_mark" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN limit_mark TEXT")
    if "visibility_matrix" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN visibility_matrix TEXT NOT NULL DEFAULT '{}'")

    if "spend_claude" in cols:
        conn.execute(
            "INSERT OR IGNORE INTO conversation_spend (conversation_id, provider, amount) "
            "SELECT id, 'claude', spend_claude FROM conversations WHERE spend_claude > 0"
        )
        conn.execute(
            "INSERT OR IGNORE INTO conversation_spend (conversation_id, provider, amount) "
            "SELECT id, 'openai', spend_openai FROM conversations WHERE spend_openai > 0"
        )

    spend_cols = {row[1] for row in conn.execute("PRAGMA table_info(conversation_spend)")}
    if spend_cols and "estimated" not in spend_cols:
        conn.execute("ALTER TABLE conversation_spend ADD COLUMN estimated INTEGER NOT NULL DEFAULT 0")

    msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "hidden" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    if "display_mode" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN display_mode TEXT")
    if "pinned" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    if "pin_target" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN pin_target TEXT")
    if "addressed_to" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN addressed_to TEXT")

    for display_name in ("Claude", "GPT", "Gemini", "Perplexity"):
        prefix = f"**{display_name}'s take:**\n\n"
        conn.execute(
            "UPDATE messages SET content = SUBSTR(content, ?) "
            "WHERE role = 'assistant' AND content LIKE ?",
            (len(prefix) + 1, f"**{display_name}'s take:**%"),
        )


def _migration_2_personas(conn: sqlite3.Connection) -> None:
    """Add the personas table and the messages.persona_id column."""
    conn.execute(
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
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_personas_active_slug
            ON personas (conversation_id, name_slug)
            WHERE deleted_at IS NULL
        """
    )
    msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "persona_id" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN persona_id TEXT")


def _migration_3_send_mode(conn: sqlite3.Connection) -> None:
    """Add the conversations.send_mode column."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "send_mode" not in cols:
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN send_mode "
            "TEXT NOT NULL DEFAULT 'parallel'"
        )


def _migration_4_rewrite_prefixes(conn: sqlite3.Connection) -> None:
    """Rewrite historical message prefixes from '<word>,<ws>' grammar
    to '@<word> <ws>' grammar (#140)."""
    import re

    provider_shorthands = {
        "claude", "gpt", "gemini", "perplexity", "pplx", "mistral",
    }
    special_keywords = {"all", "flipped", "both"}
    keyword_rename = {"flipped": "others", "both": "all"}

    token_pattern = re.compile(
        r"^\s*([A-Za-z][A-Za-z0-9_\-]*)\s*[,:]\s*",
    )

    rows = conn.execute(
        "SELECT id, conversation_id, content FROM messages "
        "WHERE role = 'user'"
    ).fetchall()

    slugs_by_conv: dict[int, set[str]] = {}

    def _slugs_for(conv_id: int) -> set[str]:
        if conv_id not in slugs_by_conv:
            persona_rows = conn.execute(
                "SELECT name_slug FROM personas WHERE conversation_id = ?",
                (conv_id,),
            ).fetchall()
            slugs_by_conv[conv_id] = {r[0] for r in persona_rows}
        return slugs_by_conv[conv_id]

    rewrites: list[tuple[int, str]] = []
    for msg_id, conv_id, content in rows:
        if not content:
            continue
        remaining = content
        consumed_words: list[str] = []
        all_known = True
        while True:
            m = token_pattern.match(remaining)
            if not m:
                break
            word = m.group(1).lower()
            if (
                word in provider_shorthands
                or word in special_keywords
                or word in _slugs_for(conv_id)
            ):
                consumed_words.append(word)
                remaining = remaining[m.end():]
                continue
            all_known = False
            break

        if not all_known or not consumed_words:
            continue

        at_tokens = [
            "@" + keyword_rename.get(w, w) for w in consumed_words
        ]
        new_content = " ".join(at_tokens) + " " + remaining.lstrip()
        new_content = new_content.rstrip()
        rewrites.append((msg_id, new_content))

    for msg_id, new_content in rewrites:
        conn.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            (new_content, msg_id),
        )


def _migration_5_rerun_rewrite_for_stragglers(conn: sqlite3.Connection) -> None:
    """Re-run the prefix rewrite to catch 'both' alias missed in v4."""
    _migration_4_rewrite_prefixes(conn)


# Ordered list of (version, migration_function) pairs.
MIGRATIONS: list[tuple[int, callable]] = [
    (1, _migration_1_initial),
    (2, _migration_2_personas),
    (3, _migration_3_send_mode),
    (4, _migration_4_rewrite_prefixes),
    (5, _migration_5_rerun_rewrite_for_stragglers),
]


def run_migrations(conn: sqlite3.Connection) -> None:
    """Run every schema migration whose version number is newer than
    the DB's current user_version, then stamp the new version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, fn in MIGRATIONS:
        if current < version:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {version}")
