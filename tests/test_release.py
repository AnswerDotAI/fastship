import fastship.release as relmod


def test_static_version_bump_synchronizes_copies_without_changing_init(tmp_path, monkeypatch):
    pyproject = tmp_path/"pyproject.toml"
    before = '''[project]
name = "demo"
version = "1.0.0.post1"
[tool.fastship]
branch = "main"
version-files = ["version.txt"]
'''
    pyproject.write_text(before)
    package = tmp_path/"demo"
    package.mkdir()
    init = package/"__init__.py"
    init_before = '__version__ = "9.9.9"\n'
    init.write_text(init_before)
    copy = tmp_path/"version.txt"
    copy.write_text("release: 1.0.0.post1\n")
    monkeypatch.chdir(tmp_path)
    relmod.ship_bump()
    assert pyproject.read_text() == before.replace("1.0.0.post1", "1.0.0.post2")
    assert copy.read_text() == "release: 1.0.0.post2\n"
    assert init.read_text() == init_before



def test_docs_mod_urls(tmp_path):
    from fastship.docs import docs_mod_urls
    pkg = tmp_path/"demo"
    pkg.mkdir()
    (pkg/"__init__.py").write_text("from ._core import render\n")
    (pkg/"sub.py").write_text("def helper(): pass\n")
    (pkg/"_priv.py").write_text("def hidden(): pass\n")
    assert docs_mod_urls(pkg) == ({"demo.sub": "api/sub.html.md"}, "api.html.md")

