import os, asyncio
import builtins
from types import SimpleNamespace

import pytest

import fastship.release as relmod


def test_post_version_bumps():
    assert relmod.bump_version("0.0.2026082005.post1") == "0.0.2026082005.post2"
    assert relmod.bump_version("0.0.2026082005.post2", unbump=True) == "0.0.2026082005.post1"
    assert relmod.bump_version("0.0.2026082005.post1", part=2) == "0.0.2026082006"


def test_plain_python_tag_release(tmp_path, monkeypatch, capsys):
    pyproject = '''[project]
name = "xmojo"
version = "0.0.2026082005.post1"

[tool.setuptools]
package-dir = {"" = "python"}

[tool.fastship]
branch = "main"
release = "tag"
version-files = ["bazel/versions.bzl"]
'''
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    package = tmp_path / "python/xmojo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    versions = tmp_path / "bazel/versions.bzl"
    versions.parent.mkdir()
    versions.write_text('XMOJO_VERSION="0.0.2026082005.post1"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: False)
    monkeypatch.setattr(relmod, "Release", lambda **kwargs: (_ for _ in ()).throw(AssertionError("local release used")))
    calls = []
    monkeypatch.setattr(relmod, "run", lambda command: calls.append(command) or "")

    asyncio.run(relmod.ship_release())

    assert calls == [
        "git tag -a v0.0.2026082005.post1 -m v0.0.2026082005.post1",
        "git push origin main",
        "git push origin v0.0.2026082005.post1",
        "git commit -am bump",
        "git push"]
    assert 'version = "0.0.2026082005.post2"' in (tmp_path / "pyproject.toml").read_text()
    assert versions.read_text() == 'XMOJO_VERSION="0.0.2026082005.post2"\n'
    assert (package / "__init__.py").read_text() == ""
    assert "Release started: v0.0.2026082005.post1" in capsys.readouterr().out


def test_version_files_are_validated_before_writing(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\nversion = "1.0.post1"\n', encoding="utf-8")
    copy = tmp_path / "version.txt"
    copy.write_text("1.0.post1 twice: 1.0.post1\n", encoding="utf-8")
    cfg = SimpleNamespace(version="1.0.post1", pyproject=pyproject, init_file=None, version_files=[copy])

    with pytest.raises(ValueError, match="exactly one"): relmod._write_config_version(cfg, "1.0.post2")

    assert 'version = "1.0.post1"' in pyproject.read_text()
    assert copy.read_text() == "1.0.post1 twice: 1.0.post1\n"


def test_ship_release_gh_no_changelog_edits_and_skips_empty_commit(monkeypatch, tmp_path, capsys):
    changefile = tmp_path / "CHANGELOG.md"
    changefile.write_text("# Changelog\n\n## Unreleased\n\n- Initial release\n", encoding="utf-8")

    state = SimpleNamespace(changelog_called=False, release_called=False)

    class FakeRelease:
        def __init__(self, **kwargs):
            self.changefile = changefile
            self.cfg = SimpleNamespace(version="0.1.0")

        async def changelog(self):
            state.changelog_called = True
            return self

        async def release(self):
            state.release_called = True
            return self

    cmds, editor_calls = [], []

    monkeypatch.setattr(relmod, "Release", FakeRelease)
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: False)
    monkeypatch.setattr(relmod, "run", lambda cmd: cmds.append(cmd) or "")
    monkeypatch.setattr(relmod.subprocess, "run", lambda args: editor_calls.append(args))
    monkeypatch.setattr(builtins, "input", lambda prompt="": "y")

    asyncio.run(relmod.ship_release_gh(token="tok", repo="owner/repo", no_changelog=True))

    assert not state.changelog_called
    assert state.release_called
    assert editor_calls == [[os.environ.get("EDITOR", "nano"), changefile]]
    assert cmds == ["git push"]
    assert "GitHub release created: 0.1.0" in capsys.readouterr().out


def test_ship_release_gh_can_skip_editor_and_prompt(monkeypatch, tmp_path):
    changefile = tmp_path / "CHANGELOG.md"
    changefile.write_text("# Changelog\n", encoding="utf-8")
    state = SimpleNamespace(release_called=False)

    class FakeRelease:
        def __init__(self, **kwargs):
            self.changefile = changefile
            self.cfg = SimpleNamespace(version="0.1.0")

        async def release(self):
            state.release_called = True
            return self

    cmds = []
    monkeypatch.setattr(relmod, "Release", FakeRelease)
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: False)
    monkeypatch.setattr(relmod, "run", lambda cmd: cmds.append(cmd) or "")
    monkeypatch.setattr(relmod.subprocess, "run", lambda args: (_ for _ in ()).throw(AssertionError("editor opened")))
    monkeypatch.setattr(builtins, "input", lambda prompt="": (_ for _ in ()).throw(AssertionError("prompted")))

    asyncio.run(relmod.ship_release_gh(token="tok", repo="owner/repo", no_changelog=True, no_editor=True, yes=True))

    assert state.release_called
    assert cmds == ["git push"]


