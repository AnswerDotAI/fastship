import os
import builtins
from types import SimpleNamespace

import fastship.release as relmod


def test_ship_release_gh_no_changelog_edits_and_skips_empty_commit(monkeypatch, tmp_path, capsys):
    changefile = tmp_path / "CHANGELOG.md"
    changefile.write_text("# Changelog\n\n## Unreleased\n\n- Initial release\n", encoding="utf-8")

    state = SimpleNamespace(changelog_called=False, release_called=False)

    class FakeRelease:
        def __init__(self, **kwargs):
            self.changefile = changefile
            self.cfg = SimpleNamespace(version="0.1.0")

        def changelog(self):
            state.changelog_called = True
            return self

        def release(self):
            state.release_called = True
            return self

    cmds, editor_calls = [], []

    monkeypatch.setattr(relmod, "Release", FakeRelease)
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: False)
    monkeypatch.setattr(relmod, "run", lambda cmd: cmds.append(cmd) or "")
    monkeypatch.setattr(relmod.subprocess, "run", lambda args: editor_calls.append(args))
    monkeypatch.setattr(builtins, "input", lambda prompt="": "y")

    relmod.ship_release_gh(token="tok", repo="owner/repo", no_changelog=True)

    assert not state.changelog_called
    assert state.release_called
    assert editor_calls == [[os.environ.get("EDITOR", "nano"), changefile]]
    assert cmds == ["git push"]
    assert "Released 0.1.0" in capsys.readouterr().out


def test_changelog_raises_when_marker_is_missing(tmp_path):
    changefile = tmp_path / "CHANGELOG.md"
    changefile.write_text("# Changelog\n\n## 0.0.1\n\n- First release\n", encoding="utf-8")

    rel = object.__new__(relmod.Release)
    rel.changefile = changefile
    rel.cfg = SimpleNamespace(version="0.0.2", branch="main")
    rel.gh = SimpleNamespace(
        repos=SimpleNamespace(get_latest_release=lambda: SimpleNamespace(tag_name="0.0.1", published_at="2026-03-17T04:23:40Z")))
    rel.groups = {}

    try: rel.changelog()
    except ValueError as e: msg = str(e)
    else: raise AssertionError("Expected changelog() to fail when the marker is missing")

    assert str(changefile) in msg
    assert relmod.CHANGELOG_MARKER.strip() in msg
