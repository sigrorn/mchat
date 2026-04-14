# ------------------------------------------------------------------
# Component: test_context_builder
# Responsibility: Tests for the pure context-policy functions that
#                 decide which messages a provider sees (build_context)
#                 and which indices fall outside that context
#                 (compute_excluded_indices) for display shading.
# Collaborators: ui.context_builder, db, models
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Message, Provider, Role
from mchat.ui.context_builder import (
    build_context,
    compute_excluded_indices,
    pin_matches,
)


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def config(tmp_path):
    return Config(config_path=tmp_path / "cfg.json")


@pytest.fixture
def conv_with_history(db):
    """Fixture: conversation with 6 messages, user-assistant pairs.

    Indices:
      0: user "q1"
      1: assistant "a1" (Claude)
      2: user "q2"
      3: assistant "a2" (Claude)
      4: user "q3"
      5: assistant "a3" (Claude)
    """
    conv = db.create_conversation()
    contents = [
        (Role.USER, "q1", None),
        (Role.ASSISTANT, "a1", Provider.CLAUDE),
        (Role.USER, "q2", None),
        (Role.ASSISTANT, "a2", Provider.CLAUDE),
        (Role.USER, "q3", None),
        (Role.ASSISTANT, "a3", Provider.CLAUDE),
    ]
    for role, text, prov in contents:
        db.add_message(Message(
            role=role, content=text, provider=prov,
            conversation_id=conv.id,
        ))
    conv.messages = db.get_messages(conv.id)
    return conv


class TestPinMatches:
    def test_none_target_is_no_match(self):
        assert pin_matches(None, Provider.CLAUDE) is False

    def test_all_target_matches_every_provider(self):
        for p in Provider:
            assert pin_matches("all", p) is True

    def test_single_target(self):
        assert pin_matches("claude", Provider.CLAUDE) is True
        assert pin_matches("claude", Provider.OPENAI) is False

    def test_multi_target(self):
        assert pin_matches("claude,openai", Provider.CLAUDE) is True
        assert pin_matches("claude,openai", Provider.OPENAI) is True
        assert pin_matches("claude,openai", Provider.GEMINI) is False


class TestBuildContext:
    def test_no_limit_returns_full_history(self, db, config, conv_with_history):
        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        # No system prompt configured → no SYSTEM message
        assert [m.content for m in ctx] == ["q1", "a1", "q2", "a2", "q3", "a3"]

    def test_system_prompt_prepended(self, db, config, conv_with_history):
        conv_with_history.system_prompt = "be terse"
        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        assert ctx[0].role == Role.SYSTEM
        assert "be terse" in ctx[0].content

    def test_limit_slices_earlier_history(self, db, config, conv_with_history):
        # Set limit to message 3 (index 2 = "q2")
        db.set_mark(conv_with_history.id, "#3", 2)
        conv_with_history.limit_mark = "#3"
        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        assert [m.content for m in ctx] == ["q2", "a2", "q3", "a3"]

    def test_pinned_before_cutoff_is_rescued(self, db, config, conv_with_history):
        # Pin message 0 (user "q1") targeted at Claude
        conv_with_history.messages[0].pinned = True
        conv_with_history.messages[0].pin_target = "claude"
        db.set_pinned(conv_with_history.messages[0].id, True, "claude")
        # Limit to message 5 — "q1" falls before the cutoff
        db.set_mark(conv_with_history.id, "#5", 4)
        conv_with_history.limit_mark = "#5"
        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        # q1 is prepended (rescued) plus the post-cutoff slice
        assert [m.content for m in ctx] == ["q1", "q3", "a3"]

    def test_pinned_not_rescued_for_other_provider(self, db, config, conv_with_history):
        # Pin message 0 targeted ONLY at Claude
        conv_with_history.messages[0].pinned = True
        conv_with_history.messages[0].pin_target = "claude"
        db.set_pinned(conv_with_history.messages[0].id, True, "claude")
        db.set_mark(conv_with_history.id, "#5", 4)
        conv_with_history.limit_mark = "#5"
        # Build context for GPT — q1 must NOT be rescued
        ctx = build_context(conv_with_history, Provider.OPENAI, db, config)
        assert "q1" not in [m.content for m in ctx]


