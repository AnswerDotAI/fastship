"""Local-first release helpers for non-nbdev Python projects.

`fastship` is inspired by the tight, simple workflow in `nbdev.release`:
bump a plain `__version__`, upload with `python -m build` + `twine`,
and create GitHub releases directly via `ghapi` (no GitHub Actions required).
"""


__all__ = ["GH_HOST", "DEFAULT_LABEL_GROUPS", "ShipConfig", "RustConfig", "get_config", "get_rs_config", "bump_version", "Release",
    "ship_bump", "ship_pypi", "ship_changelog", "ship_release_gh", "ship_release", "ship_new", "ship_pr",
    "ship_rs_new", "ship_rs_new_cli", "ship_rs_init", "ship_rs_init_cli", "ship_rs_prep", "ship_rs_prep_cli",
    "ship_rs_build", "ship_rs_build_cli", "ship_rs_test", "ship_rs_test_cli", "ship_rs_bump", "ship_rs_bump_cli",
    "ship_rs_release", "ship_rs_release_cli"]

import os, re, sys, shutil, subprocess, ast, importlib.resources, shlex, stat, tempfile
from dataclasses import dataclass

try: import tomllib
except ImportError: import tomli as tomllib  # pragma: no cover
from packaging.version import Version

from fastcore.all import *  # Path, nested_idx, ifnone, parallel, run, repo_details, call_parse, ...
from fastgit import Git
from ghapi.core import *    # GhApi, HTTP404NotFoundError, ...

GH_HOST = "https://api.github.com"
CHANGELOG_MARKER = "<!-- do not remove -->\n"

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


def _parse_repo(repo: str = None) -> tuple[str | None, str | None]:
    "Parse 'OWNER/REPO' string, falling back to git origin."
    if repo and "/" in repo: return repo.split("/", 1)
    g_owner, g_repo = _git_owner_repo()
    return g_owner, repo or g_repo


def _git_has_changes() -> bool:
    "Return `True` if the current git worktree has staged or unstaged changes."
    return bool(run("git status --porcelain").strip())


def _get_token(root: Path = None) -> str | None:
    "Find GitHub token from env vars or token file."
    token = os.getenv("FASTSHIP_TOKEN")
    if not token and root and (root / "token").exists(): token = (root / "token").read_text().strip()
    if not token and Path("token").exists(): token = Path("token").read_text().strip()
    return token or os.getenv("GITHUB_TOKEN")


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


@dataclass
class RustConfig:
    root: Path
    pyproject: Path
    data: dict
    manifest_path: Path
    bins: list[str]
    data_scripts: Path | None
    branch: str

    @property
    def version(self) -> str: return _cargo_version(self.manifest_path)


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


def get_rs_config(start: str | Path | None = None) -> RustConfig:
    "Load fastship config for a maturin/PyO3 project."
    pyproj = _find_pyproject(start)
    root = pyproj.parent
    data = _load_toml(pyproj)
    proj = data.get("project") or {}
    if proj.get("version") is not None: raise ValueError(f'{pyproj} must use Cargo.toml for ship-rs versions; remove [project].version')
    dyn = proj.get("dynamic") or []
    if not isinstance(dyn, list) or "version" not in dyn: raise ValueError(f'{pyproj} must set [project].dynamic = ["version"] for ship-rs commands')
    ship = nested_idx(data, "tool", "fastship") or {}
    rs = ship.get("rs") or {}
    maturin = nested_idx(data, "tool", "maturin") or {}
    bins = list(rs.get("bins") or [])
    data_scripts = rs.get("data_scripts")
    if not data_scripts and maturin.get("data"): data_scripts = str(Path(maturin["data"]) / "scripts")
    data_scripts = root / data_scripts if data_scripts else None
    branch = ship.get("branch") or os.getenv("FASTSHIP_BRANCH") or _git_branch()
    manifest_path = root / rs.get("manifest_path", "Cargo.toml")
    return RustConfig(root=root, pyproject=pyproj, data=data, manifest_path=manifest_path, bins=bins, data_scripts=data_scripts, branch=branch)


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
# Rust/PyO3 helpers
# ---------------------------------------------------------------------------

def _q(s) -> str: return shlex.quote(str(s))


