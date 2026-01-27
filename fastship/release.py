"""Local-first release helpers for non-nbdev Python projects.

`fastship` is inspired by the tight, simple workflow in `nbdev.release`:
bump a plain `__version__`, upload with `python -m build` + `twine`,
and create GitHub releases directly via `ghapi` (no GitHub Actions required).
"""

from __future__ import annotations

__all__ = ["GH_HOST", "DEFAULT_LABEL_GROUPS", "ShipConfig", "get_config", "bump_version", "Release", "ship_bump",
    "ship_bump_cli", "ship_pypi", "ship_pypi_cli", "ship_release_gh", "ship_release_gh_cli", "ship_new", "ship_new_cli",
    "ship_pr", "ship_pr_cli"]

import os, re, sys, shutil, subprocess, ast, importlib.resources
from dataclasses import dataclass

try: import tomllib
except ImportError: import tomli as tomllib  # pragma: no cover
from packaging.version import Version

from fastcore.all import *  # Path, nested_idx, ifnone, parallel, run, repo_details, call_parse, ...
from fastgit import Git
from ghapi.core import *    # GhApi, HTTP404NotFoundError, ...

GH_HOST = "https://api.github.com"

DEFAULT_LABEL_GROUPS = dict(breaking="Breaking Changes", enhancement="New Features", bug="Bugs Squashed")

_pyproj = "pyproject.toml"
_init = "__init__.py"

