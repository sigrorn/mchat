# ------------------------------------------------------------------
# Component: test_stats
# Responsibility: Tests for the pure stats helpers — ChatStats,
#                 compute_chat_stats, format_stats — plus the
#                 //stats command handler.
# Collaborators: ui.stats, ui.commands.history or wherever handle_stats
#                lives, db, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Message, Provider, Role
from mchat.models.persona import Persona, generate_persona_id


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "stats.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    return Config(config_path=tmp_path / "cfg.json")


def _add_user(db, conv_id, text):
    m = Message(role=Role.USER, content=text, conversation_id=conv_id)
    db.add_message(m)
    return m


def _add_assistant(db, conv_id, text, provider, persona_id=None):
    m = Message(
        role=Role.ASSISTANT,
        content=text,
        provider=provider,
        persona_id=persona_id,
        conversation_id=conv_id,
    )
    db.add_message(m)
    return m


def _make_persona(
    conv_id: int,
    name: str,
    provider: Provider = Provider.CLAUDE,
    **overrides,
):
    return Persona(
        conversation_id=conv_id,
        id=generate_persona_id(),
        provider=provider,
        name=name,
        name_slug=name.lower(),
        **overrides,
    )


class TestEstimateTokens:
    def test_zero_chars(self):
        from mchat.ui.stats import estimate_tokens
        assert estimate_tokens(0) == 0

    def test_negative_chars(self):
        from mchat.ui.stats import estimate_tokens
        assert estimate_tokens(-10) == 0

    def test_chars_divided_by_4(self):
        from mchat.ui.stats import estimate_tokens
        assert estimate_tokens(400) == 100
        assert estimate_tokens(1001) == 250  # floor division


class TestChatStatsWholeSection:
    """compute_chat_stats must always populate the whole-chat section
    with an 'all visibility' row first, followed by one row per persona."""

    def test_whole_chat_all_visibility_row_sums_raw_content(self, db, config):
        conv = db.create_conversation()
        _add_user(db, conv.id, "hello world")  # 11 chars
        _add_assistant(db, conv.id, "hi there", Provider.CLAUDE)  # 8 chars
        conv.messages = db.get_messages(conv.id)

        from mchat.ui.stats import compute_chat_stats
        stats = compute_chat_stats(conv, db, config)
        assert stats.whole.heading == "Whole chat"
        first = stats.whole.rows[0]
        assert first.label == "all visibility"
        assert first.chars == 11 + 8

    def test_whole_chat_has_one_row_per_persona(self, db, config):
        conv = db.create_conversation()
        p1 = _make_persona(conv.id, "Alice", Provider.CLAUDE)
        p2 = _make_persona(conv.id, "Bob", Provider.OPENAI)
        db.create_persona(p1)
        db.create_persona(p2)
        _add_user(db, conv.id, "q")
        _add_assistant(db, conv.id, "a1", Provider.CLAUDE, persona_id=p1.id)
        _add_assistant(db, conv.id, "a2", Provider.OPENAI, persona_id=p2.id)
        conv.messages = db.get_messages(conv.id)

        from mchat.ui.stats import compute_chat_stats
        stats = compute_chat_stats(conv, db, config)
        labels = [r.label for r in stats.whole.rows]
        assert "all visibility" in labels
        assert "Alice" in labels
        assert "Bob" in labels
        # Exactly 3 rows: all + two personas
        assert len(stats.whole.rows) == 3

    def test_whole_chat_per_persona_uses_build_context(self, db, config):
        """The per-persona row must reflect what build_context returns,
        which includes system prompt + actual context — so the number
        should be >= the raw user+assistant content for that persona."""
        conv = db.create_conversation()
        p = _make_persona(
            conv.id, "Alice", Provider.CLAUDE,
            system_prompt_override="You are a helpful tutor.",
        )
        db.create_persona(p)
        _add_user(db, conv.id, "q")
        _add_assistant(db, conv.id, "a", Provider.CLAUDE, persona_id=p.id)
        conv.messages = db.get_messages(conv.id)

        from mchat.ui.stats import compute_chat_stats
        stats = compute_chat_stats(conv, db, config)
        alice_row = next(r for r in stats.whole.rows if r.label == "Alice")
        # At minimum the system prompt override + user q + assistant a
        # → "You are a helpful tutor." (24) + "You are Alice..." identity
        # line (40+ chars) + "q" + "a" = well above 30
        assert alice_row.chars > 30

    def test_whole_chat_ignores_limit_mark(self, db, config):
        """The whole-chat per-persona row must ignore any active //limit."""
        conv = db.create_conversation()
        p = _make_persona(conv.id, "Alice", Provider.CLAUDE)
        db.create_persona(p)
        for i in range(4):
            _add_user(db, conv.id, f"u{i}")
            _add_assistant(
                db, conv.id, f"a{i}" * 100, Provider.CLAUDE, persona_id=p.id,
            )
        conv.messages = db.get_messages(conv.id)
        # Set a limit at message 5 (0-indexed: skip first 5)
        db.set_mark(conv.id, "#5", 5)
        conv.limit_mark = "#5"
        db.set_conversation_limit(conv.id, "#5")

        from mchat.ui.stats import compute_chat_stats
        stats = compute_chat_stats(conv, db, config)
        whole_all = next(r for r in stats.whole.rows if r.label == "all visibility")
        # Whole chat raw = all 8 messages
        total_chars = sum(len(m.content) for m in conv.messages)
        assert whole_all.chars == total_chars
        # After compute_chat_stats returns, conv.limit_mark is restored
        assert conv.limit_mark == "#5"

    def test_tombstoned_persona_gets_removed_suffix(self, db, config):
        from datetime import datetime, timezone
        conv = db.create_conversation()
        p = _make_persona(
            conv.id, "Archived", Provider.CLAUDE,
            deleted_at=datetime.now(timezone.utc),
        )
        db.create_persona(p)
        _add_user(db, conv.id, "q")
        _add_assistant(db, conv.id, "a", Provider.CLAUDE, persona_id=p.id)
        conv.messages = db.get_messages(conv.id)

        from mchat.ui.stats import compute_chat_stats
        stats = compute_chat_stats(conv, db, config)
        labels = [r.label for r in stats.whole.rows]
        assert "Archived (removed)" in labels


