"A repo can carry nbdev docs over a maturin build (e.g. ptymini); releases must use the tag flow, not the nbdev flow."
import fastship.release as relmod

_pyproject = """[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "ptymini"
dynamic = ["version"]

[tool.fastship]
branch = "main"

[tool.nbdev]
"""

_cargo_toml = """[package]
name = "ptymini"
version = "0.0.2"
edition = "2024"
"""


def test_nbdev_docs_over_maturin_uses_tag_flow(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(_pyproject, encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(_cargo_toml, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert relmod._nbdev_release() is None  # [tool.nbdev] alone must not claim a maturin repo

    calls = []
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: calls.append(cmd))
    relmod.ship_bump()
    assert 'version = "0.0.3"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")
    assert "maturin develop" in calls  # the maturin bump path, not nbdev's
