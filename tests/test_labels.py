import tempfile
from pathlib import Path
from fastship.release import get_config, DEFAULT_LABEL_GROUPS

def _make_project(tmp, release_yml=None, pkg_name="mypkg", proj_name=None, src_layout=False):
    "Create a minimal project structure"
    root = Path(tmp)
    proj_name = proj_name or pkg_name
    if src_layout:
        (root / "src" / pkg_name).mkdir(parents=True)
        (root / "src" / pkg_name / "__init__.py").write_text('__version__ = "0.1.0"\n')
    else:
        (root / pkg_name).mkdir()
        (root / pkg_name / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (root / "pyproject.toml").write_text(f'''[project]
name = "{proj_name}"
''')
    if release_yml:
        (root / ".github").mkdir()
        (root / ".github" / "release.yml").write_text(release_yml)
    return root

def test_labels_from_release_yml():
    "Label groups should be read from .github/release.yml when present"
    release_yml = '''changelog:
  categories:
    - title: "Breaking Changes"
      labels:
        - breaking
        - breaking-change
    - title: "New Features"
      labels:
        - enhancement
        - feature
    - title: "Bug Fixes"
      labels:
        - bug
'''
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_project(tmp, release_yml=release_yml)
        cfg = get_config(root)
        # Should have label groups from release.yml, not defaults
        assert cfg.label_groups == {
            'breaking': 'Breaking Changes',
            'breaking-change': 'Breaking Changes',
            'enhancement': 'New Features',
            'feature': 'New Features',
            'bug': 'Bug Fixes',
        }

def test_labels_default_when_no_release_yml():
    "Should use DEFAULT_LABEL_GROUPS when .github/release.yml is missing"
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_project(tmp, release_yml=None)
        cfg = get_config(root)
        assert cfg.label_groups == DEFAULT_LABEL_GROUPS


def test_package_inferred_from_project_name():
    "Package name should be inferred from [project].name"
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_project(tmp, pkg_name="my_pkg", proj_name="my-pkg")
        cfg = get_config(root)
        assert cfg.pkg == "my_pkg"


def test_package_inferred_src_layout():
    "Package name should be inferred for src/ layout"
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_project(tmp, pkg_name="my_pkg", proj_name="my-pkg", src_layout=True)
        cfg = get_config(root)
        assert cfg.pkg == "my_pkg"
        assert "src" in str(cfg.pkg_path)


def test_fastship_own_config():
    "Fastship's own config should load without explicit package setting"
    from pathlib import Path
    root = Path(__file__).parent.parent
    cfg = get_config(root)
    assert cfg.pkg == "fastship"
    assert cfg.branch == "main"
    assert "bug" in cfg.label_groups  # defaults should load


def test_ship_new_creates_project():
    "ship_new should create a complete project with LICENSE from package"
    from fastship.release import ship_new, _read_license
    with tempfile.TemporaryDirectory() as tmp:
        root = ship_new("test-proj", path=tmp)
        assert (root / "LICENSE").exists()
        assert (root / "LICENSE").read_text() == _read_license()
        assert (root / "pyproject.toml").exists()
        assert (root / "test_proj" / "__init__.py").exists()
        assert "Apache License" in (root / "LICENSE").read_text()
