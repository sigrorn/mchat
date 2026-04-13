# ------------------------------------------------------------------
# Component: test_db
# Responsibility: Tests for database persistence
# Collaborators: db, models
# ------------------------------------------------------------------
from __future__ import annotations

from pathlib import Path

import pytest

from mchat.db import Database
from mchat.models.message import Message, Provider, Role


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test.db")
    yield database
    database.close()


class TestDatabase:
    def test_create_and_list_conversations(self, db):
        conv = db.create_conversation("Test Chat")
        convs = db.list_conversations()
        assert len(convs) == 1
        assert convs[0].title == "Test Chat"
        assert convs[0].id == conv.id

    def test_get_conversation(self, db):
        conv = db.create_conversation("Test Chat")
        fetched = db.get_conversation(conv.id)
        assert fetched is not None
        assert fetched.title == "Test Chat"
        assert fetched.id == conv.id

    def test_get_conversation_missing(self, db):
        assert db.get_conversation(9999) is None

    def test_delete_conversation(self, db):
        conv = db.create_conversation("To Delete")
        db.delete_conversation(conv.id)
        assert len(db.list_conversations()) == 0

    def test_add_and_get_messages(self, db):
        conv = db.create_conversation()
        msg = Message(
            role=Role.USER,
            content="Hello",
            conversation_id=conv.id,
        )
        saved = db.add_message(msg)
        assert saved.id is not None

        messages = db.get_messages(conv.id)
        assert len(messages) == 1
        assert messages[0].content == "Hello"
        assert messages[0].role == Role.USER

    def test_message_with_provider(self, db):
        conv = db.create_conversation()
        msg = Message(
            role=Role.ASSISTANT,
            content="Hi there",
            provider=Provider.CLAUDE,
            model="claude-sonnet-4-20250514",
            conversation_id=conv.id,
        )
        db.add_message(msg)

        messages = db.get_messages(conv.id)
        assert messages[0].provider == Provider.CLAUDE
        assert messages[0].model == "claude-sonnet-4-20250514"

    def test_update_conversation_title(self, db):
        conv = db.create_conversation("Old Title")
        db.update_conversation_title(conv.id, "New Title")
        convs = db.list_conversations()
        assert convs[0].title == "New Title"

    def test_update_message_content_rewrites_text_and_mode(self, db):
        """#130 — update_message_content replaces content (and optional
        display_mode) in place without changing message id or position."""
        conv = db.create_conversation()
        m1 = Message(
            role=Role.ASSISTANT, content="[Error from claude: overloaded]",
            provider=Provider.CLAUDE, persona_id="p_a",
            conversation_id=conv.id, display_mode=None,
        )
        m2 = Message(
            role=Role.ASSISTANT, content="original text",
            provider=Provider.OPENAI, persona_id="p_b",
            conversation_id=conv.id, display_mode="cols",
        )
        db.add_message(m1)
        db.add_message(m2)
        m1_id = db.get_messages(conv.id)[0].id

        db.update_message_content(
            m1_id, "actual response text", display_mode="cols",
        )
        msgs = db.get_messages(conv.id)
        assert msgs[0].id == m1_id
        assert msgs[0].content == "actual response text"
        assert msgs[0].display_mode == "cols"
        # Second message unchanged
        assert msgs[1].content == "original text"

    def test_update_message_content_without_mode(self, db):
        """display_mode left alone when update_message_content is called
        with display_mode=None (default)."""
        conv = db.create_conversation()
        m = Message(
            role=Role.ASSISTANT, content="old", provider=Provider.CLAUDE,
            conversation_id=conv.id, display_mode="lines",
        )
        db.add_message(m)
        mid = db.get_messages(conv.id)[0].id
        db.update_message_content(mid, "new")
        msgs = db.get_messages(conv.id)
        assert msgs[0].content == "new"
        assert msgs[0].display_mode == "lines"

    def test_cascade_delete(self, db):
        conv = db.create_conversation()
        db.add_message(Message(role=Role.USER, content="test", conversation_id=conv.id))
        db.delete_conversation(conv.id)
        assert db.get_messages(conv.id) == []