def _replace_toml_section_key(p:Path, section:str, key:str, val:str):
    "Replace `key = ...` inside a top-level TOML section."
    lines = p.read_text(encoding="utf-8").splitlines()
    in_sec = False
    pat = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*).*$")
    for i, ln in enumerate(lines):
        if re.match(r"^\s*\[[^\[].*\]\s*$", ln):
            in_sec = ln.strip() == f"[{section}]"
            continue
        if in_sec and pat.match(ln):
            lines[i] = f'{key} = "{val}"'
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    raise ValueError(f"Could not find {key!r} in [{section}] of {p}")


def _bin_suffix(target:str = None) -> str:
    if target and "windows" in target: return ".exe"
    return ".exe" if os.name == "nt" else ""


def _cargo_profile_dir(root:Path, profile:str, target:str = None) -> Path:
    return root / "target" / target / profile if target else root / "target" / profile


def _chmod_exec(p:Path):
    try: p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except PermissionError: pass


def _copy_rs_bins(cfg:RustConfig, release:bool = False, target:str = None):
    "Build configured Rust bins and copy them into maturin's .data/scripts folder."
    if not cfg.bins: return []
    if not cfg.data_scripts: raise ValueError("Set [tool.fastship.rs].data_scripts or [tool.maturin].data before copying Rust bins")
    os.chdir(cfg.root)
    profile = "release" if release else "debug"
    flags = " --release" if release else ""
    target_arg = f" --target {_q(target)}" if target else ""
    run(f"cargo build{flags}{target_arg} --bins")
    cfg.data_scripts.mkdir(parents=True, exist_ok=True)
    suffix = _bin_suffix(target)
    res = []
    for b in cfg.bins:
        for old in (cfg.data_scripts / b, cfg.data_scripts / f"{b}.exe"):
            if old.exists(): old.unlink()
        src = _cargo_profile_dir(cfg.root, profile, target) / f"{b}{suffix}"
        if not src.exists(): raise FileNotFoundError(f"Built binary not found: {src}")
        dst = cfg.data_scripts / src.name
        shutil.copy2(src, dst)
        _chmod_exec(dst)
        res.append(dst)
    return res


def _maturin_cmd(command:str, release:bool = False, target:str = None, outdir:str = None, args:str = "") -> str:
    parts = ["maturin", command]
    if release: parts.append("--release")
    if target: parts += ["--target", _q(target)]
    if outdir: parts += ["-o", _q(outdir)]
    if args: parts.append(args)
    return " ".join(parts)


def _is_maturin_project(data:dict) -> bool:
    build_backend = nested_idx(data, "build-system", "build-backend") or ""
    return "maturin" in build_backend or bool(nested_idx(data, "tool", "maturin"))


def _cargo_bins(manifest:Path) -> list[str]:
    "Read explicit `[[bin]]` names from Cargo.toml."
    data = _load_toml(manifest)
    return [o["name"] for o in data.get("bin", []) if o.get("name")]


def _cargo_version(manifest:Path) -> str:
    "Read `[package].version` from Cargo.toml."
    ver = (_load_toml(manifest).get("package") or {}).get("version")
    if not ver: raise ValueError(f"Could not find [package].version in {manifest}")
    return ver


def _fmt_toml_val(v):
    if isinstance(v, list): return "[" + ", ".join(f'"{o}"' for o in v) + "]"
    return f'"{v}"'


def _toml_section_bounds(lines:list[str], section:str):
    hdr = f"[{section}]"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == hdr), None)
    if start is None: return None, None
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[.*\]\s*$", lines[i])), len(lines))
    return start, end