class TestChatStatsLimitedSection:
    """The limited section only appears when conv.limit_mark is set
    and reflects the actual outgoing context for each persona."""

    def test_no_limit_section_when_no_limit_mark(self, db, config):
        conv = db.create_conversation()
        _add_user(db, conv.id, "q")
        conv.messages = db.get_messages(conv.id)

        from mchat.ui.stats import compute_chat_stats
        stats = compute_chat_stats(conv, db, config)
        assert stats.limited is None

    def test_limit_section_present_when_limit_mark_set(self, db, config):
        conv = db.create_conversation()
        p = _make_persona(conv.id, "Alice", Provider.CLAUDE)
        db.create_persona(p)
        for i in range(4):
            _add_user(db, conv.id, f"u{i}")
            _add_assistant(db, conv.id, f"a{i}", Provider.CLAUDE, persona_id=p.id)
        conv.messages = db.get_messages(conv.id)
        db.set_mark(conv.id, "#5", 5)
        conv.limit_mark = "#5"

        from mchat.ui.stats import compute_chat_stats
        stats = compute_chat_stats(conv, db, config)
        assert stats.limited is not None
        assert "#5" in stats.limited.heading

    def test_limit_all_visibility_row_only_counts_post_cut_messages(
        self, db, config,
    ):
        conv = db.create_conversation()
        # Messages before the cut
        _add_user(db, conv.id, "before1")  # 7 chars
        _add_user(db, conv.id, "before2")
        # Messages after the cut
        _add_user(db, conv.id, "after1")  # 6 chars
        _add_user(db, conv.id, "after2")
        conv.messages = db.get_messages(conv.id)
        db.set_mark(conv.id, "#3", 2)  # cut at index 2
        conv.limit_mark = "#3"

        from mchat.ui.stats import compute_chat_stats
        stats = compute_chat_stats(conv, db, config)
        limit_all = next(
            r for r in stats.limited.rows if r.label == "all visibility"
        )
        # Only "after1" + "after2" = 6 + 6 = 12
        assert limit_all.chars == 12


class TestFormatStats:
    def test_format_whole_only(self):
        from mchat.ui.stats import (
            ChatStats,
            StatsRow,
            StatsSection,
            format_stats,
        )
        stats = ChatStats(
            whole=StatsSection(
                heading="Whole chat",
                rows=[
                    StatsRow("all visibility", 1000),
                    StatsRow("Alice", 400),
                ],
            ),
        )
        lines = format_stats(stats)
        assert lines[0] == "Whole chat"
        # Some line contains "1,000 chars"
        assert any("1,000 chars" in line for line in lines)
        assert any("Alice" in line for line in lines)
        assert any("~250 tokens" in line for line in lines)

    def test_format_with_limit_section(self):
        from mchat.ui.stats import (
            ChatStats,
            StatsRow,
            StatsSection,
            format_stats,
        )
        stats = ChatStats(
            whole=StatsSection(
                heading="Whole chat",
                rows=[StatsRow("all visibility", 2000)],
            ),
            limited=StatsSection(
                heading="Limit (#42)",
                rows=[StatsRow("all visibility", 800)],
            ),
        )
        lines = format_stats(stats)
        assert any(line == "Whole chat" for line in lines)
        assert any(line == "Limit (#42)" for line in lines)
        # Blank separator between sections
        assert "" in lines