class TestConversationSpend:
    def test_add_and_get_spend(self, db):
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "claude", 0.005)
        db.add_conversation_spend(conv.id, "claude", 0.003)
        db.add_conversation_spend(conv.id, "openai", 0.010)

        spend = db.get_conversation_spend(conv.id)
        assert abs(spend["claude"][0] - 0.008) < 1e-9
        assert spend["claude"][1] is False
        assert abs(spend["openai"][0] - 0.010) < 1e-9

    def test_spend_defaults_to_zero(self, db):
        conv = db.create_conversation()
        spend = db.get_conversation_spend(conv.id)
        assert spend == {}

    def test_spend_for_new_providers(self, db):
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "gemini", 0.002, estimated=True)
        db.add_conversation_spend(conv.id, "perplexity", 0.001)
        spend = db.get_conversation_spend(conv.id)
        assert abs(spend["gemini"][0] - 0.002) < 1e-9
        assert spend["gemini"][1] is True
        assert abs(spend["perplexity"][0] - 0.001) < 1e-9
        assert spend["perplexity"][1] is False

    def test_estimated_flag_sticky(self, db):
        """Once any contribution is estimated, the flag stays True."""
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "gemini", 0.001, estimated=False)
        db.add_conversation_spend(conv.id, "gemini", 0.002, estimated=True)
        spend = db.get_conversation_spend(conv.id)
        assert spend["gemini"][1] is True

    def test_per_persona_spend(self, db):
        """#95 — spend should be trackable per persona_id, not just
        per provider. Two Claude personas accumulate separately."""
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "p_partner", 0.005)
        db.add_conversation_spend(conv.id, "p_evaluator", 0.003)
        db.add_conversation_spend(conv.id, "p_partner", 0.002)
        spend = db.get_conversation_spend(conv.id)
        assert abs(spend["p_partner"][0] - 0.007) < 1e-9
        assert abs(spend["p_evaluator"][0] - 0.003) < 1e-9

    def test_spend_deleted_with_conversation(self, db):
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "claude", 0.005)
        db.delete_conversation(conv.id)
        spend = db.get_conversation_spend(conv.id)
        assert spend == {}


class TestDeleteMessages:
    def test_delete_messages_by_ids(self, db):
        conv = db.create_conversation()
        m1 = db.add_message(Message(role=Role.USER, content="q1", conversation_id=conv.id))
        m2 = db.add_message(Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE, conversation_id=conv.id))
        m3 = db.add_message(Message(role=Role.USER, content="q2", conversation_id=conv.id))

        db.delete_messages([m2.id, m3.id])
        remaining = db.get_messages(conv.id)
        assert len(remaining) == 1
        assert remaining[0].content == "q1"

    def test_delete_empty_list(self, db):
        conv = db.create_conversation()
        db.add_message(Message(role=Role.USER, content="q1", conversation_id=conv.id))
        db.delete_messages([])
        assert len(db.get_messages(conv.id)) == 1


class TestHideUnhide:
    def test_hide_messages(self, db):
        conv = db.create_conversation()
        m1 = db.add_message(Message(role=Role.USER, content="q1", conversation_id=conv.id))
        m2 = db.add_message(Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE, conversation_id=conv.id))
        m3 = db.add_message(Message(role=Role.USER, content="q2", conversation_id=conv.id))

        db.hide_messages([m1.id, m2.id])
        visible = db.get_messages(conv.id)
        assert len(visible) == 1
        assert visible[0].content == "q2"

        all_msgs = db.get_messages(conv.id, include_hidden=True)
        assert len(all_msgs) == 3

    def test_unhide_all(self, db):
        conv = db.create_conversation()
        m1 = db.add_message(Message(role=Role.USER, content="q1", conversation_id=conv.id))
        m2 = db.add_message(Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE, conversation_id=conv.id))
        db.hide_messages([m1.id, m2.id])

        db.unhide_all_messages(conv.id)
        visible = db.get_messages(conv.id)
        assert len(visible) == 2


