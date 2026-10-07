from types import SimpleNamespace

import pytest
import fastship.release as relmod


def test_post_version_bumps():
    assert relmod.bump_version("0.0.2026082005.post1") == "0.0.2026082005.post2"
    assert relmod.bump_version("0.0.2026082005.post2", unbump=True) == "0.0.2026082005.post1"
    assert relmod.bump_version("0.0.2026082005.post1", part=2) == "0.0.2026082006"
    assert relmod.bump_version("9.7.1.1") == "9.7.1.2"
    assert relmod.bump_version("9.7.1.2", unbump=True) == "9.7.1.1"
    assert relmod.bump_version("9.7.1.1", part=1) == "9.8.0.0"


def test_static_version_bump_synchronizes_copies_without_changing_init(tmp_path, monkeypatch):
    pyproject = tmp_path/"pyproject.toml"
    before = '''[project]
name = "demo"
version = "1.0.post1"
[tool.nbdev]
lib_path = "demo"
[tool.fastship]
branch = "main"
version-files = ["version.txt"]
'''
    pyproject.write_text(before)
    package = tmp_path/"demo"
    package.mkdir()
    init = package/"__init__.py"
    init.write_text('__version__ = "9.9.9"\n')
    copy = tmp_path/"version.txt"
    copy.write_text("release: 1.0.post1\n")
    monkeypatch.chdir(tmp_path)
    relmod.ship_bump()
    assert pyproject.read_text() == before.replace("1.0.post1", "1.0.post2")
    assert copy.read_text() == "release: 1.0.post2\n"
    assert init.read_text() == '__version__ = "9.9.9"\n'


@pytest.mark.parametrize("invalid_copy", ["0.9\n", "1.0.post1 twice: 1.0.post1\n"])
def test_version_files_are_validated_before_writing(tmp_path, invalid_copy):
    pyproject, valid, invalid = (tmp_path/o for o in ("pyproject.toml", "valid.txt", "invalid.txt"))
    before = {pyproject: '[project]\nname = "demo"\nversion = "1.0.post1"\n', valid: "1.0.post1\n", invalid: invalid_copy}
    for p, text in before.items(): p.write_text(text)
    cfg = SimpleNamespace(pyproject=pyproject, init_file=None)
    with pytest.raises(ValueError, match="exactly one"):
        relmod._bump("1.0.post1", lambda v: relmod._write_config_version(cfg, v), [valid, invalid])
    assert {p: p.read_text() for p in before} == before