def _ensure_toml_section(p:Path, section:str, items:dict, replace:bool = False):
    "Ensure a TOML section contains key/value pairs, preserving existing keys unless `replace`."
    lines = p.read_text(encoding="utf-8").splitlines()
    start, end = _toml_section_bounds(lines, section)
    if start is None:
        if lines and lines[-1].strip(): lines.append("")
        lines += [f"[{section}]"] + [f"{k} = {_fmt_toml_val(v)}" for k, v in items.items()]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    existing = {}
    for i in range(start + 1, end):
        m = re.match(r"^(\s*([A-Za-z0-9_-]+)\s*=\s*).*$", lines[i])
        if m: existing[m.group(2)] = i
    inserts = []
    for k, v in items.items():
        val = f"{k} = {_fmt_toml_val(v)}"
        if k in existing:
            if replace: lines[existing[k]] = val
        else: inserts.append(val)
    if inserts: lines[end:end] = inserts
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_project_dynamic_version(p:Path):
    "Make `[project]` use maturin's Cargo.toml-derived version."
    data = _load_toml(p)
    proj = data.get("project") or {}
    dyn = proj.get("dynamic") or []
    if not isinstance(dyn, list): raise ValueError(f"{p} [project].dynamic must be a list")
    if "version" not in dyn: dyn.append("version")

    lines = p.read_text(encoding="utf-8").splitlines()
    start, end = _toml_section_bounds(lines, "project")
    if start is None: raise ValueError(f"Could not find [project] in {p}")

    for i in range(end - 1, start, -1):
        if re.match(r"^\s*version\s*=", lines[i]):
            del lines[i]
            end -= 1

    dynamic_range = None
    name_idx = None
    for i in range(start + 1, end):
        if re.match(r"^\s*dynamic\s*=", lines[i]):
            j = i + 1
            if "[" in lines[i] and "]" not in lines[i]:
                while j < end and "]" not in lines[j]: j += 1
                j = min(j + 1, end)
            dynamic_range = (i, j)
        if re.match(r"^\s*name\s*=", lines[i]): name_idx = i
    val = f"dynamic = {_fmt_toml_val(dyn)}"
    if dynamic_range is not None: lines[dynamic_range[0]:dynamic_range[1]] = [val]
    else: lines.insert((name_idx or start) + 1, val)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rs_module_name(data:dict) -> str | None:
    mod = nested_idx(data, "tool", "maturin", "module-name") or nested_idx(data, "project", "name")
    return _norm_mod(mod.rsplit(".", 1)[-1]) if mod else None


def _warn(msg:str): print(f"ship-rs-init: warning: {msg}", file=sys.stderr)


def _ensure_rs_runtime_version(root:Path, data:dict):
    "Expose Cargo.toml's version as `__version__` in the PyO3 module."
    mod = _rs_module_name(data)
    if not mod:
        _warn("could not infer PyO3 module name; skipped Rust __version__ export")
        return
    pat = re.compile(rf"^\s*fn\s+{re.escape(mod)}\s*\(\s*(\w+)\s*:", re.MULTILINE)
    found = False
    for p in (root / "src").rglob("*.rs"):
        txt = p.read_text(encoding="utf-8")
        m = pat.search(txt)
        if not m: continue
        found = True
        if "__version__" in txt[m.start():]: return
        ok = txt.find("\n    Ok(())", m.end())
        if ok < 0: continue
        txt = txt[:ok] + f'\n    {m.group(1)}.add("__version__", env!("CARGO_PKG_VERSION"))?;' + txt[ok:]
        p.write_text(txt, encoding="utf-8")
        return
    if found: _warn(f"found PyO3 module `{mod}` but could not find a simple `Ok(())`; skipped Rust __version__ export")
    else: _warn(f"could not find PyO3 module function `fn {mod}(...)`; skipped Rust __version__ export")


def _ensure_py_runtime_version(root:Path, data:dict):
    "Re-export extension-module `__version__` from a Python package wrapper."
    mod = nested_idx(data, "tool", "maturin", "module-name")
    if not mod or "." not in mod: return
    pkg, ext = mod.rsplit(".", 1)
    init = root / (nested_idx(data, "tool", "maturin", "python-source") or "python") / pkg / _init
    if not init.exists(): return
    txt = init.read_text(encoding="utf-8")
    if not _re_version_any.search(txt): return

    lines = [ln for ln in txt.splitlines() if not _re_version_any.match(ln)]
    import_pat = re.compile(rf"^from\s+({re.escape(pkg + '.' + ext)}|\.{re.escape(ext)})\s+import\s+(.+)$")
    for i, ln in enumerate(lines):
        m = import_pat.match(ln)
        if not m: continue
        names = [o.strip() for o in m.group(2).split(",")]
        if "__version__" not in names: names.insert(0, "__version__")
        lines[i] = f"from {m.group(1)} import {', '.join(names)}"
        init.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    _warn(f"found literal __version__ in {init} but could not find a simple import from `{pkg}.{ext}`; left Python wrapper unchanged")


