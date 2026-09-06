from pathlib import Path

import pytest, fastship.release as relmod


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


def _add_version_file(root: Path, text: str):
    pyproj = root / "pyproject.toml"
    pyproj.write_text(pyproj.read_text(encoding="utf-8") + 'version-files = ["wasm/package.json"]\n', encoding="utf-8")
    (root / "wasm").mkdir()
    (root / "wasm" / "package.json").write_text(text, encoding="utf-8")


def test_rs_version_files_bump_with_cargo(tmp_path, monkeypatch):
    _make_rs_project(tmp_path)
    _add_version_file(tmp_path, '{\n  "name": "@acme/exhash",\n  "version": "0.1.2"\n}\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: None)

    relmod.ship_bump(part=2)

    assert 'version = "0.1.3"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")
    assert '"version": "0.1.3"' in (tmp_path / "wasm" / "package.json").read_text(encoding="utf-8")


def test_rs_version_files_refuse_stale_copy(tmp_path, monkeypatch):
    _make_rs_project(tmp_path)
    _add_version_file(tmp_path, '{"version": "0.0.9"}\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: None)

    with pytest.raises(ValueError, match="exactly one"): relmod.ship_bump(part=2)
    assert 'version = "0.1.2"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")  # nothing written


def test_single_crate_missing_version_names_package(tmp_path):
    _make_rs_project(tmp_path)
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "exhash"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[package\]\.version"): relmod.get_rs_config(tmp_path).version


_ws_package = """[package]
name = "exhash"
version.workspace = true
edition = "2024"
"""
_ws_root = """[workspace]
members = ["py"]

[workspace.package]
version = "0.1.2"
"""


@pytest.mark.parametrize("version,new", [("0.1.2", "0.1.3"), ("9.8.7", "9.8.8")])
@pytest.mark.parametrize("workspace", [_ws_root, '[workspace]\nmembers = ["py"]\n'])
def test_workspace_bumps_independent_package_version(tmp_path, version, new, workspace):
    _make_rs_project(tmp_path)
    manifest = tmp_path / "Cargo.toml"
    cargo = workspace + _ws_package.replace("version.workspace = true", f'version = "{version}"')
    manifest.write_text(cargo, encoding="utf-8")
    cfg = relmod.get_rs_config(tmp_path)

    assert cfg.version == version
    assert relmod._cargo_bump(cfg, part=2) == new
    assert manifest.read_text(encoding="utf-8") == workspace + _ws_package.replace("version.workspace = true", f'version = "{new}"')


@pytest.mark.parametrize("cargo", [_ws_root, _ws_root + "\n" + _ws_package, _ws_package + "\n" + _ws_root])
def test_workspace_version_read_and_bump(tmp_path, monkeypatch, cargo):
    _make_rs_project(tmp_path)
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: None)

    assert relmod.get_rs_config(tmp_path).version == "0.1.2"
    relmod.ship_bump(part=2)

    assert (tmp_path/"Cargo.toml").read_text() == cargo.replace('version = "0.1.2"', 'version = "0.1.3"')


@pytest.mark.parametrize("copy_version", ["0.1.2", "0.0.9"])
def test_workspace_version_files(tmp_path, monkeypatch, copy_version):
    _make_rs_project(tmp_path)
    _add_version_file(tmp_path, f'{{"version": "{copy_version}"}}\n')
    manifest = tmp_path/"Cargo.toml"
    manifest.write_text(_ws_root.replace('["py"]', '["wasm"]') + _ws_package)
    member = tmp_path/"wasm"/"Cargo.toml"
    member.write_text(_ws_package.replace('name = "exhash"', 'name = "exhash-wasm"'))
    paths = [manifest, member, tmp_path/"wasm"/"package.json"]
    before = [p.read_text() for p in paths]
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: calls.append(cmd))

    if copy_version == "0.0.9":
        with pytest.raises(ValueError, match="exactly one"): relmod.ship_bump(part=2)
        assert [p.read_text() for p in paths] == before
        assert not calls
    else:
        relmod.ship_bump(part=2)
        assert [p.read_text() for p in paths] == [s.replace("0.1.2", "0.1.3") for s in before]
        assert calls == ["maturin develop"]


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
    with pytest.raises(relmod.CliError, match="Uncommitted changes"): relmod._ship_tag_release("rust")