class TestComputeExcludedIndices:
    def test_no_limit_returns_empty(self, db, conv_with_history):
        assert compute_excluded_indices(conv_with_history, db, {Provider.CLAUDE}) == set()

    def test_limit_excludes_earlier_indices(self, db, conv_with_history):
        db.set_mark(conv_with_history.id, "#4", 3)
        conv_with_history.limit_mark = "#4"
        excluded = compute_excluded_indices(
            conv_with_history, db, {Provider.CLAUDE}
        )
        assert excluded == {0, 1, 2}

    def test_pinned_not_excluded_when_configured(self, db, conv_with_history):
        # Pin index 1 targeting Claude
        conv_with_history.messages[1].pinned = True
        conv_with_history.messages[1].pin_target = "claude"
        db.set_pinned(conv_with_history.messages[1].id, True, "claude")
        db.set_mark(conv_with_history.id, "#4", 3)
        conv_with_history.limit_mark = "#4"
        excluded = compute_excluded_indices(
            conv_with_history, db, {Provider.CLAUDE}
        )
        # Index 1 is pinned for a configured provider → not shaded
        assert excluded == {0, 2}

    def test_pinned_excluded_when_target_not_configured(self, db, conv_with_history):
        conv_with_history.messages[1].pinned = True
        conv_with_history.messages[1].pin_target = "gemini"
        db.set_pinned(conv_with_history.messages[1].id, True, "gemini")
        db.set_mark(conv_with_history.id, "#4", 3)
        conv_with_history.limit_mark = "#4"
        # Only Claude is configured — Gemini-targeted pin isn't live, so shade
        excluded = compute_excluded_indices(
            conv_with_history, db, {Provider.CLAUDE}
        )
        assert excluded == {0, 1, 2}


class TestBuildContextWithPersonaTarget:
    """Stage 2.5 — build_context now takes a PersonaTarget instead of
    a bare Provider. Existing Provider calls still work via a back-
    compat shim. The persona's system_prompt_override is applied via
    resolve_persona_prompt, and a non-null created_at_message_index
    slices prior history."""

    def _make_persona(self, conv_id, name="Evaluator", slug="evaluator",
                      system_prompt_override=None,
                      created_at_message_index=None):
        from mchat.models.persona import Persona, generate_persona_id
        return Persona(
            conversation_id=conv_id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name=name,
            name_slug=slug,
            system_prompt_override=system_prompt_override,
            created_at_message_index=created_at_message_index,
        )

    def test_accepts_persona_target(self, db, config, conv_with_history):
        """build_context(conv, target, db, config) where target is a
        PersonaTarget. The resolved provider is target.provider."""
        from mchat.ui.persona_target import PersonaTarget
        target = PersonaTarget(persona_id="claude", provider=Provider.CLAUDE)
        ctx = build_context(conv_with_history, target, db, config)
        assert [m.content for m in ctx] == ["q1", "a1", "q2", "a2", "q3", "a3"]

    def test_accepts_bare_provider_for_back_compat(self, db, config, conv_with_history):
        """Existing callers still passing a bare Provider must keep
        working until Stage 2.6 updates send_controller."""
        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        assert [m.content for m in ctx] == ["q1", "a1", "q2", "a2", "q3", "a3"]

    def test_persona_system_prompt_override_replaces_global(
        self, db, config, conv_with_history,
    ):
        """D6: a persona with a non-null system_prompt_override uses
        that prompt instead of the global provider prompt."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget
        config.set("system_prompt_claude", "Global Claude prompt")
        config.save()

        p = self._make_persona(
            conv_with_history.id,
            system_prompt_override="Be ruthless and direct",
        )
        db.create_persona(p)
        target = PersonaTarget(persona_id=p.id, provider=p.provider)

        ctx = build_context(conv_with_history, target, db, config)
        # First message is the SYSTEM block
        assert ctx[0].role == Role.SYSTEM
        assert "Be ruthless and direct" in ctx[0].content
        # Global Claude prompt is NOT present — override replaced it
        assert "Global Claude prompt" not in ctx[0].content

    def test_persona_with_none_prompt_falls_through_to_global(
        self, db, config, conv_with_history,
    ):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget
        config.set("system_prompt_claude", "Global Claude prompt")
        config.save()

        p = self._make_persona(conv_with_history.id, system_prompt_override=None)
        db.create_persona(p)
        target = PersonaTarget(persona_id=p.id, provider=p.provider)

        ctx = build_context(conv_with_history, target, db, config)
        assert ctx[0].role == Role.SYSTEM
        assert "Global Claude prompt" in ctx[0].content

    def test_persona_history_cutoff_slices_prior_history(
        self, db, config, conv_with_history,
    ):
        """A persona with created_at_message_index=2 only sees messages
        at index >= 2 (q2 onwards). This runs AFTER the //limit slice,
        so //limit and persona cutoff stack."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        p = self._make_persona(
            conv_with_history.id, created_at_message_index=2,
        )
        db.create_persona(p)
        target = PersonaTarget(persona_id=p.id, provider=p.provider)

        ctx = build_context(conv_with_history, target, db, config)
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert contents == ["q2", "a2", "q3", "a3"]

    def test_persona_cutoff_stacks_with_limit(
        self, db, config, conv_with_history,
    ):
        """Both //limit and the persona history cutoff apply.
        //limit to index 2, persona cutoff at index 4 → only index 4+.
        """
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        db.set_mark(conv_with_history.id, "#3", 2)
        conv_with_history.limit_mark = "#3"

        p = self._make_persona(
            conv_with_history.id, created_at_message_index=4,
        )
        db.create_persona(p)
        target = PersonaTarget(persona_id=p.id, provider=p.provider)

        ctx = build_context(conv_with_history, target, db, config)
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert contents == ["q3", "a3"]

    def test_persona_cutoff_none_means_full_history(
        self, db, config, conv_with_history,
    ):
        """created_at_message_index=None (the default) means the
        persona sees full history."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        p = self._make_persona(
            conv_with_history.id, created_at_message_index=None,
        )
        db.create_persona(p)
        target = PersonaTarget(persona_id=p.id, provider=p.provider)

        ctx = build_context(conv_with_history, target, db, config)
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert contents == ["q1", "a1", "q2", "a2", "q3", "a3"]

    def test_synthetic_default_target_behaves_like_bare_provider(
        self, db, config, conv_with_history,
    ):
        """A PersonaTarget with persona_id = provider.value (the
        synthetic default) produces identical context to passing the
        Provider directly — D1's unified code path."""
        from mchat.ui.persona_target import synthetic_default

        ctx_synthetic = build_context(
            conv_with_history, synthetic_default(Provider.CLAUDE), db, config,
        )
        ctx_provider = build_context(
            conv_with_history, Provider.CLAUDE, db, config,
        )
        assert [m.content for m in ctx_synthetic] == [m.content for m in ctx_provider]


