import pytest

import fastship.release as relmod

_pkg_json = """{
  "name": "typrose",
  "version": "0.1.2",
  "main": "typrose.css",
  "style": "typrose.css"
}
"""


def _make_npm_project(root):
    (root / "package.json").write_text(_pkg_json, encoding="utf-8")


def test_get_npm_config(tmp_path, monkeypatch):
    _make_npm_project(tmp_path)
    monkeypatch.setenv("FASTSHIP_BRANCH", "main")
    cfg = relmod.get_npm_config(tmp_path)

    assert cfg.name == "typrose"
    assert cfg.version == "0.1.2"
    assert cfg.branch == "main"
    assert cfg.pkg_json == tmp_path / "package.json"


def test_write_npm_version_preserves_formatting(tmp_path):
    _make_npm_project(tmp_path)
    relmod._write_npm_version(tmp_path / "package.json", "0.2.0")

    txt = (tmp_path / "package.json").read_text(encoding="utf-8")
    assert '"version": "0.2.0"' in txt
    assert '"style": "typrose.css"' in txt  # rest of the file untouched
    assert txt.startswith("{\n  ")  # no json round-trip reformatting


def test_ship_bump_routes_to_package_json(tmp_path, monkeypatch):
    _make_npm_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTSHIP_BRANCH", "main")
    monkeypatch.setattr(relmod, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not shell out")))

    relmod.ship_bump(part=1)

    assert '"version": "0.2.0"' in (tmp_path / "package.json").read_text(encoding="utf-8")


def test_ship_npm_release_refuses_dirty_tree(tmp_path, monkeypatch):
    _make_npm_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTSHIP_BRANCH", "main")
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: True)

    with pytest.raises(SystemExit, match="Uncommitted changes"):
        relmod._ship_npm_release()


def test_ship_npm_release_tags_then_bumps(tmp_path, monkeypatch):
    _make_npm_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTSHIP_BRANCH", "main")
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: False)
    calls = []
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: calls.append(cmd))

    version = relmod._ship_npm_release()

    assert version == "0.1.2"
    assert any("git tag" in c and "v0.1.2" in c for c in calls)
    assert any(c == "git push origin v0.1.2" for c in calls)
    assert calls.index("git commit -am bump") > calls.index("git push origin v0.1.2")  # bump follows the tag push
    assert '"version": "0.1.3"' in (tmp_path / "package.json").read_text(encoding="utf-8")


def test_cli_entrypoints_return_none(tmp_path, monkeypatch):
    # Console scripts run `sys.exit(func())`: a returned string means exit code 1.
    _make_npm_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTSHIP_BRANCH", "main")
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: None)

    assert relmod.ship_bump(part=2) is None
