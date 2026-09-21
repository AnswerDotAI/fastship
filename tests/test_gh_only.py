"A repo with a static version and no package is released on GitHub only. There is no build, PyPI upload or changelog. nbdev3-template is one such repo."
import pytest
import fastship.release as relmod

_pyproject = """[project]
name = "nbdev3-template"
version = "0.0.4"

[tool.fastship]
branch = "master"

[tool.nbdev]
"""


def test_gh_only_project(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_pyproject, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cfg = relmod.get_config()
    assert cfg.gh_only and cfg.version == "0.0.4"
    assert relmod._nbdev_release() is None  # [tool.nbdev] alone must not claim a repo with no package

    relmod.ship_bump()
    assert 'version = "0.0.5"' in pyproject.read_text(encoding="utf-8")

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("", encoding="utf-8")
    assert not relmod.get_config().gh_only


def test_uv_workspace_root_is_not_releasable(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(_pyproject + "\n[tool.uv.workspace]\nmembers = []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "member").mkdir()
    (tmp_path / "member/__init__.py").write_text("", encoding="utf-8")  # a member checkout is not the root's package
    with pytest.raises(relmod.CliError, match="workspace"): relmod.get_config()
