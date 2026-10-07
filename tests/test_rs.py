import pytest
import fastship.release as relmod

_package = '[package]\nname = "demo"\nversion = "9.8.7"\n'
_inherited = '[package]\nname = "demo"\nversion.workspace = true\n'
_workspace = '[workspace]\nmembers = []\n[workspace.package]\nversion = "0.1.2"\n'


@pytest.mark.parametrize("cargo,old,new", [
    (_package, "9.8.7", "9.8.8"),
    (_workspace + _package, "9.8.7", "9.8.8"),
    (_workspace, "0.1.2", "0.1.3"),
    (_workspace + _inherited, "0.1.2", "0.1.3"),
    (_inherited + _workspace, "0.1.2", "0.1.3"),
])
def test_cargo_bump_changes_only_authoritative_version(tmp_path, cargo, old, new):
    manifest = tmp_path/"Cargo.toml"
    manifest.write_text(cargo)
    cfg = relmod.get_crate_config(tmp_path)
    assert cfg.version == old
    assert relmod._cargo_bump(cfg) == new
    assert manifest.read_text() == cargo.replace(f'version = "{old}"', f'version = "{new}"')


def test_maturin_workspace_bump_synchronizes_copies(tmp_path, monkeypatch):
    (tmp_path/"pyproject.toml").write_text('''[build-system]
build-backend = "maturin"
[project]
name = "demo"
dynamic = ["version"]
[tool.nbdev]
[tool.fastship]
branch = "main"
version-files = ["package.json"]
''')
    manifest, copy = tmp_path/"Cargo.toml", tmp_path/"package.json"
    cargo, npm = _workspace + _inherited, '{"version": "0.1.2"}\n'
    manifest.write_text(cargo)
    copy.write_text(npm)
    monkeypatch.chdir(tmp_path)
    relmod.ship_bump()
    assert manifest.read_text() == cargo.replace("0.1.2", "0.1.3")
    assert copy.read_text() == npm.replace("0.1.2", "0.1.3")
