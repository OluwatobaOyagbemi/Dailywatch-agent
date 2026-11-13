"""Practice tasks and 'why watch this' notes (offline: the Claude client is faked)."""
import json, sys, types, pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import guardrails as g  # noqa: E402
import watch_agent as w  # noqa: E402


def vids():
    return [{"id": "aaaaaaaaaaa", "title": "Speak with confidence", "channel": "TED", "description": "Tips"},
            {"id": "bbbbbbbbbbb", "title": "Storytelling 101", "channel": "Coach", "description": ""}]


# ── fake anthropic SDK ───────────────────────────────────────────────────────

class FakeAPIError(Exception):
    pass


class FakeStatusError(FakeAPIError):
    def __init__(self, code=500):
        super().__init__("boom"); self.status_code = code


def install_fake_sdk(monkeypatch, *, text=None, stop="end_turn", raise_exc=None):
    calls = []

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            if raise_exc:
                raise raise_exc
            block = types.SimpleNamespace(type="text", text=text)
            return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="thinking"), block])

    class Client:
        def __init__(self, **kw):
            self.beta = types.SimpleNamespace(messages=Messages())

    mod = types.SimpleNamespace(Anthropic=Client, RateLimitError=type("RateLimitError", (FakeAPIError,), {}),
                                APIStatusError=FakeStatusError, APIConnectionError=type("APIConnectionError", (FakeAPIError,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-1234567890")
    return calls


def test_notes_are_attached_and_cleaned(monkeypatch):
    calls = install_fake_sdk(monkeypatch, text=json.dumps({"notes": [
        {"n": 1, "note": "Three habits\nthat steady your voice. " + "x" * 300},
        {"n": 2, "note": "A simple story structure."},
        {"n": 9, "note": "out of range is ignored"}]}))
    v = vids()
    w.add_notes(v, "Public Speaking", {})
    assert v[1]["note"] == "A simple story structure."
    assert "\n" not in v[0]["note"] and len(v[0]["note"]) <= w.NOTE_MAX
    req = calls[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "Public Speaking" in req["messages"][0]["content"]


def test_no_key_means_no_call(monkeypatch):
    calls = install_fake_sdk(monkeypatch, text="{}")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    v = vids(); w.add_notes(v, "T", {})
    assert not calls and "note" not in v[0]


def test_notes_can_be_switched_off(monkeypatch):
    calls = install_fake_sdk(monkeypatch, text="{}")
    w.add_notes(vids(), "T", {"why_notes": False})
    assert not calls


@pytest.mark.parametrize("kwargs", [
    {"raise_exc": FakeStatusError(529)},
    {"text": "not json"},
    {"text": json.dumps({"notes": []}), "stop": "refusal"},
])
def test_failures_never_block_the_message(monkeypatch, kwargs):
    install_fake_sdk(monkeypatch, **kwargs)
    v = vids()
    w.add_notes(v, "T", {})          # must not raise
    assert all("note" not in x for x in v)


def test_notes_and_task_render_everywhere():
    v = vids(); v[0]["note"] = "Why <this> matters"
    day = {"theme": "T", "task": "Record a 60-second voice note"}
    tg = w.build_message("T", "monday", v, {}, day)
    assert "💡 Why &lt;this&gt; matters" in tg and "<b>Try this:</b> Record a 60-second voice note" in tg
    assert "Why: Why <this> matters" in w.build_plain("T", "monday", v, {}, day)
    em = w.build_html("T", "monday", v, {}, day)
    assert "💡 Why &lt;this&gt; matters" in em and "Try this:" in em


def test_page_data_carries_task_and_notes():
    v = vids(); v[0]["note"] = "n1"
    data = w.page_data("2026-11-13", "monday", {"theme": "T", "task": "Do it"}, {}, v)
    assert data["task"] == "Do it" and data["videos"][0]["note"] == "n1" and data["videos"][1]["note"] == ""


def test_task_and_notes_settings_validate():
    base = {"days": {"monday": {"theme": "x", "creators": ["TED"], "task": "Short task"}}}
    g.validate_config(dict(base, why_notes=True))
    with pytest.raises(g.ConfigError):
        g.validate_config({"days": {"monday": {"theme": "x", "task": "y" * 201}}})
    with pytest.raises(g.ConfigError):
        g.validate_config(dict(base, why_notes="yes"))


def test_anthropic_key_is_redacted(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SECRETSECRET")
    assert "SECRETSECRET" not in g.redact("auth failed for sk-ant-api03-SECRETSECRET")
    assert "sk-ant-other-KEYKEYKEYKEY" not in g.redact("leaked sk-ant-other-KEYKEYKEYKEY")
