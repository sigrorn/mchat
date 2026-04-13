# ------------------------------------------------------------------
# Component: test_db_migrations
# Responsibility: Tests for the extracted db_migrations module —
#                 schema constants and migration functions.
# Collaborators: db_migrations, db
# ------------------------------------------------------------------
from __future__ import annotations

import pytest


class TestDbMigrationsExtracted:
    """#161 — db_migrations is a standalone module extracted from Database."""

    def test_module_importable(self):
        from mchat.db_migrations import SCHEMA, CURRENT_SCHEMA_VERSION, MIGRATIONS
        assert SCHEMA is not None
        assert CURRENT_SCHEMA_VERSION >= 5
        assert len(MIGRATIONS) == CURRENT_SCHEMA_VERSION

    def test_migrations_are_callables(self):
        """Each migration entry is a (version, callable) tuple."""
        from mchat.db_migrations import MIGRATIONS
        for version, fn in MIGRATIONS:
            assert isinstance(version, int)
            assert callable(fn)

    def test_versions_are_sequential(self):
        from mchat.db_migrations import MIGRATIONS
        versions = [v for v, _ in MIGRATIONS]
        assert versions == list(range(1, len(MIGRATIONS) + 1))

    def test_run_migrations_on_fresh_db(self, tmp_path):
        """run_migrations on a fresh DB should bring it to CURRENT_SCHEMA_VERSION."""
        import sqlite3
        from mchat.db_migrations import SCHEMA, CURRENT_SCHEMA_VERSION, run_migrations
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        run_migrations(conn)
        conn.commit()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == CURRENT_SCHEMA_VERSION
        conn.close()

    def test_database_still_works(self, tmp_path):
        """Database class should still work end-to-end with extracted migrations."""
        from mchat.db import Database
        db = Database(db_path=tmp_path / "test.db")
        conv = db.create_conversation("Test")
        assert conv.title == "Test"
        assert db.list_conversations()[0].id == conv.id
        db.close()