class TestCrossPersonaLabelUsesName:
    """#126 — cross-persona relabeling must use the human persona name,
    not the opaque persona_id, so providers don't echo internal ids back."""

    def test_cross_persona_label_uses_persona_name(self, db, config):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        conv = db.create_conversation()
        # Two explicit personas, one Claude one OpenAI
        p_claude = Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="claudebot", name_slug="claudebot",
        )
        p_gpt = Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.OPENAI, name="gptbot", name_slug="gptbot",
        )
        db.create_persona(p_claude)
        db.create_persona(p_gpt)

        # User message + claudebot response — both already in DB
        db.add_message(Message(
            role=Role.USER, content="hi", conversation_id=conv.id,
        ))
        db.add_message(Message(
            role=Role.ASSISTANT, content="hello from claude",
            provider=Provider.CLAUDE, persona_id=p_claude.id,
            conversation_id=conv.id,
        ))
        # Reload conv with messages
        conv = db.get_conversation(conv.id)
        conv.messages = db.get_messages(conv.id)

        # Build context for gptbot — should see claudebot's response
        # relabeled as user-context, with the persona NAME, not the id.
        ctx = build_context(
            conv,
            PersonaTarget(persona_id=p_gpt.id, provider=Provider.OPENAI),
            db, config,
        )
        labels = [m.content for m in ctx]
        # The persona name must appear in the relabel
        assert any("claudebot" in c for c in labels), (
            f"expected 'claudebot' in cross-persona context, got: {labels}"
        )
        # The opaque id must NOT appear
        assert not any(p_claude.id in c for c in labels), (
            f"opaque persona_id leaked into context: {labels}"
        )