_re_version_any = re.compile(r"^__version__\s*=.*$", re.MULTILINE)
_re_version_val = re.compile(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Project discovery + config
# ---------------------------------------------------------------------------

def _find_pyproject(start: Path | None = None, fname: str = _pyproj) -> Path:
    "Search `start` and parent directories for a `pyproject.toml`."
    cfg_path = Path(start or Path().absolute())
    while cfg_path != cfg_path.parent and not (cfg_path / fname).exists(): cfg_path = cfg_path.parent
    p = cfg_path / fname
    if not p.exists(): raise FileNotFoundError(f"Could not find {fname} (searched parents from {Path().absolute()})")
    return p


def _load_toml(p: Path) -> dict: return tomllib.loads(p.read_text(encoding="utf-8"))


def _norm_mod(name: str) -> str:
    "Normalize a project name to a likely Python import/package name."
    name = name.strip().replace("-", "_")
    return re.sub(r"[^0-9a-zA-Z_]+", "_", name)


def _find_pkg(root: Path, data: dict) -> str:
    "Find the package directory from [project].name or explicit [tool.fastship].package."
    ship = nested_idx(data, "tool", "fastship") or {}
    pkg = ship.get("package")
    if pkg: return pkg

    proj = data.get("project") or {}
    nm = proj.get("name")
    if nm:
        cand = _norm_mod(nm)
        if (root / cand / _init).exists() or (root / "src" / cand / _init).exists(): return cand

    # fallback: scan for any package folder (handles non-standard layouts)
    for base in (root / "src", root):
        if not base.exists(): continue
        cands = [p for p in base.iterdir() if p.is_dir() and (p / _init).exists() and not p.name.startswith(".")]
        if cands:
            if nm:
                cand = _norm_mod(nm)
                for p in cands:
                    if p.name == cand: return cand
            return cands[0].name

    raise FileNotFoundError(
        f'Could not find package directory. Ensure [project].name in pyproject.toml '
        f'matches your package folder (e.g., "my-project" -> my_project/).')


def _pkg_path(root: Path, pkg: str) -> Path:
    "Find the directory containing `pkg/__init__.py` (supports `src/` layout)."
    if (root / pkg / _init).exists(): return root / pkg
    if (root / "src" / pkg / _init).exists(): return root / "src" / pkg

    # last-resort scan
    for base in (root / "src", root):
        if not base.exists(): continue
        for p in base.rglob(_init):
            if p.parent.name == pkg: return p.parent

    raise FileNotFoundError(f"Could not find {pkg}/__init__.py under {root}")


def _load_release_yml(root: Path) -> dict | None:
    "Load label groups from .github/release.yml if it exists."
    for name in ("release.yml", "release.yaml"):
        p = root / ".github" / name
        if p.exists():
            data = _load_toml_or_yaml(p)
            categories = nested_idx(data, "changelog", "categories") or []
            groups = {}
            for cat in categories:
                title = cat.get("title")
                if not title: continue
                for label in cat.get("labels", []):
                    if label != "*": groups[label] = title
            return groups if groups else None
    return None


def _load_toml_or_yaml(p: Path) -> dict:
    "Load a TOML or YAML file."
    txt = p.read_text(encoding="utf-8")
    if p.suffix in (".yml", ".yaml"):
        import yaml
        return yaml.safe_load(txt) or {}
    return tomllib.loads(txt)


def _git_branch(default: str = "main") -> str:
    try: return run("git branch --show-current").strip() or default
    except Exception: return default


def _git_owner_repo() -> tuple[str | None, str | None]:
    try: return repo_details(run("git config --get remote.origin.url").strip())
    except Exception: return None, None


@dataclass
class ShipConfig:
    root: Path
    pyproject: Path
    data: dict
    pkg: str
    pkg_path: Path
    init_file: Path
    changelog_file: Path
    branch: str
    label_groups: dict

    @property
    def version(self) -> str: return _read_version(self.init_file)


def get_config(start: str | Path | None = None) -> ShipConfig:
    "Load fastship config from `pyproject.toml`."
    pyproj = _find_pyproject(start)
    root = pyproj.parent
    data = _load_toml(pyproj)

    pkg = _find_pkg(root, data)
    pkg_path = _pkg_path(root, pkg)
    init_file = pkg_path / _init

    ship = nested_idx(data, "tool", "fastship") or {}
    changelog_file = root / ship.get("changelog_file", "CHANGELOG.md")
    branch = ship.get("branch") or os.getenv("FASTSHIP_BRANCH") or _git_branch()
    label_groups = _load_release_yml(root) or ship.get("label_groups") or DEFAULT_LABEL_GROUPS

    return ShipConfig(root=root, pyproject=pyproj, data=data, pkg=pkg, pkg_path=pkg_path,
                      init_file=init_file, changelog_file=changelog_file, branch=branch, label_groups=label_groups)


# ---------------------------------------------------------------------------
# Version read/write + bump
# ---------------------------------------------------------------------------

def _read_version(init_file: Path) -> str:
    if not init_file.exists(): raise FileNotFoundError(f"Missing {init_file}")
    m = _re_version_val.search(init_file.read_text(encoding="utf-8"))
    if not m: raise ValueError(f'Could not find __version__ = "..." in {init_file}')
    return m.group(1)


def _write_version(init_file: Path, version: str):
    """Write `__version__ = "x.y.z"` to `init_file`.

    We keep this *import-safe* for builds that use setuptools' dynamic
    `version = {attr = "pkg.__version__"}` by ensuring:

    - `__version__` is a *literal string* (so setuptools can read it from AST)
    - it appears near the top of the file (after shebang/encoding/docstring)

    This lets you keep normal imports in `__init__.py` without forcing setuptools
    to import your package at build time.
    """
    init_file.parent.mkdir(parents=True, exist_ok=True)
    if not init_file.exists(): init_file.write_text("", encoding="utf-8")

    raw = init_file.read_text(encoding="utf-8")
    lines = raw.splitlines()

    # Remove any existing __version__ assignment line(s)
    kept = [ln for ln in lines if not _re_version_any.match(ln)]

    # Figure out insertion point: after shebang/encoding and module docstring (if any)
    insert_at = 0
    if kept and kept[0].startswith("#!"): insert_at = 1
    # encoding cookie can be on line 1 or 2
    for i in range(insert_at, min(insert_at + 2, len(kept))):
        if re.match(r"^#.*coding[:=]\s*[-\w.]+", kept[i]): insert_at = i + 1

    # Detect module docstring end line using AST (best-effort)
    try:
        mod = ast.parse("\n".join(kept) + "\n")
        first = mod.body[0] if mod.body else None
        if first and isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
            if isinstance(first.value.value, str):
                end = getattr(first, "end_lineno", None)
                if end: insert_at = max(insert_at, end)
    except SyntaxError: pass

    ver_line = f'__version__ = "{version}"'
    out = kept[:insert_at] + [ver_line, ""] + kept[insert_at:]
    init_file.write_text("\n".join(out) + "\n", encoding="utf-8")


def bump_version(version: str, part: int = 2, unbump: bool = False) -> str:
    "Bump one part of a version (0=major, 1=minor, 2=patch) and reset later parts to 0."
    if part not in (0, 1, 2): raise ValueError("part must be 0, 1, or 2")
    v = Version(version)
    rel = list(v.release)
    while len(rel) < 3: rel.append(0)
    rel = rel[:3]
    rel[part] = max(0, rel[part] + (-1 if unbump else 1))
    for i in range(part + 1, 3): rel[i] = 0
    return ".".join(map(str, rel))


# ---------------------------------------------------------------------------
# GitHub release notes (changelog from issues) + release creation
# ---------------------------------------------------------------------------

def _issue_txt(issue):
    res = f"- {issue.title.strip()} ([#{issue.number}]({issue.html_url}))"
    if hasattr(issue, "pull_request"): res += f", thanks to [@{issue.user.login}]({issue.user.html_url})"
    return res + "\n"


def _issues_txt(iss, label):
    if not iss: return ""
    res = f"### {label}\n\n"
    return res + "\n".join(map(_issue_txt, iss))


class Release:
    def __init__(self, owner=None, repo=None, token=None, cfg: ShipConfig | None = None, **groups):
        "Create CHANGELOG.md from closed GitHub issues and publish GitHub releases."
        self.cfg = cfg or get_config()
        self.changefile = self.cfg.changelog_file

        if not groups: groups = dict(self.cfg.label_groups) if self.cfg.label_groups else DEFAULT_LABEL_GROUPS

        os.chdir(self.cfg.root)

        # Repo inference: allow override, else use git origin
        g_owner, g_repo = _git_owner_repo()
        owner, repo = owner or g_owner, repo or g_repo
        if not owner or not repo:
            raise Exception("Could not infer GitHub owner/repo. Pass --repo OWNER/REPO or set a git remote `origin`.")

        # Token discovery (mirrors nbdev/fastrelease conventions)
        token = ifnone(token, os.getenv("FASTSHIP_TOKEN", None))
        if not token and (self.cfg.root / "token").exists(): token = (self.cfg.root / "token").read_text().strip()
        token = ifnone(token, os.getenv("GITHUB_TOKEN", None))
        if not token: raise Exception("Failed to find token (FASTSHIP_TOKEN, GITHUB_TOKEN, or a ./token file)")

        self.gh = GhApi(owner, repo, token)
        self.groups = groups

    def _issues(self, label):
        return self.gh.issues.list_for_repo(state="closed", sort="created", filter="all", since=self.commit_date, labels=label)

    def changelog(self, debug: bool = False):
        """Create or update CHANGELOG.md from closed and labeled GitHub issues.

        Issues are pulled since the latest GitHub release's `published_at`.
        If no releases exist, all matching issues are included.
        """
        if not self.changefile.exists():
            self.changefile.write_text("# Release notes\n\n<!-- do not remove -->\n", encoding="utf-8")
        marker = "<!-- do not remove -->\n"

        try:
            lr = self.gh.repos.get_latest_release()
            self.commit_date = lr.published_at
        except HTTP404NotFoundError: lr, self.commit_date = None, "2000-01-01T00:00:00Z"

        if lr and (Version(self.cfg.version) <= Version(lr.tag_name)):
            print(f"Error: Version bump required: expected: >{lr.tag_name}, got: {self.cfg.version}.")
            raise SystemExit(1)

        res = f"\n## {self.cfg.version}\n\n"
        issues = parallel(self._issues, self.groups.keys(), progress=False, threadpool=True)
        res += "".join(_issues_txt(*o) for o in zip(issues, self.groups.values()))

        if debug: return res

        txt = self.changefile.read_text(encoding="utf-8")
        txt = txt.replace(marker, marker + res + "\n")
        shutil.copy(self.changefile, self.changefile.with_suffix(".bak"))
        self.changefile.write_text(txt, encoding="utf-8")
        run(f"git add {self.changefile}")

    def latest_notes(self) -> str:
        "Latest CHANGELOG entry (the most recent `## <version>` section)."
        if not self.changefile.exists(): return ""
        its = re.split(r"^## ", self.changefile.read_text(encoding="utf-8"), flags=re.MULTILINE)
        if len(its) <= 1: return ""
        return "\n".join(its[1].splitlines()[1:]).strip()

    def release(self):
        "Tag and create a release in GitHub for the current version."
        ver = self.cfg.version
        notes = self.latest_notes()
        self.gh.create_release(ver, branch=self.cfg.branch, body=notes)
        return ver


# ---------------------------------------------------------------------------
# CLI entrypoints
# ---------------------------------------------------------------------------

def ship_bump(
    part: int = 2,  # Part of version to bump (0=major, 1=minor, 2=patch)
    unbump: bool = False,  # Reduce version instead of increasing it
):
    "Increment `__version__` in your package `__init__.py` by one."
    cfg = get_config()
    old = cfg.version
    print(f"Old version: {old}")
    new = bump_version(old, part=part, unbump=unbump)
    _write_version(cfg.init_file, new)
    print(f"New version: {new}")
    return new

@call_parse
@delegates(ship_bump)
def ship_bump_cli(**kwargs): ship_bump(**kwargs)


def _clean_dist(root: Path):
    for d in ("dist", "build"):
        p = root / d
        if p.exists(): shutil.rmtree(p)
    for p in root.glob("*.egg-info"):
        if p.is_dir(): shutil.rmtree(p)


def ship_pypi(
    repository: str = "pypi",  # Repository in ~/.pypirc (e.g. "pypi" or "testpypi")
    quiet: bool = False,  # Reduce output verbosity
):
    "Build and upload the package to PyPI (uses `python -m build` + `twine upload`)."
    cfg = get_config()
    os.chdir(cfg.root)
    q = " --quiet" if quiet else ""
    p = " --disable-progress-bar" if quiet else ""
    _clean_dist(cfg.root)
    run(f"{sys.executable} -m build{q}")
    run(f"twine upload --repository {repository}{p} dist/*")

@call_parse
@delegates(ship_pypi)
def ship_pypi_cli(**kwargs): ship_pypi(**kwargs)


def ship_release_gh(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,   # Override repo ("OWNER/REPO" or just "REPO" if origin remote is set)
):
    "Create/update CHANGELOG.md, let you edit it, then commit/push and create a GitHub release."
    owner = None
    if repo and "/" in repo: owner, repo = repo.split("/", 1)

    rel = Release(owner=owner, repo=repo, token=token)
    rel.changelog()
    subprocess.run([os.environ.get("EDITOR", "nano"), rel.changefile])

    if not input("Make release now? (y/n) ").lower().startswith("y"): sys.exit(1)

    run("git commit -am release")
    run("git push")
    ver = rel.release()
    print(f"Released {ver}")
    return ver

@call_parse
@delegates(ship_release_gh)
def ship_release_gh_cli(**kwargs): ship_release_gh(**kwargs)

# ---------------------------------------------------------------------------
# Project scaffolding
# ---------------------------------------------------------------------------

def _slugify_pkg(name:str)->str:
    "Best-effort convert a project name to a valid Python package name."
    pkg = name.strip().replace("-", "_").replace(" ", "_")
    pkg = re.sub(r"[^0-9A-Za-z_]", "_", pkg)
    pkg = re.sub(r"_+", "_", pkg).strip("_")
    if not pkg: pkg = "pkg"
    if re.match(r"^\d", pkg): pkg = "pkg_" + pkg
    return pkg

def _write(p:Path, s:str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def _template_pyproject(proj_name:str, pkg_name:str, desc:str)->str:
    return f"""[build-system]
requires = [\"setuptools>=68\", \"wheel\"]
build-backend = \"setuptools.build_meta\"

[project]
name = \"{proj_name}\"
dynamic = [\"version\"]
description = \"{desc}\"
readme = \"README.md\"
requires-python = \">=3.10\"
license = {{ text = \"Apache-2.0\" }}
authors = [{{ name = \"{proj_name} contributors\" }}]
classifiers = [
  \"Programming Language :: Python :: 3\",
  \"Programming Language :: Python :: 3 :: Only\",
]

dependencies = []

[project.optional-dependencies]
dev = [
  \"fastship\",
  \"build\",
  \"twine\",
]

[tool.setuptools.dynamic]
version = {{ attr = \"{pkg_name}.__version__\" }}

[tool.fastship]
branch = \"main\"
"""

def _template_readme(proj_name:str, pkg_name:str)->str:
    return f"""# {proj_name}

A modern Python package scaffolded by **fastship**.

## Development

Create a virtualenv, then install dev tools:

```bash
pip install -e .[dev]
```

## Versioning

Version lives in `{pkg_name}/__init__.py` as `__version__`.
Bump it with:

```bash
ship_bump --part 2   # patch
ship_bump --part 1   # minor
ship_bump --part 0   # major
```

## Release

1) Ensure your GitHub issues are labeled (`bug`, `enhancement`, `breaking`).
2) Run:

```bash
ship_release_gh
ship_pypi
```
"""

def _template_gitignore()->str:
    return """__pycache__/
*.py[cod]
*.so
*.egg-info/
dist/
build/
.venv/
venv/
.env
.DS_Store
.ipynb_checkpoints/
"""

def _template_manifest()->str:
    return """include README.md
include LICENSE
include CHANGELOG.md
"""

def _read_license():
    "Read the Apache 2.0 license from the package."
    return importlib.resources.files("fastship").joinpath("LICENSE").read_text(encoding="utf-8")

def ship_new(
    name: str,              # Project name (PyPI name), e.g. "my-project"
    package: str = None,    # Python package import name, e.g. "my_project" (defaults from `name`)
    description: str = "A Python package",  # Short project description
    path: str = ".",        # Directory to create the project folder in
    force: bool = False,    # Overwrite if the folder already exists
):
    "Create a modern setuptools project wired for fastship."
    pkg = package or _slugify_pkg(name)
    root = Path(path) / name
    if root.exists():
        if not force: raise FileExistsError(f"{root} already exists (use force=True to overwrite)")
        shutil.rmtree(root)

    _write(root/"pyproject.toml", _template_pyproject(name, pkg, description))
    _write(root/"README.md", _template_readme(name, pkg))
    _write(root/"CHANGELOG.md", "<!-- do not remove -->\n\n")
    _write(root/"LICENSE", _read_license())
    _write(root/"MANIFEST.in", _template_manifest())
    _write(root/".gitignore", _template_gitignore())
    _write(root/pkg/"__init__.py", '__version__ = "0.1.0"\n')

    print(f"Created {root}")
    print(f"Next:\n  cd {root}")
    print("  pip install -e .[dev]")
    return root

@call_parse
@delegates(ship_new)
def ship_new_cli(
    name: str,  # Project name (PyPI name), e.g. "my-project"
    **kwargs
):
    "Create a modern setuptools project wired for fastship."
    ship_new(name, **kwargs)


# ---------------------------------------------------------------------------
# Quick PR workflow
# ---------------------------------------------------------------------------

def ship_pr(
    title: str,             # PR title (also used for commit message if needed)
    branch: str = None,     # Branch name (auto-generated from title if not provided)
    label: str = "enhancement",  # GitHub label for the PR
    body: str = "",         # PR body text, or path to file containing body
    token: str = None,      # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,       # Override repo ("OWNER/REPO")
):
    "Create a PR from uncommitted/unpushed work, merge it, and clean up."
    g = Git(".")
    if not g.exists: raise SystemExit("Not a git repository")

    try: default = g.remote('show', 'origin', split="\n", mute_errors=True).split("HEAD branch:")[1].split()[0]
    except Exception: default = "main"

    current = g.branch(show_current=True).strip()
    if current != default: raise SystemExit(f"Must be on {default} branch (currently on {current})")

    g.fetch('origin')
    try: behind = bool(g.log(f'HEAD..origin/{default}', oneline=True, mute_errors=True).strip())
    except Exception: behind = False
    if behind: raise SystemExit(f"Local {default} is behind origin. Run: git pull")

    try: has_commits = bool(g.log(f'origin/{default}..HEAD', oneline=True, mute_errors=True).strip())
    except Exception: has_commits = False
    has_changes = bool(g.status(porcelain=True))
    if not has_commits and not has_changes:
        raise SystemExit("Nothing to PR: no unpushed commits and no uncommitted changes")

    slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')[:50]
    if len(slug) == 50: slug = slug.rsplit('-', 1)[0]
    pr_branch = branch or f"pr/{slug}"
    g.switch('-c', pr_branch)

    try:
        if has_changes: g.commit('-am', title)
        g.push('-u', 'origin', pr_branch)

        owner, repo_name = repo.split("/", 1) if repo and "/" in repo else _git_owner_repo()
        if not owner or not repo_name: raise SystemExit("Could not determine GitHub repo. Use --repo OWNER/REPO")

        token = ifnone(token, os.getenv("FASTSHIP_TOKEN"))
        if not token and Path("token").exists(): token = Path("token").read_text().strip()
        token = ifnone(token, os.getenv("GITHUB_TOKEN"))
        if not token: raise SystemExit("No GitHub token found")

        gh = GhApi(owner, repo_name, token)
        pr_body = Path(body).read_text().strip() if body and '\n' not in body and Path(body).exists() else body
        pr = gh.pulls.create(title=title, head=pr_branch, base=default, body=pr_body)
        print(f"Created PR #{pr.number}: {pr.html_url}")

        try: gh.issues.add_labels(pr.number, labels=[label])
        except Exception: pass

        gh.pulls.merge(pr.number, merge_method="squash", commit_title=title)
        print(f"Merged PR #{pr.number}")

        try: gh.git.delete_ref(f"heads/{pr_branch}")
        except Exception: pass

    finally:
        g.switch(default)

    g.fetch('origin')
    g.reset('--hard', f'origin/{default}')
    print(f"Done! {default} updated to include squashed commit.")

@call_parse
@delegates(ship_pr)
def ship_pr_cli(
    title: str,  # PR title (also used for commit message if needed)
    **kwargs
): ship_pr(title, **kwargs)