def _init_rs_config(root:Path, branch:str = None, force:bool = False):
    pyproj = _find_pyproject(root)
    data = _load_toml(pyproj)
    if not _is_maturin_project(data): raise SystemExit(f"{pyproj} does not look like a maturin project")
    manifest = pyproj.parent / "Cargo.toml"
    if not manifest.exists(): raise SystemExit(f"Missing {manifest}")
    _ensure_project_dynamic_version(pyproj)
    _ensure_toml_section(pyproj, "project.optional-dependencies", dict(dev=["fastship>=0.0.11", "maturin>=1.0,<2.0", "pytest"]))
    _ensure_rs_runtime_version(root, data)
    _ensure_py_runtime_version(root, data)
    maturin = nested_idx(data, "tool", "maturin") or {}
    data_scripts = str(Path(maturin["data"]) / "scripts") if maturin.get("data") else None
    branch = branch or nested_idx(data, "tool", "fastship", "branch") or _git_branch()
    _ensure_toml_section(pyproj, "tool.fastship", dict(branch=branch), replace=force)
    bins = _cargo_bins(manifest)
    items = dict(bins=bins) if bins else {}
    if data_scripts: items["data_scripts"] = data_scripts
    _ensure_toml_section(pyproj, "tool.fastship.rs", items, replace=force)
    return pyproj


def _update_rs_ci(root:Path):
    wf = root / ".github" / "workflows" / "ci.yml"
    if not wf.exists(): return None
    txt = wf.read_text(encoding="utf-8")
    txt = txt.replace("- run: tools/build.sh release", "- run: ship-rs-prep --release")
    txt = txt.replace("- run: tools/build.sh", "- run: ship-rs-prep")
    if "ship-rs-prep" in txt and "pip install -e '.[dev]'" not in txt:
        lines, inserted = txt.splitlines(), False
        out = []
        for ln in lines:
            if not inserted and re.search(r"- run: ship-rs-prep(\s|$)", ln):
                out.append(f"{ln.split('- run:', 1)[0]}- run: pip install -e '.[dev]'")
                inserted = True
            out.append(ln)
        txt = "\n".join(out) + ("\n" if txt.endswith("\n") else "")
    wf.write_text(txt, encoding="utf-8")
    return wf


# ---------------------------------------------------------------------------
# GitHub release notes (changelog from issues) + release creation
# ---------------------------------------------------------------------------

def _issue_txt(issue):
    res = f"- {issue.title.strip()} ([#{issue.number}]({issue.html_url}))"
    if hasattr(issue, "pull_request"): res += f", thanks to [@{issue.user.login}]({issue.user.html_url})"
    return res


def _issues_txt(iss, label):
    if not iss: return ""
    res = f"### {label}\n\n"
    return res + "\n".join(map(_issue_txt, iss)) + "\n"


class Release:
    def __init__(self, owner=None, repo=None, token=None, cfg: ShipConfig | None = None, **groups):
        "Create CHANGELOG.md from closed GitHub issues and publish GitHub releases."
        self.cfg = cfg or get_config()
        self.changefile = self.cfg.changelog_file

        if not groups: groups = dict(self.cfg.label_groups) if self.cfg.label_groups else DEFAULT_LABEL_GROUPS

        os.chdir(self.cfg.root)

        owner, repo = _parse_repo(repo) if not owner else (owner, repo)
        if not owner or not repo: raise Exception("Could not infer GitHub owner/repo. Pass --repo OWNER/REPO or set a git remote `origin`.")

        token = token or _get_token(self.cfg.root)
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
        if not self.changefile.exists(): self.changefile.write_text(f"# Release notes\n\n{CHANGELOG_MARKER}", encoding="utf-8")

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
        if CHANGELOG_MARKER not in txt:
            raise ValueError(
                f"{self.changefile} is missing the fastship changelog marker "
                f"{CHANGELOG_MARKER.strip()!r}. Add it near the top of the file.")
        txt = txt.replace(CHANGELOG_MARKER, CHANGELOG_MARKER + res + "\n")
        shutil.copy(self.changefile, self.changefile.with_suffix(".bak"))
        self.changefile.write_text(txt, encoding="utf-8")
        run(f"git add {self.changefile}")
        return self

    def latest_notes(self) -> str:
        "Latest CHANGELOG entry (the most recent `## <version>` section)."
        if not self.changefile.exists(): return ""
        its = re.split(r"^## ", self.changefile.read_text(encoding="utf-8"), flags=re.MULTILINE)
        if len(its) <= 1: return ""
        return "\n".join(its[1].splitlines()[1:]).strip()

    def release(self):
        "Tag and create a release in GitHub for the current version."
        self.gh.create_release(self.cfg.version, branch=self.cfg.branch, body=self.latest_notes())
        return self