class TestDiagramInstructionInjection:
    """#151 — build_context must inject a diagram instruction into the
    system prompt when diagram tools are available."""

    def test_diagram_instruction_present_when_tools_available(
        self, db, config, conv_with_history, monkeypatch,
    ):
        from mchat import dot_renderer, mermaid_renderer

        dot_renderer.is_graphviz_available.cache_clear()
        mermaid_renderer.is_mmdc_available.cache_clear()
        monkeypatch.setattr(dot_renderer, "is_graphviz_available", lambda: True)
        monkeypatch.setattr(mermaid_renderer, "is_mmdc_available", lambda: False)

        # Ensure there's a system prompt so build_context creates a
        # SYSTEM message we can inspect for the diagram fragment.
        conv_with_history.system_prompt = "Be helpful."

        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        sys_msgs = [m for m in ctx if m.role == Role.SYSTEM]
        assert sys_msgs
        sys_text = sys_msgs[0].content
        assert "dot" in sys_text.lower()

    def test_no_diagram_instruction_when_nothing_installed(
        self, db, config, conv_with_history, monkeypatch,
    ):
        from mchat import dot_renderer, mermaid_renderer

        dot_renderer.is_graphviz_available.cache_clear()
        mermaid_renderer.is_mmdc_available.cache_clear()
        monkeypatch.setattr(dot_renderer, "is_graphviz_available", lambda: False)
        monkeypatch.setattr(mermaid_renderer, "is_mmdc_available", lambda: False)

        conv_with_history.system_prompt = "Be helpful."

        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        sys_msgs = [m for m in ctx if m.role == Role.SYSTEM]
        assert sys_msgs
        sys_text = sys_msgs[0].content.lower()
        # Should not contain diagram instructions
        assert "fenced code block" not in sys_text

    def test_diagram_format_none_suppresses_injection(
        self, db, config, conv_with_history, monkeypatch,
    ):
        from mchat import dot_renderer, mermaid_renderer

        dot_renderer.is_graphviz_available.cache_clear()
        mermaid_renderer.is_mmdc_available.cache_clear()
        monkeypatch.setattr(dot_renderer, "is_graphviz_available", lambda: True)
        monkeypatch.setattr(mermaid_renderer, "is_mmdc_available", lambda: True)
        config.set("diagram_format", "none")

        conv_with_history.system_prompt = "Be helpful."

        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        sys_msgs = [m for m in ctx if m.role == Role.SYSTEM]
        assert sys_msgs
        sys_text = sys_msgs[0].content.lower()
        assert "fenced code block" not in sys_text

    def test_diagram_instruction_injected_even_without_system_prompt(
        self, db, config, conv_with_history, monkeypatch,
    ):
        """When no system prompt is set but diagram tools are available,
        build_context should still create a SYSTEM message with just
        the diagram instruction."""
        from mchat import dot_renderer, mermaid_renderer

        dot_renderer.is_graphviz_available.cache_clear()
        mermaid_renderer.is_mmdc_available.cache_clear()
        monkeypatch.setattr(dot_renderer, "is_graphviz_available", lambda: True)
        monkeypatch.setattr(mermaid_renderer, "is_mmdc_available", lambda: False)

        conv_with_history.system_prompt = None

        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config)
        sys_msgs = [m for m in ctx if m.role == Role.SYSTEM]
        assert sys_msgs
        sys_text = sys_msgs[0].content
        assert "dot" in sys_text.lower()


