import pytest
import fastship.release as relmod

_pyproject = '''[project]
name = "nbdev3-template"
version = "0.0.4"
[tool.fastship]
branch = "main"
[tool.nbdev]
'''


def test_package_less_project_can_bump_without_a_package(tmp_path, monkeypatch):
    pyproject = tmp_path/"pyproject.toml"
    pyproject.write_text(_pyproject)
    monkeypatch.chdir(tmp_path)
    assert relmod.get_config().gh_only
    relmod.ship_bump()
    assert pyproject.read_text() == _pyproject.replace("0.0.4", "0.0.5")
    (tmp_path/"pkg").mkdir()
    (tmp_path/"pkg"/"__init__.py").write_text("")
    assert not relmod.get_config().gh_only


def test_uv_workspace_root_is_not_releasable(tmp_path):
    (tmp_path/"pyproject.toml").write_text(_pyproject + '\n[tool.uv.workspace]\nmembers = ["member"]\n')
    (tmp_path/"member").mkdir()
    (tmp_path/"member"/"__init__.py").write_text("")
    with pytest.raises(relmod.CliError, match="workspace"): relmod.get_config(tmp_path)
