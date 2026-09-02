from pathlib import Path

import fastship.release as relmod


def _make_rs_project(root: Path, configured: bool = True, dynamic: bool = True):
    cfg = """
[tool.fastship]
branch = "main"
""" if configured else ""
    pyproj = """[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "exhash"
""" + ('dynamic = ["version"]\n' if dynamic else 'version = "0.1.2"\n') + """
[tool.maturin]
python-source = "python"
""" + cfg
    cargo = """[package]
name = "exhash"
version = "0.1.2"
edition = "2021"
"""
    (root / "pyproject.toml").write_text(pyproj, encoding="utf-8")
    (root / "Cargo.toml").write_text(cargo, encoding="utf-8")


def test_get_rs_config(tmp_path):
    _make_rs_project(tmp_path)
    cfg = relmod.get_rs_config(tmp_path)

    assert cfg.version == "0.1.2"
    assert cfg.branch == "main"
    assert cfg.manifest_path == tmp_path / "Cargo.toml"


def test_ship_bump_routes_to_cargo_when_cargo_toml_present(tmp_path, monkeypatch):
    _make_rs_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: calls.append(cmd))

    relmod.ship_bump(part=1)

    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = "0.2.0"' not in pyproject
    assert 'version = "0.2.0"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")
    assert any("maturin develop" in c for c in calls)  # bump refreshes the local install


def test_ship_bump_uses_static_project_version_when_present(tmp_path, monkeypatch):
    pyproj = '[project]\nname = "myproj"\nversion = "0.1.2"\n'
    (tmp_path / "pyproject.toml").write_text(pyproj, encoding="utf-8")
    pkg = tmp_path / "myproj"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('__version__ = "0.1.2"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(relmod, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not shell out")))

    relmod.ship_bump(part=1)

    assert 'version = "0.2.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '__version__ = "0.1.2"' in (pkg / "__init__.py").read_text(encoding="utf-8")


def test_ship_tag_release_needs_no_token_or_flags(tmp_path, monkeypatch):
    # The rust flow is pure tag-push: no changelog machinery, no GitHub client, no prompts.
    _make_rs_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("FASTSHIP_TOKEN", raising=False)
    calls = []
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: calls.append(cmd))

    relmod._ship_tag_release("rust")

    assert any("git tag" in c and "v0.1.2" in c for c in calls)
    assert any(c == "git push origin v0.1.2" for c in calls)
    assert 'version = "0.1.3"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")  # patch bump after the tag
    assert calls.index("git commit -am bump") > calls.index("git push origin v0.1.2")


def test_ship_tag_release_refuses_dirty_tree(tmp_path, monkeypatch):
    _make_rs_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: True)

    import pytest
    with pytest.raises(relmod.CliError, match="Uncommitted changes"):
        relmod._ship_tag_release("rust")