# ---------------------------------------------------------------------------
# CLI entrypoints
# ---------------------------------------------------------------------------

@call_parse
def ship_bump(
    part: int = 2,  # Part of version to bump (0=major, 1=minor, 2=patch)
    unbump: bool = False,  # Reduce version instead of increasing it
):
    "Increment `__version__` in your package `__init__.py` by one."
    cfg = get_config()
    print(f"Old version: {cfg.version}")
    new = bump_version(cfg.version, part=part, unbump=unbump)
    _write_version(cfg.init_file, new)
    print(f"New version: {new}")


def _clean_dist(root: Path):
    for d in ("dist", "build"):
        p = root / d
        if p.exists(): shutil.rmtree(p)
    for p in root.glob("*.egg-info"):
        if p.is_dir(): shutil.rmtree(p)


@call_parse
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
def ship_changelog(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,   # Override repo ("OWNER/REPO")
):
    "Create/update CHANGELOG.md from closed GitHub issues (without opening editor or releasing)."
    print(f"Updated {Release(repo=repo, token=token).changelog().changefile}")


@call_parse
def ship_release_gh(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,   # Override repo ("OWNER/REPO")
    no_changelog: bool = False,  # Skip changelog generation (assumes CHANGELOG.md is ready)
):
    "Create/update CHANGELOG.md, let you edit it, then commit/push and create a GitHub release."
    rel = Release(repo=repo, token=token)
    if not no_changelog: rel.changelog()
    subprocess.run([os.environ.get("EDITOR", "nano"), rel.changefile])
    if not input("Make release now? (y/n) ").lower().startswith("y"): sys.exit(1)

    if _git_has_changes(): run("git commit -am release")
    run("git push")
    print(f"Released {rel.release().cfg.version}")


@call_parse
def ship_release(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,   # Override repo ("OWNER/REPO")
    repository: str = "pypi",  # PyPI repository in ~/.pypirc
):
    "Release to GitHub and PyPI, bump version, and push (assumes CHANGELOG.md is ready)."
    ship_release_gh(token=token, repo=repo, no_changelog=True)
    ship_pypi(repository=repository)
    ship_bump()
    run("git commit -am bump")
    run("git push")


def ship_rs_init(branch: str = None, ci: bool = False, force: bool = False):
    "Configure an existing maturin/PyO3 project for fastship Rust commands."
    root = _find_pyproject().parent
    pyproj = _init_rs_config(root, branch=branch, force=force)
    print(f"Updated {pyproj}")
    if ci:
        wf = _update_rs_ci(root)
        if wf: print(f"Updated {wf}")


@call_parse
def ship_rs_init_cli(
    branch: str = None,   # Branch for [tool.fastship] (defaults to existing/current)
    ci: bool = False,     # Update .github/workflows/ci.yml to call ship-rs-prep
    force: bool = False,  # Replace existing [tool.fastship] / [tool.fastship.rs] keys
):
    "Configure an existing maturin/PyO3 project for fastship Rust commands."
    return ship_rs_init(branch=branch, ci=ci, force=force)


def ship_rs_prep(release: bool = False, target: str = None):
    "Build configured Rust CLI bins and copy them into maturin .data/scripts."
    cfg = get_rs_config()
    copied = _copy_rs_bins(cfg, release=release, target=target)
    for p in copied: print(f"Copied {p}")


@call_parse
def ship_rs_prep_cli(
    release: bool = False,  # Build bins with `cargo build --release`
    target: str = None,     # Optional Rust target triple
):
    "Build configured Rust CLI bins and copy them into maturin .data/scripts."
    return ship_rs_prep(release=release, target=target)


def ship_rs_build(release: bool = True, target: str = None, outdir: str = "dist", args: str = ""):
    "Prepare configured Rust CLI bins, then build wheels with maturin."
    cfg = get_rs_config()
    os.chdir(cfg.root)
    _copy_rs_bins(cfg, release=release, target=target)
    run(_maturin_cmd("build", release=release, target=target, outdir=outdir, args=args))


