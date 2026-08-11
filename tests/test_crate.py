import pytest

import fastship.release as relmod

_cargo_toml = """[package]
name = "zmtpmini"
version = "0.1.2"
edition = "2024"
license = "Apache-2.0"
description = "A minimal ZMTP client"

[dependencies]
"""


def _make_crate_project(root):
    (root / "Cargo.toml").write_text(_cargo_toml, encoding="utf-8")


def test_get_crate_config(tmp_path, monkeypatch):
    _make_crate_project(tmp_path)
    monkeypatch.setenv("FASTSHIP_BRANCH", "main")
    cfg = relmod.get_crate_config(tmp_path)

    assert cfg.name == "zmtpmini"
    assert cfg.version == "0.1.2"
    assert cfg.branch == "main"
    assert cfg.manifest_path == tmp_path / "Cargo.toml"


def test_ship_bump_routes_to_cargo_toml(tmp_path, monkeypatch):
    _make_crate_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTSHIP_BRANCH", "main")
    monkeypatch.setattr(relmod, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not shell out")))

    relmod.ship_bump(part=1)

    assert 'version = "0.2.0"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")


def test_ship_crate_release_tags_then_bumps(tmp_path, monkeypatch):
    _make_crate_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTSHIP_BRANCH", "main")
    monkeypatch.setattr(relmod, "_git_has_changes", lambda: False)
    calls = []
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: calls.append(cmd))

    version = relmod._ship_tag_release("crate")

    assert version == "0.1.2"
    assert any("git tag" in c and "v0.1.2" in c for c in calls)
    assert any(c == "git push origin v0.1.2" for c in calls)
    assert calls.index("git commit -am bump") > calls.index("git push origin v0.1.2")  # bump follows the tag push
    assert 'version = "0.1.3"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")


def test_ship_crate_new_scaffold(tmp_path):
    root = relmod._create_crate_project("My Crate", description="Does things", path=tmp_path)

    assert root == tmp_path / "my-crate"
    cargo = (root / "Cargo.toml").read_text(encoding="utf-8")
    assert 'name = "my-crate"' in cargo
    assert 'description = "Does things"' in cargo
    assert (root / "src" / "lib.rs").exists()
    assert (root / "LICENSE").exists()
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "crates-io-auth-action" in ci
    assert "cargo publish" in ci
    assert not (root / "pyproject.toml").exists()  # a pure crate: Cargo.toml is the only project marker