class TestPinning:
    def test_add_pinned_message(self, db):
        conv = db.create_conversation()
        msg = Message(
            role=Role.USER,
            content="always reply in bullet points",
            conversation_id=conv.id,
            pinned=True,
            pin_target="claude",
        )
        db.add_message(msg)
        messages = db.get_messages(conv.id)
        assert len(messages) == 1
        assert messages[0].pinned is True
        assert messages[0].pin_target == "claude"

    def test_default_message_is_not_pinned(self, db):
        conv = db.create_conversation()
        db.add_message(Message(role=Role.USER, content="hi", conversation_id=conv.id))
        messages = db.get_messages(conv.id)
        assert messages[0].pinned is False
        assert messages[0].pin_target is None

    def test_set_pinned_unpins(self, db):
        conv = db.create_conversation()
        m = db.add_message(Message(
            role=Role.USER,
            content="be concise",
            conversation_id=conv.id,
            pinned=True,
            pin_target="all",
        ))
        db.set_pinned(m.id, False, None)
        messages = db.get_messages(conv.id)
        assert messages[0].pinned is False
        assert messages[0].pin_target is None

    def test_set_pinned_updates_target(self, db):
        conv = db.create_conversation()
        m = db.add_message(Message(
            role=Role.USER,
            content="rule",
            conversation_id=conv.id,
            pinned=True,
            pin_target="claude",
        ))
        db.set_pinned(m.id, True, "claude,openai")
        messages = db.get_messages(conv.id)
        assert messages[0].pinned is True
        assert messages[0].pin_target == "claude,openai"


class TestSchemaVersioning:
    def test_new_db_has_current_version(self, db):
        from mchat.db import CURRENT_SCHEMA_VERSION
        version = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_SCHEMA_VERSION

    def test_reopen_does_not_downgrade(self, tmp_path):
        from mchat.db import Database, CURRENT_SCHEMA_VERSION
        db1 = Database(db_path=tmp_path / "v.db")
        db1.close()
        db2 = Database(db_path=tmp_path / "v.db")
        try:
            version = db2._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == CURRENT_SCHEMA_VERSION
        finally:
            db2.close()

    def test_legacy_db_upgraded(self, tmp_path):
        """A DB created before versioning (user_version=0) but with all
        current columns present must still end up at the current version
        after the migration runs."""
        import sqlite3
        from mchat.db import Database, CURRENT_SCHEMA_VERSION

        # Create a DB at version 0, then let Database upgrade it.
        path = tmp_path / "legacy.db"
        raw = sqlite3.connect(str(path))
        raw.execute("PRAGMA user_version = 0")
        raw.commit()
        raw.close()

        db = Database(db_path=path)
        try:
            version = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == CURRENT_SCHEMA_VERSION
            # Smoke test: can still add and read a message
            conv = db.create_conversation("legacy")
            db.add_message(Message(role=Role.USER, content="hi", conversation_id=conv.id))
            assert len(db.get_messages(conv.id)) == 1
        finally:
            db.close()