def test_ship_release_builds_before_publishing(monkeypatch):
    calls = []

    class FakeRelease:
        def __init__(self, **kwargs):
            self.changefile = "CHANGELOG.md"
            self.cfg = SimpleNamespace(version="0.1.0", root=".", wheel_only=False)

        async def release(self):
            calls.append(("gh", {}))
            return self

    async def fake_prepare(*args, **kwargs): calls.append(("prepare", {}))

    monkeypatch.setattr(relmod, "Release", FakeRelease)
    monkeypatch.setattr(relmod, "_prepare_release", fake_prepare)
    monkeypatch.setattr(relmod, "_build_dist", lambda *a, **k: calls.append(("build", k)))
    monkeypatch.setattr(relmod, "_commit_release", lambda *a, **k: calls.append(("commit", {})))
    monkeypatch.setattr(relmod, "_upload_dist", lambda *a, **k: calls.append(("upload", k)))
    monkeypatch.setattr(relmod, "ship_bump", lambda: calls.append(("bump", {})))
    monkeypatch.setattr(relmod, "run", lambda cmd: calls.append((cmd, {})))

    asyncio.run(relmod.ship_release(token="tok", repo="owner/repo", repository="pypi", no_changelog=True, no_editor=True, yes=True))

    assert [o[0] for o in calls] == ["prepare", "build", "commit", "gh", "upload", "bump", "git commit -am bump", "git push"]


def test_build_dist_can_be_wheel_only(monkeypatch, tmp_path):
    calls = []
    cfg = SimpleNamespace(root=tmp_path, wheel_only=True)
    monkeypatch.setattr(relmod, "run", lambda cmd: calls.append(cmd))

    relmod._build_dist(cfg)

    assert any("-m build --wheel" in o for o in calls)
    assert calls[-1] == "twine check dist/*"


def test_changelog_raises_when_marker_is_missing(tmp_path):
    changefile = tmp_path / "CHANGELOG.md"
    changefile.write_text("# Changelog\n\n## 0.0.1\n\n- First release\n", encoding="utf-8")

    rel = object.__new__(relmod.Release)
    rel.changefile = changefile
    rel.cfg = SimpleNamespace(version="0.0.2", branch="main")
    async def _fake_lr(): return SimpleNamespace(tag_name="0.0.1", published_at="2026-03-17T04:23:40Z")
    rel.gh = SimpleNamespace(repos=SimpleNamespace(get_latest_release=_fake_lr))
    rel.groups = {}

    try: asyncio.run(rel.changelog())
    except ValueError as e: msg = str(e)
    else: raise AssertionError("Expected changelog() to fail when the marker is missing")

    assert str(changefile) in msg
    assert relmod.CHANGELOG_MARKER.strip() in msg