class TestVisiblePersonaIdsFilter:
    """#169 — visible_persona_ids filters assistant messages to ancestor chain."""

    def test_filter_keeps_only_listed_personas(self, db, config):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        conv = db.create_conversation()
        pa = Persona(conversation_id=conv.id, id=generate_persona_id(),
                     provider=Provider.CLAUDE, name="A", name_slug="a")
        pb = Persona(conversation_id=conv.id, id=generate_persona_id(),
                     provider=Provider.OPENAI, name="B", name_slug="b")
        pc = Persona(conversation_id=conv.id, id=generate_persona_id(),
                     provider=Provider.GEMINI, name="C", name_slug="c",
                     runs_after=pa.id)
        db.create_persona(pa)
        db.create_persona(pb)
        db.create_persona(pc)

        db.add_message(Message(role=Role.USER, content="hello",
                               conversation_id=conv.id))
        db.add_message(Message(role=Role.ASSISTANT, content="from A",
                               provider=Provider.CLAUDE, persona_id=pa.id,
                               conversation_id=conv.id))
        db.add_message(Message(role=Role.ASSISTANT, content="from B",
                               provider=Provider.OPENAI, persona_id=pb.id,
                               conversation_id=conv.id))
        conv.messages = db.get_messages(conv.id)

        target_c = PersonaTarget(persona_id=pc.id, provider=Provider.GEMINI)
        ctx = build_context(conv, target_c, db, config,
                            visible_persona_ids={pa.id, pc.id})
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert any("hello" in c for c in contents)
        assert any("from A" in c for c in contents)  # may be relabeled as "[A responded]: from A"
        assert not any("from B" in c for c in contents)

    def test_filter_preserves_own_history(self, db, config):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        conv = db.create_conversation()
        pa = Persona(conversation_id=conv.id, id=generate_persona_id(),
                     provider=Provider.CLAUDE, name="A", name_slug="a")
        db.create_persona(pa)
        db.add_message(Message(role=Role.USER, content="q1",
                               conversation_id=conv.id))
        db.add_message(Message(role=Role.ASSISTANT, content="a1",
                               provider=Provider.CLAUDE, persona_id=pa.id,
                               conversation_id=conv.id))
        conv.messages = db.get_messages(conv.id)

        target_a = PersonaTarget(persona_id=pa.id, provider=Provider.CLAUDE)
        ctx = build_context(conv, target_a, db, config,
                            visible_persona_ids={pa.id})
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert "a1" in contents

    def test_legacy_persona_id_none_matched_by_provider(self, db, config):
        from mchat.ui.persona_target import PersonaTarget

        conv = db.create_conversation()
        db.add_message(Message(role=Role.USER, content="q1",
                               conversation_id=conv.id))
        db.add_message(Message(role=Role.ASSISTANT, content="legacy",
                               provider=Provider.CLAUDE, persona_id=None,
                               conversation_id=conv.id))
        conv.messages = db.get_messages(conv.id)

        target = PersonaTarget(persona_id="claude", provider=Provider.CLAUDE)
        ctx = build_context(conv, target, db, config,
                            visible_persona_ids={"claude"})
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert "legacy" in contents

    def test_none_visible_ids_means_no_filter(self, db, config, conv_with_history):
        ctx = build_context(conv_with_history, Provider.CLAUDE, db, config,
                            visible_persona_ids=None)
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert len(contents) == 6

    def test_pinned_assistant_from_excluded_persona_filtered(self, db, config):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        conv = db.create_conversation()
        pa = Persona(conversation_id=conv.id, id=generate_persona_id(),
                     provider=Provider.CLAUDE, name="A", name_slug="a")
        pb = Persona(conversation_id=conv.id, id=generate_persona_id(),
                     provider=Provider.OPENAI, name="B", name_slug="b")
        db.create_persona(pa)
        db.create_persona(pb)

        db.add_message(Message(role=Role.ASSISTANT, content="pinned from B",
                               provider=Provider.OPENAI, persona_id=pb.id,
                               conversation_id=conv.id, pinned=True,
                               pin_target="all"))
        db.add_message(Message(role=Role.USER, content="hello",
                               conversation_id=conv.id))
        conv.messages = db.get_messages(conv.id)

        target_a = PersonaTarget(persona_id=pa.id, provider=Provider.CLAUDE)
        ctx = build_context(conv, target_a, db, config,
                            visible_persona_ids={pa.id})
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert "pinned from B" not in contents

    def test_dag_root_sees_only_own_history(self, db, config):
        """Regression: DAG roots must use visible_persona_ids={self},
        not None — root A must not see root B's prior history."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        conv = db.create_conversation()
        pa = Persona(conversation_id=conv.id, id=generate_persona_id(),
                     provider=Provider.CLAUDE, name="A", name_slug="a")
        pb = Persona(conversation_id=conv.id, id=generate_persona_id(),
                     provider=Provider.OPENAI, name="B", name_slug="b")
        db.create_persona(pa)
        db.create_persona(pb)

        # Prior exchange: user asked, both roots responded
        db.add_message(Message(role=Role.USER, content="prior question",
                               conversation_id=conv.id))
        db.add_message(Message(role=Role.ASSISTANT, content="A prior",
                               provider=Provider.CLAUDE, persona_id=pa.id,
                               conversation_id=conv.id))
        db.add_message(Message(role=Role.ASSISTANT, content="B prior",
                               provider=Provider.OPENAI, persona_id=pb.id,
                               conversation_id=conv.id))
        # New user prompt
        db.add_message(Message(role=Role.USER, content="new question",
                               conversation_id=conv.id))
        conv.messages = db.get_messages(conv.id)

        # Root A's context with visible_persona_ids={A} — simulates
        # what _start_dag_send now passes.
        target_a = PersonaTarget(persona_id=pa.id, provider=Provider.CLAUDE)
        ctx = build_context(conv, target_a, db, config,
                            visible_persona_ids={pa.id})
        contents = [m.content for m in ctx if m.role != Role.SYSTEM]
        assert any("A prior" in c for c in contents)
        assert not any("B prior" in c for c in contents)
        assert any("new question" in c for c in contents)