class TestMigration4RewritePrefixes:
    """#140 — migration 4 rewrites historical message content from the
    old '<word>,<ws>' grammar to the new '@<word> <ws>' grammar, so
    context-builder's strip path keeps working cleanly. Persona rows
    are NOT touched (grandfathering decision)."""

    def _seed_v3_db_with_content(
        self, tmp_path, conv_content_pairs, persona_rows=None,
    ):
        """Create a raw SQLite DB at schema version 3, seed one
        conversation per entry with the given message contents and
        (optionally) persona rows, then return the path for
        upgrading via Database().

        ``conv_content_pairs`` is a list of (conv_id_hint, [message
        contents]) tuples. ``persona_rows`` is a list of
        (conv_id_hint, [(name, slug, provider_value), ...]) tuples
        — the migration should NOT rewrite these.
        """
        import sqlite3
        from datetime import datetime, timezone

        path = tmp_path / "v3.db"
        raw = sqlite3.connect(str(path))
        # Recreate the schema up to and including V3.
        # Rather than hand-copying the full schema, let a regular
        # Database() instance build it and then manually roll back
        # to V3 by setting the pragma.
        raw.close()
        from mchat.db import Database
        temp_db = Database(db_path=path)
        temp_db.close()

        # Reopen raw and roll back to V3.
        raw = sqlite3.connect(str(path))
        raw.execute("PRAGMA user_version = 3")

        now = datetime.now(timezone.utc).isoformat()
        conv_ids: dict = {}
        for conv_hint, contents in conv_content_pairs:
            cur = raw.execute(
                "INSERT INTO conversations (title, system_prompt, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (f"conv-{conv_hint}", "", now, now),
            )
            conv_ids[conv_hint] = cur.lastrowid
            for content in contents:
                raw.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (conv_ids[conv_hint], "user", content, now),
                )

        if persona_rows:
            for conv_hint, rows in persona_rows:
                cid = conv_ids[conv_hint]
                for name, slug, provider_value in rows:
                    raw.execute(
                        "INSERT INTO personas (conversation_id, id, provider, "
                        "name, name_slug, sort_order) VALUES (?, ?, ?, ?, ?, 0)",
                        (
                            cid,
                            f"p_test_{slug}",
                            provider_value,
                            name,
                            slug,
                        ),
                    )

        raw.commit()
        raw.close()
        return path, conv_ids

    def _get_contents(self, db_path, conv_id):
        import sqlite3
        raw = sqlite3.connect(str(db_path))
        rows = raw.execute(
            "SELECT content FROM messages WHERE conversation_id = ? "
            "ORDER BY id",
            (conv_id,),
        ).fetchall()
        raw.close()
        return [r[0] for r in rows]

    def test_migration_rewrites_provider_prefix(self, tmp_path):
        path, convs = self._seed_v3_db_with_content(
            tmp_path, [("a", ["claude, hello"])],
        )
        # Trigger migration 4 by opening via Database()
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["@claude hello"]
        finally:
            db.close()

    def test_migration_rewrites_multi_token_prefix(self, tmp_path):
        path, convs = self._seed_v3_db_with_content(
            tmp_path, [("a", ["claude, gpt, compare these"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["@claude @gpt compare these"]
        finally:
            db.close()

    def test_migration_rewrites_flipped_to_others(self, tmp_path):
        path, convs = self._seed_v3_db_with_content(
            tmp_path, [("a", ["flipped, continue"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["@others continue"]
        finally:
            db.close()

    def test_migration_rewrites_both_to_all(self, tmp_path):
        """'both, ...' was an old alias for 'all, ...' before it was
        removed. Historical messages with 'both,' must rewrite to
        '@all ...' — the same semantics the user originally typed."""
        path, convs = self._seed_v3_db_with_content(
            tmp_path, [("a", ["both, what do you think?"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["@all what do you think?"]
        finally:
            db.close()

    def test_migration_rewrites_all_prefix(self, tmp_path):
        path, convs = self._seed_v3_db_with_content(
            tmp_path, [("a", ["all, hello everyone"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["@all hello everyone"]
        finally:
            db.close()

    def test_migration_rewrites_persona_name_prefix(self, tmp_path):
        """Conversation has an explicit persona 'partner'. A message
        like 'partner, ciao' should rewrite to '@partner ciao'."""
        path, convs = self._seed_v3_db_with_content(
            tmp_path,
            [("a", ["partner, ciao"])],
            persona_rows=[("a", [("Partner", "partner", "claude")])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["@partner ciao"]
        finally:
            db.close()

    def test_migration_leaves_natural_english_unchanged(self, tmp_path):
        """'ok, let's continue' does NOT start with a recognised token.
        The migration must leave it alone — exactly the false-positive
        immunity the new grammar buys us."""
        path, convs = self._seed_v3_db_with_content(
            tmp_path, [("a", ["ok, let's continue"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["ok, let's continue"]
        finally:
            db.close()

    def test_migration_is_idempotent(self, tmp_path):
        """Running migration 4 twice yields the same result — the
        second run finds no '<word>,<ws>' prefixes to rewrite because
        the messages already start with '@'."""
        path, convs = self._seed_v3_db_with_content(
            tmp_path, [("a", ["claude, hello"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            after_first = self._get_contents(path, convs["a"])
        finally:
            db.close()
        # Simulate a second run: bump the schema back and reopen.
        import sqlite3
        raw = sqlite3.connect(str(path))
        raw.execute("PRAGMA user_version = 3")
        raw.commit()
        raw.close()
        db = Database(db_path=path)
        try:
            after_second = self._get_contents(path, convs["a"])
            assert after_first == after_second == ["@claude hello"]
        finally:
            db.close()

    def test_migration_does_not_touch_persona_rows(self, tmp_path):
        """Grandfathering: persona rows with whitespace-containing
        names, reserved names, etc. are NOT rewritten. Only message
        content is touched."""
        path, convs = self._seed_v3_db_with_content(
            tmp_path,
            [("a", ["partner, hi"])],
            persona_rows=[
                ("a", [
                    ("Partner", "partner", "claude"),
                    ("Claude Bot", "claude_bot", "openai"),
                    ("claude", "claude", "openai"),
                ]),
            ],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            personas = db.list_personas(convs["a"])
            names = sorted(p.name for p in personas)
            assert names == sorted(["Partner", "Claude Bot", "claude"])
            # And their slugs are unchanged
            slugs = {p.name for p in personas}
            assert "Claude Bot" in slugs  # still has whitespace
            assert "claude" in slugs  # still a reserved name
        finally:
            db.close()

    def test_migration_leaves_unknown_prefix_alone(self, tmp_path):
        """A 'word, text' where 'word' is not a recognised token
        (not a provider shorthand, not all/flipped, not an active
        persona slug in this conv) must be left alone."""
        path, convs = self._seed_v3_db_with_content(
            tmp_path, [("a", ["nobody, hi there"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["nobody, hi there"]
        finally:
            db.close()


class TestMigration5RerunForStragglers:
    """#140 follow-up — users who ran the initial V4 migration (which
    didn't recognise 'both,' as a legacy alias) have DBs stuck at
    user_version=4 with 'both, ...' messages still in the old grammar.
    Migration 5 re-runs the rewrite pass to catch those stragglers.
    Because _migration_4_rewrite_prefixes is idempotent, this is safe
    on DBs that don't have any stragglers.
    """

    def _seed_v4_db_with_content(self, tmp_path, conv_contents):
        """Build a DB, let all migrations run, force user_version back
        to 4, and insert messages as if the user had run the first
        version of migration 4 that didn't handle 'both,'."""
        import sqlite3
        from datetime import datetime, timezone

        path = tmp_path / "v4.db"
        from mchat.db import Database
        temp_db = Database(db_path=path)
        temp_db.close()

        raw = sqlite3.connect(str(path))
        raw.execute("PRAGMA user_version = 4")
        now = datetime.now(timezone.utc).isoformat()
        conv_ids: dict = {}
        for conv_hint, contents in conv_contents:
            cur = raw.execute(
                "INSERT INTO conversations (title, system_prompt, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (f"conv-{conv_hint}", "", now, now),
            )
            conv_ids[conv_hint] = cur.lastrowid
            for content in contents:
                raw.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (conv_ids[conv_hint], "user", content, now),
                )
        raw.commit()
        raw.close()
        return path, conv_ids

    def _get_contents(self, db_path, conv_id):
        import sqlite3
        raw = sqlite3.connect(str(db_path))
        rows = raw.execute(
            "SELECT content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conv_id,),
        ).fetchall()
        raw.close()
        return [r[0] for r in rows]

    def test_migration_5_catches_both_straggler(self, tmp_path):
        """A V4 DB with 'both, explain this' gets its content
        rewritten to '@all explain this' when upgraded to V5."""
        path, convs = self._seed_v4_db_with_content(
            tmp_path, [("a", ["both, explain this"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["@all explain this"]
            # And user_version should now be 5
            version = db._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version >= 5
        finally:
            db.close()

    def test_migration_5_leaves_already_migrated_rows_alone(self, tmp_path):
        """A V4 DB with '@claude hello' (already migrated) stays
        unchanged — the rewrite is idempotent."""
        path, convs = self._seed_v4_db_with_content(
            tmp_path, [("a", ["@claude hello", "@all compare"])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == ["@claude hello", "@all compare"]
        finally:
            db.close()

    def test_migration_5_mixed_content(self, tmp_path):
        """A realistic V4 DB has some already-migrated rows plus
        some 'both,' stragglers. Only the stragglers change."""
        path, convs = self._seed_v4_db_with_content(
            tmp_path,
            [("a", [
                "@claude hello",          # already migrated — untouched
                "both, what do you think",  # straggler — rewritten
                "plain text",              # never had a prefix — untouched
                "@gpt @gemini compare",   # already migrated — untouched
            ])],
        )
        from mchat.db import Database
        db = Database(db_path=path)
        try:
            contents = self._get_contents(path, convs["a"])
            assert contents == [
                "@claude hello",
                "@all what do you think",
                "plain text",
                "@gpt @gemini compare",
            ]
        finally:
            db.close()


class TestVisibility:
    def test_addressed_to_roundtrip(self, db):
        conv = db.create_conversation()
        db.add_message(Message(
            role=Role.USER,
            content="hi claude",
            conversation_id=conv.id,
            addressed_to="claude",
        ))
        db.add_message(Message(
            role=Role.USER,
            content="hi all",
            conversation_id=conv.id,
            addressed_to="all",
        ))
        db.add_message(Message(
            role=Role.USER,
            content="legacy",
            conversation_id=conv.id,
        ))
        messages = db.get_messages(conv.id)
        assert messages[0].addressed_to == "claude"
        assert messages[1].addressed_to == "all"
        assert messages[2].addressed_to is None

    def test_visibility_matrix_default_empty(self, db):
        conv = db.create_conversation()
        fetched = db.get_conversation(conv.id)
        assert fetched.visibility_matrix == {}

    def test_set_and_get_visibility_matrix(self, db):
        conv = db.create_conversation()
        matrix = {"openai": ["claude"], "gemini": []}
        db.set_visibility_matrix(conv.id, matrix)
        fetched = db.get_conversation(conv.id)
        assert fetched.visibility_matrix == matrix

    def test_visibility_matrix_in_list_conversations(self, db):
        conv = db.create_conversation()
        db.set_visibility_matrix(conv.id, {"claude": ["openai", "gemini"]})
        convs = db.list_conversations()
        assert convs[0].visibility_matrix == {"claude": ["openai", "gemini"]}


class TestPersonas:
    """Stage 1.2 — persona CRUD and the messages.persona_id column.

    See docs/plans/personas.md § Stage 1.2.
    """

    def _make_persona(self, conv_id, **overrides):
        """Helper: build a Persona with sensible defaults for tests."""
        from mchat.models.persona import Persona, generate_persona_id
        fields = dict(
            conversation_id=conv_id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Evaluator",
            name_slug="evaluator",
        )
        fields.update(overrides)
        return Persona(**fields)

    def test_create_and_list_personas_round_trip(self, db):
        conv = db.create_conversation()
        p = self._make_persona(conv.id, name="Partner", name_slug="partner")
        created = db.create_persona(p)
        assert created.id == p.id

        listed = db.list_personas(conv.id)
        assert len(listed) == 1
        got = listed[0]
        assert got.id == p.id
        assert got.name == "Partner"
        assert got.name_slug == "partner"
        assert got.provider == Provider.CLAUDE
        assert got.system_prompt_override is None
        assert got.model_override is None
        assert got.color_override is None
        assert got.created_at_message_index is None
        assert got.deleted_at is None

    def test_create_persona_with_all_fields(self, db):
        from datetime import datetime, timezone
        conv = db.create_conversation()
        p = self._make_persona(
            conv.id,
            name="Critic",
            name_slug="critic",
            system_prompt_override="Be critical",
            model_override="claude-opus-4",
            color_override="#ff00ff",
            created_at_message_index=5,
            sort_order=2,
        )
        db.create_persona(p)
        listed = db.list_personas(conv.id)
        assert listed[0].system_prompt_override == "Be critical"
        assert listed[0].model_override == "claude-opus-4"
        assert listed[0].color_override == "#ff00ff"
        assert listed[0].created_at_message_index == 5
        assert listed[0].sort_order == 2

    def test_list_personas_returns_sorted(self, db):
        conv = db.create_conversation()
        db.create_persona(self._make_persona(
            conv.id, name="B", name_slug="b", sort_order=2,
        ))
        db.create_persona(self._make_persona(
            conv.id, name="A", name_slug="a", sort_order=1,
        ))
        db.create_persona(self._make_persona(
            conv.id, name="C", name_slug="c", sort_order=3,
        ))
        names = [p.name for p in db.list_personas(conv.id)]
        assert names == ["A", "B", "C"]

    def test_update_persona(self, db):
        conv = db.create_conversation()
        p = db.create_persona(self._make_persona(conv.id))
        p.system_prompt_override = "Updated"
        p.model_override = "claude-haiku-4-5"
        db.update_persona(p)

        listed = db.list_personas(conv.id)
        assert listed[0].system_prompt_override == "Updated"
        assert listed[0].model_override == "claude-haiku-4-5"

    def test_tombstone_does_not_hard_delete(self, db):
        """D3: tombstoned rows remain in the table but are excluded
        from list_personas. list_personas_including_deleted still
        sees them so historical messages can resolve their labels."""
        conv = db.create_conversation()
        p = db.create_persona(self._make_persona(conv.id))

        db.tombstone_persona(conv.id, p.id)

        # Active list omits the tombstoned row
        assert db.list_personas(conv.id) == []

        # Including-deleted list still has it, with deleted_at set
        all_personas = db.list_personas_including_deleted(conv.id)
        assert len(all_personas) == 1
        assert all_personas[0].id == p.id
        assert all_personas[0].deleted_at is not None

    def test_partial_unique_index_blocks_active_slug_collision(self, db):
        """D2: two active personas in the same chat cannot share a slug."""
        import sqlite3
        conv = db.create_conversation()
        db.create_persona(self._make_persona(
            conv.id, name="Eval", name_slug="evaluator",
        ))
        with pytest.raises(sqlite3.IntegrityError):
            db.create_persona(self._make_persona(
                conv.id, name="Evaluator 2", name_slug="evaluator",
            ))

    def test_tombstoned_slug_can_be_reused(self, db):
        """Partial unique index excludes tombstoned rows — the same
        slug can be reused after the original is removed."""
        conv = db.create_conversation()
        first = db.create_persona(self._make_persona(
            conv.id, name="Eval", name_slug="evaluator",
        ))
        db.tombstone_persona(conv.id, first.id)

        # Now creating a new persona with the same slug should succeed
        second = db.create_persona(self._make_persona(
            conv.id, name="Eval v2", name_slug="evaluator",
        ))
        assert second.id != first.id
        active = db.list_personas(conv.id)
        assert len(active) == 1
        assert active[0].id == second.id

    def test_next_persona_sort_order_empty(self, db):
        """#158 — next_persona_sort_order returns 0 for a chat with no personas."""
        conv = db.create_conversation()
        assert db.next_persona_sort_order(conv.id) == 0

    def test_next_persona_sort_order_increments(self, db):
        """#158 — next_persona_sort_order returns max + 1."""
        conv = db.create_conversation()
        db.create_persona(self._make_persona(
            conv.id, name="A", name_slug="a", sort_order=0,
        ))
        assert db.next_persona_sort_order(conv.id) == 1
        db.create_persona(self._make_persona(
            conv.id, name="B", name_slug="b", sort_order=1,
        ))
        assert db.next_persona_sort_order(conv.id) == 2

    def test_next_persona_sort_order_ignores_tombstoned(self, db):
        """#158 — tombstoned personas don't affect next sort_order."""
        conv = db.create_conversation()
        p = db.create_persona(self._make_persona(
            conv.id, name="A", name_slug="a", sort_order=5,
        ))
        db.tombstone_persona(conv.id, p.id)
        # After tombstoning, next should still count from the max
        # of all rows (including tombstoned) to avoid collisions
        assert db.next_persona_sort_order(conv.id) == 6

    def test_slug_collision_across_conversations_is_allowed(self, db):
        """The unique index is scoped to conversation_id, so two chats
        can each have a persona named 'partner'."""
        conv_a = db.create_conversation("A")
        conv_b = db.create_conversation("B")
        db.create_persona(self._make_persona(
            conv_a.id, name="Partner", name_slug="partner",
        ))
        db.create_persona(self._make_persona(
            conv_b.id, name="Partner", name_slug="partner",
        ))
        assert len(db.list_personas(conv_a.id)) == 1
        assert len(db.list_personas(conv_b.id)) == 1

    def test_cascade_delete_with_conversation(self, db):
        conv = db.create_conversation()
        db.create_persona(self._make_persona(conv.id))
        db.delete_conversation(conv.id)
        # The persona row should be gone with the conversation
        assert db.list_personas_including_deleted(conv.id) == []

    def test_message_persona_id_round_trip(self, db):
        conv = db.create_conversation()
        p = db.create_persona(self._make_persona(conv.id))

        db.add_message(Message(
            role=Role.ASSISTANT,
            content="hi",
            provider=Provider.CLAUDE,
            conversation_id=conv.id,
            persona_id=p.id,
        ))
        msgs = db.get_messages(conv.id)
        assert msgs[0].persona_id == p.id

    def test_legacy_messages_load_with_persona_id_none(self, db):
        """Messages inserted without a persona_id (pre-migration rows,
        or messages added before the persona layer exists in code)
        must load with persona_id=None."""
        conv = db.create_conversation()
        db.add_message(Message(
            role=Role.USER, content="hi", conversation_id=conv.id,
        ))
        msgs = db.get_messages(conv.id)
        assert msgs[0].persona_id is None

    def test_migration_2_is_idempotent(self, tmp_path):
        """Re-opening a migrated DB must not run migration 2 a second
        time (would crash on CREATE TABLE without IF NOT EXISTS or on
        ALTER TABLE ADD COLUMN twice)."""
        from mchat.db import Database, CURRENT_SCHEMA_VERSION
        path = tmp_path / "mig.db"
        db1 = Database(db_path=path)
        # Create a persona so we have data to preserve
        conv = db1.create_conversation()
        p = db1.create_persona(self._make_persona(conv.id))
        db1.close()

        # Reopen and verify everything still works
        db2 = Database(db_path=path)
        try:
            version = db2._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == CURRENT_SCHEMA_VERSION
            listed = db2.list_personas(conv.id)
            assert len(listed) == 1
            assert listed[0].id == p.id
        finally:
            db2.close()

    def test_migration_2_upgrades_legacy_v1_db(self, tmp_path):
        """A DB that was stamped at schema version 1 should upgrade
        cleanly to version 2 and gain an empty personas table + the
        messages.persona_id column."""
        import sqlite3
        from mchat.db import Database, CURRENT_SCHEMA_VERSION

        # Create a DB at version 1 by running migration 1 only.
        path = tmp_path / "legacy_v1.db"
        raw = sqlite3.connect(str(path))
        # Let Database handle the v1 migration then force version back
        # to 1 and drop the v2 artifacts to simulate a legacy DB.
        raw.close()

        db1 = Database(db_path=path)
        # Add a pre-migration message so we can verify it round-trips
        conv = db1.create_conversation("legacy")
        db1.add_message(Message(
            role=Role.USER, content="legacy msg", conversation_id=conv.id,
        ))
        db1.close()

        # Manually roll back to v1 state: drop personas and persona_id
        raw = sqlite3.connect(str(path))
        raw.execute("DROP TABLE IF EXISTS personas")
        # We can't drop a column in old SQLite versions, but we can
        # leave it — the migration's IF NOT EXISTS / check should tolerate it.
        raw.execute("PRAGMA user_version = 1")
        raw.commit()
        raw.close()

        # Reopen through Database — should migrate to v2
        db2 = Database(db_path=path)
        try:
            version = db2._conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == CURRENT_SCHEMA_VERSION
            # personas table exists and is empty
            assert db2.list_personas(conv.id) == []
            # Legacy message still loads, persona_id is None
            msgs = db2.get_messages(conv.id)
            assert len(msgs) == 1
            assert msgs[0].content == "legacy msg"
            assert msgs[0].persona_id is None
        finally:
            db2.close()

    def test_personas_table_empty_on_fresh_db(self, db):
        """A brand-new DB should have no personas."""
        conv = db.create_conversation()
        assert db.list_personas(conv.id) == []
        assert db.list_personas_including_deleted(conv.id) == []

    def test_update_persona_sets_deleted_at_via_tombstone_not_update(self, db):
        """update_persona should not be used to tombstone — that's
        what tombstone_persona is for. Verify update preserves
        deleted_at as whatever the caller passed in (typically None)."""
        conv = db.create_conversation()
        p = db.create_persona(self._make_persona(conv.id))
        p.name = "Renamed"
        db.update_persona(p)
        listed = db.list_personas(conv.id)
        assert listed[0].name == "Renamed"
        assert listed[0].deleted_at is None