@call_parse
def ship_rs_build_cli(
    release: bool = True,  # Build release wheels by default
    target: str = None,    # Optional Rust target triple
    outdir: str = "dist",  # Wheel output directory
    args: str = "",        # Extra arguments appended to `maturin build`
):
    "Prepare configured Rust CLI bins, then build wheels with maturin."
    return ship_rs_build(release=release, target=target, outdir=outdir, args=args)


def ship_rs_test(target: str = None, pytest_args: str = "-q"):
    "Run cargo tests, install a local wheel, then run pytest."
    cfg = get_rs_config()
    os.chdir(cfg.root)
    run("cargo test")
    _copy_rs_bins(cfg, release=False, target=target)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        run(_maturin_cmd("build", target=target, outdir=out))
        wheels = list(out.glob("*.whl"))
        if len(wheels) != 1: raise RuntimeError(f"Expected one wheel from maturin build, found {len(wheels)} in {out}")
        run(f"{sys.executable} -m pip install --force-reinstall {_q(wheels[0])}")
    run(f"pytest {pytest_args}")


@call_parse
def ship_rs_test_cli(
    target: str = None,      # Optional Rust target triple for bin prep/build
    pytest_args: str = "-q", # Arguments passed to pytest
):
    "Run cargo tests, install a local wheel, then run pytest."
    return ship_rs_test(target=target, pytest_args=pytest_args)


def ship_rs_bump(part: int = 2, unbump: bool = False):
    "Bump `[package].version` in Cargo.toml."
    cfg = get_rs_config()
    old = cfg.version
    new = bump_version(old, part=part, unbump=unbump)
    _replace_toml_section_key(cfg.manifest_path, "package", "version", new)
    print(f"{old} -> {new}")


@call_parse
def ship_rs_bump_cli(
    part: int = 2,          # Part of version to bump (0=major, 1=minor, 2=patch)
    unbump: bool = False,   # Reduce version instead of increasing it
):
    "Bump `[package].version` in Cargo.toml."
    return ship_rs_bump(part=part, unbump=unbump)


def ship_rs_release(remote: str = "origin", branch: str = None):
    "Tag `v<version>` and push branch plus tags, leaving CI to build/publish wheels."
    cfg = get_rs_config()
    os.chdir(cfg.root)
    branch = branch or cfg.branch
    run(f"git tag v{cfg.version}")
    run(f"git push {remote} {branch} --tags")
    print(f"Released v{cfg.version}")


@call_parse
def ship_rs_release_cli(
    remote: str = "origin", # Git remote to push
    branch: str = None,     # Branch to push (defaults to [tool.fastship].branch/current branch)
):
    "Tag `v<version>` and push branch plus tags, leaving CI to build/publish wheels."
    return ship_rs_release(remote=remote, branch=branch)


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

def _slugify_dist(name:str)->str:
    "Best-effort convert a project name to a PyPI/Cargo-style distribution name."
    dist = name.strip().lower().replace("_", "-").replace(" ", "-")
    dist = re.sub(r"[^0-9a-zA-Z-]", "-", dist)
    dist = re.sub(r"-+", "-", dist).strip("-")
    if not dist: dist = "pkg"
    if re.match(r"^\d", dist): dist = "pkg-" + dist
    return dist

