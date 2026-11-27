import pytest, fastship.config as cfgmod

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
    cfg = cfgmod.get_crate_config(tmp_path)
    assert cfg.version == old
    assert cfgmod.cargo_bump(cfg) == new
    assert manifest.read_text() == cargo.replace(f'version = "{old}"', f'version = "{new}"')