def _write(p:Path, s:str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def _prepare_new_root(root:Path, force:bool = False):
    if root.exists():
        if not force: raise FileExistsError(f"{root} already exists (use force=True to overwrite)")
        shutil.rmtree(root)
    return root

def _template_pyproject(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
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

[project.urls]
Homepage = \"https://github.com/{gh_org}/{proj_name}\"

[tool.setuptools.dynamic]
version = {{ attr = \"{pkg_name}.__version__\" }}

[tool.setuptools.packages.find]
include = [\"{pkg_name}\"]
"""

def _template_readme(proj_name:str, pkg_name:str)->str:
    return f"""# {proj_name}

A modern Python package scaffolded by **fastship**.

## Development

```bash
pip install -e .[dev]
```

## Versioning

Version lives in `{pkg_name}/__init__.py` as `__version__`.
Bump it with:

```bash
ship-bump --part 2   # patch
ship-bump --part 1   # minor
ship-bump --part 0   # major
```

## Release

1) Ensure your GitHub issues are labeled (`bug`, `enhancement`, `breaking`).
2) Run:

```bash
ship-gh
ship-pypi
```
"""

def _template_gitignore()->str:
    return """__pycache__/
*.py[cod]
*.so
*.egg-info/
tags
target/
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

def _template_rs_pyproject(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
    return f"""[build-system]
requires = [\"maturin>=1.0,<2.0\"]
build-backend = \"maturin\"

[project]
name = \"{proj_name}\"
dynamic = [\"version\"]
description = \"{desc}\"
license = {{text = \"Apache-2.0\"}}
requires-python = \">=3.10\"
readme = \"README.md\"
authors = [{{name = \"{proj_name} contributors\"}}]
classifiers = [
    \"Programming Language :: Rust\",
    \"Programming Language :: Python :: Implementation :: CPython\",
]

[project.optional-dependencies]
dev = [\"fastship>=0.0.11\", \"maturin>=1.0,<2.0\", \"pytest\"]

[project.urls]
Homepage = \"https://github.com/{gh_org}/{proj_name}\"
Repository = \"https://github.com/{gh_org}/{proj_name}\"
Issues = \"https://github.com/{gh_org}/{proj_name}/issues\"

[tool.maturin]
features = [\"extension-module\"]
python-source = \"python\"
module-name = \"{pkg_name}._core\"

[tool.fastship]
branch = \"main\"

[tool.pytest.ini_options]
testpaths = [\"tests\"]
"""

def _template_cargo_toml(proj_name:str, pkg_name:str, desc:str)->str:
    return f"""[package]
name = \"{proj_name}\"
version = \"0.1.0\"
edition = \"2021\"
license = \"Apache-2.0\"
description = \"{desc}\"

[lib]
name = \"{pkg_name}\"
crate-type = [\"cdylib\", \"rlib\"]

[dependencies]
pyo3 = {{ version = \">=0.28\", optional = true }}

[features]
extension-module = [\"pyo3\", \"pyo3/extension-module\"]
pyo3 = [\"dep:pyo3\"]
"""

def _template_rs_lib()->str:
    return """pub fn hello(name: &str) -> String {
    format!("Hello, {name}!")
}

#[cfg(feature = "pyo3")]
use pyo3::prelude::*;

#[cfg(feature = "pyo3")]
#[pyfunction(name = "hello")]
fn py_hello(name: &str) -> String {
    hello(name)
}

#[cfg(feature = "pyo3")]
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_hello, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hello_returns_greeting() {
        assert_eq!(hello("Rust"), "Hello, Rust!");
    }
}
"""

def _template_rs_init()->str:
    return """from ._core import __version__, hello

__all__ = ["__version__", "hello"]
"""

def _template_rs_test(pkg_name:str)->str:
    return f"""from {pkg_name} import hello

def test_hello():
    assert hello("fastship") == "Hello, fastship!"
"""

def _template_rs_readme(proj_name:str)->str:
    return f"""# {proj_name}

PyO3/maturin package scaffolded by fastship.

## Development

```bash
pip install -e .[dev]
ship-rs-test
```

## Build

```bash
ship-rs-build
```

## Release

Release flow is: release first, then bump.

```bash
ship-rs-test
ship-rs-release
ship-rs-bump
```

The GitHub workflow builds wheels on tags matching `v*` and publishes them to GitHub Releases and PyPI.
"""

def _template_rs_dev()->str:
    return """# Development

## Commands

```bash
ship-rs-test
ship-rs-build
```

## Versioning

The canonical version lives in `Cargo.toml`. `pyproject.toml` gets the Python package version from Cargo via `dynamic = ["version"]`.

## Release

Release flow is: release first, then bump.

1. Run `ship-rs-test`.
2. Confirm the release version in `Cargo.toml` (`[package].version`).
3. Run `ship-rs-release`.
4. After pushing the release tag, run `ship-rs-bump`, commit the `Cargo.toml` version bump, and push to `main` without a tag.
"""

def _template_rs_workflow()->str:
    return """name: CI

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -e '.[dev]'
      - run: ship-rs-test

  linux:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: PyO3/maturin-action@v1
        with:
          args: --release --out dist -i python3.10 -i python3.11 -i python3.12 -i python3.13
          manylinux: auto
          before-script-linux: |
            python3.13 -m pip install 'fastship>=0.0.11'
            python3.13 -m fastship.rs_prep --release
      - uses: actions/upload-artifact@v4
        with:
          name: wheels-linux
          path: dist

  macos:
    needs: test
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - run: python -m pip install 'fastship>=0.0.11'
      - run: python -m fastship.rs_prep --release
      - uses: PyO3/maturin-action@v1
        with:
          args: --release --out dist -i python3.10 -i python3.11 -i python3.12 -i python3.13
      - uses: actions/upload-artifact@v4
        with:
          name: wheels-macos
          path: dist

  sdist:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: PyO3/maturin-action@v1
        with:
          command: sdist
          args: -o dist
      - uses: actions/upload-artifact@v4
        with:
          name: wheels-sdist
          path: dist

  publish:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: [linux, macos, sdist]
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          path: dist
          merge-multiple: true
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*
          generate_release_notes: true
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: dist/
"""

@call_parse
def _create_rs_project(name:str, package:str = None, description:str = "A PyO3 package", path:str = ".", gh_org:str = "AnswerDotAI", force:bool = False):
    "Create a maturin/PyO3 project and return its root."
    proj = _slugify_dist(name)
    pkg = package or _slugify_pkg(proj)
    root = _prepare_new_root(Path(path) / proj, force)

    _write(root/"pyproject.toml", _template_rs_pyproject(proj, pkg, description, gh_org))
    _write(root/"Cargo.toml", _template_cargo_toml(proj, pkg, description))
    _write(root/"src"/"lib.rs", _template_rs_lib())
    _write(root/"python"/pkg/"__init__.py", _template_rs_init())
    _write(root/"tests"/"test_basic.py", _template_rs_test(pkg))
    _write(root/"README.md", _template_rs_readme(proj))
    _write(root/"DEV.md", _template_rs_dev())
    _write(root/"LICENSE", _read_license())
    _write(root/".gitignore", _template_gitignore())
    _write(root/".github"/"workflows"/"ci.yml", _template_rs_workflow())
    return root

def ship_rs_new(name: str, package: str = None, description: str = "A PyO3 package", path: str = ".", gh_org: str = "AnswerDotAI", force: bool = False):
    "Create a maturin/PyO3 project wired for fastship Rust commands."
    root = _create_rs_project(name, package=package, description=description, path=path, gh_org=gh_org, force=force)

    print(f"Created {root}")
    print(f"Next:\n  cd {root}")
    print("  pip install -e .[dev]")
    print("  ship-rs-test")


@call_parse
def ship_rs_new_cli(
    name: str,              # Project name (PyPI/Cargo name), e.g. "my-project"
    package: str = None,    # Python package import name, e.g. "my_project" (defaults from `name`)
    description: str = "A PyO3 package",  # Short project description
    path: str = ".",        # Directory to create the project folder in
    gh_org: str = "AnswerDotAI",  # GitHub organization for project.urls
    force: bool = False,    # Overwrite if the folder already exists
):
    "Create a maturin/PyO3 project wired for fastship Rust commands."
    return ship_rs_new(name, package=package, description=description, path=path, gh_org=gh_org, force=force)


@call_parse
def ship_new(
    name: str,              # Project name (PyPI name), e.g. "my-project"
    package: str = None,    # Python package import name, e.g. "my_project" (defaults from `name`)
    description: str = "A Python package",  # Short project description
    path: str = ".",        # Directory to create the project folder in
    gh_org: str = "AnswerDotAI",  # GitHub organization for project.urls
    force: bool = False,    # Overwrite if the folder already exists
):
    "Create a modern setuptools project wired for fastship."
    pkg = package or _slugify_pkg(name)
    root = _prepare_new_root(Path(path) / name, force)

    _write(root/"pyproject.toml", _template_pyproject(name, pkg, description, gh_org))
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


# ---------------------------------------------------------------------------
# Quick PR workflow
# ---------------------------------------------------------------------------

@call_parse
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
    if not has_commits and not has_changes: raise SystemExit("Nothing to PR: no unpushed commits and no uncommitted changes")

    slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')[:50]
    if len(slug) == 50: slug = slug.rsplit('-', 1)[0]
    pr_branch = branch or f"pr/{slug}"
    g.switch('-c', pr_branch)

    try:
        if has_changes: g.commit('-am', title)
        g.push('-u', 'origin', pr_branch)

        owner, repo_name = _parse_repo(repo)
        if not owner or not repo_name: raise SystemExit("Could not determine GitHub repo. Use --repo OWNER/REPO")

        token = token or _get_token()
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

    finally: g.switch(default)

    g.fetch('origin')
    g.reset('--hard', f'origin/{default}')
    print(f"Done! {default} updated to include squashed commit.")
