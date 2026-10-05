"""Local-first release helpers for non-nbdev Python projects.

`fastship` is inspired by the tight, simple workflow in `nbdev.release`:
bump a plain `__version__`, upload with `python -m build` + `twine`,
and create GitHub releases directly via `ghapi` (no GitHub Actions required).
"""


__all__ = ["GH_HOST", "DEFAULT_LABEL_GROUPS", "ShipConfig", "RustConfig", "NpmConfig", "get_config", "get_rs_config", "get_npm_config", "bump_version", "Release",
    "ship_bump", "ship_pages", "ship_pypi", "ship_changelog", "ship_release_gh", "ship_release", "ship_new", "ship_pr",
    "ship_rs_new", "ship_rs_init", "ship_rs_build", "ship_rs_bump", "ship_zig_new", "ship_zig_build"]

import os, re, sys, json, shutil, subprocess, ast, importlib.resources, shlex
from dataclasses import dataclass, field

try: import tomllib
except ImportError: import tomli as tomllib  # pragma: no cover
from packaging.version import Version

from fastcore.all import *  # Path, nested_idx, ifnone, parallel, run, repo_details, call_parse, ...
from fastgit import Git
from ghapi.core import *    # GhApi, APIError, ...

GH_HOST = "https://api.github.com"
CHANGELOG_MARKER = "<!-- do not remove -->\n"
_NEW_CHANGELOG = f"# Release notes\n\n{CHANGELOG_MARKER}"

DEFAULT_LABEL_GROUPS = dict(breaking="Breaking Changes", enhancement="New Features", bug="Bugs Squashed")

_pyproj = "pyproject.toml"
_init = "__init__.py"

_NEW_VERSION = "0.1.0"
_GH_ORG = "AnswerDotAI"
_ZIG_REQ = "ziglang==0.16.0"
_MATURIN_REQ = "maturin>=1.0,<2.0"
_RS_DEV_DEPS = ["fastship>=0.0.11", _MATURIN_REQ, "pytest"]
_BUMP_COMMIT = 'git commit -am "bump [skip ci]"'

_re_version_any = re.compile(r"^__version__\s*=.*$", re.MULTILINE)
_re_version_val = re.compile(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Project discovery + config
# ---------------------------------------------------------------------------

def _find_marker(start: Path | None, *fnames: str) -> Path:
    "Find the nearest of `fnames` in `start` (default: the current directory) or its parents. Within each directory, `fnames` are tried in order."
    p = Path(start or Path().absolute())
    for d in [p, *p.parents]:
        for f in fnames:
            if (d/f).exists(): return d/f
    raise FileNotFoundError(f"Could not find {' or '.join(fnames)} (searched parents from {Path().absolute()})")


def _find_pyproject(start: Path | None = None) -> Path: return _find_marker(start, _pyproj)


_MARKER_TYPES = {_pyproj: "py", "package.json": "npm", "Cargo.toml": "crate"}

def _find_project(start: Path | None = None) -> tuple[str, Path]:
    "Nearest project marker up the tree: ('py', pyproject.toml), ('npm', package.json), or ('crate', Cargo.toml), in that priority order."
    p = _find_marker(start, *_MARKER_TYPES)
    return _MARKER_TYPES[p.name], p


def _load_toml(p: Path) -> dict: return tomllib.loads(p.read_text(encoding="utf-8"))

def _load_json(p: Path) -> dict: return json.loads(p.read_text(encoding="utf-8"))


def _ship_cfg(data:dict) -> dict: return nested_idx(data, "tool", "fastship") or {}


def _is_uv_workspace(data:dict) -> bool: return nested_idx(data, "tool", "uv", "workspace") is not None


def _static_version(pyproject:Path) -> str | None: return nested_idx(_load_toml(pyproject), "project", "version")


def _norm_mod(name: str) -> str:
    "Normalize a project name to a likely Python import/package name."
    name = name.strip().replace("-", "_")
    return re.sub(r"[^0-9a-zA-Z_]+", "_", name)


def _pkg_bases(root:Path, data:dict)->list[Path]:
    "Package roots declared by setuptools, followed by its conventional layouts."
    package_dir = nested_idx(data, "tool", "setuptools", "package-dir", "")
    bases = [root/package_dir] if package_dir else []
    return list(dict.fromkeys([*bases, root/"src", root]))


def _find_pkg_path(root: Path, data: dict) -> Path:
    "Find the package directory. A `[tool.fastship].package` is searched for recursively. Without one, use the package named after `[project].name`, else the first package folder."
    bases = [o for o in _pkg_bases(root, data) if o.exists()]
    def top_level(pkg): return (b/pkg for b in bases if (b/pkg/_init).exists())
    if pkg := _ship_cfg(data).get("package"):
        nested = (p.parent for b in bases for p in b.rglob(_init) if p.parent.name == pkg)
        if found := next(top_level(pkg), None) or next(nested, None): return found
        raise FileNotFoundError(f"Could not find {pkg}/__init__.py under {root}")
    if (nm := nested_idx(data, "project", "name")) and (found := next(top_level(_norm_mod(nm)), None)): return found
    # The folders in a uv workspace root are member projects, never the root's own package
    if not _is_uv_workspace(data):
        found = next((p for b in bases for p in b.iterdir() if p.is_dir() and (p/_init).exists() and not p.name.startswith(".")), None)
        if found: return found
    raise FileNotFoundError(
        f'Could not find package directory. Ensure [project].name in pyproject.toml '
        f'matches your package folder (e.g., "my-project" -> my_project/).')


def _gh_only(root:Path, data:dict) -> bool:
    "True when the project has a static `[project].version` and no package to build. Such a project is released on GitHub only."
    if nested_idx(data, "project", "version") is None or _ship_cfg(data).get("package"): return False
    try: _find_pkg_path(root, data)
    except FileNotFoundError: return True
    return False

def _load_release_yml(root: Path) -> dict | None:
    "Load label groups from .github/release.yml if it exists."
    for name in ("release.yml", "release.yaml"):
        p = root / ".github" / name
        if p.exists():
            import yaml
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            categories = nested_idx(data, "changelog", "categories") or []
            groups = {}
            for cat in categories:
                title = cat.get("title")
                if not title: continue
                for label in cat.get("labels", []):
                    if label != "*": groups[label] = title
            return groups if groups else None
    return None



def _git(cmd:str, default:str = "", path:Path = None) -> str:
    "Run `git {cmd}` in `path` (default: the current directory) and return its stripped output, or `default` when git fails or prints nothing."
    try: return run(f"git -C {_q(path)} {cmd}" if path else f"git {cmd}").strip() or default
    except Exception: return default


def _branch(ship:dict = None) -> str:
    "Return `[tool.fastship].branch`, else `$FASTSHIP_BRANCH`, else the current git branch, else `main`."
    return (ship or {}).get("branch") or os.getenv("FASTSHIP_BRANCH") or _git("branch --show-current", "main")


def _parse_repo(repo:str = None, path:Path = None) -> tuple[str | None, str | None]:
    "Parse 'OWNER/REPO' string, falling back to the git origin in `path`."
    if repo and "/" in repo: return repo.split("/", 1)
    try: g_owner, g_repo = repo_details(_git("config --get remote.origin.url", path=path))
    except Exception: g_owner = g_repo = None
    return g_owner, repo or g_repo


def _git_has_changes() -> bool:
    "Return `True` if the current git worktree has staged or unstaged changes."
    return bool(run("git status --porcelain --untracked-files=no").strip())


def _get_token(root: Path = None) -> str | None:
    "Find GitHub token from env vars or token file."
    token = os.getenv("FASTSHIP_TOKEN")
    if not token and root and (root / "token").exists(): token = (root / "token").read_text().strip()
    if not token and Path("token").exists(): token = Path("token").read_text().strip()
    return token or os.getenv("GITHUB_TOKEN")


def _gh_api(owner:str = None, repo:str = None, token:str = None, root:Path = None) -> GhApi:
    "Return a `GhApi` for `owner`/`repo`, authenticated with `token`, else `_get_token(root)`. Without `owner`, `_parse_repo` reads the owner from `repo` or from the git origin in `root`."
    if not owner: owner, repo = _parse_repo(repo, root)
    if not owner or not repo: raise CliError("Could not infer GitHub owner/repo. Pass --repo OWNER/REPO or set a git remote `origin`.")
    token = token or _get_token(root)
    if not token: raise CliError("Failed to find token (FASTSHIP_TOKEN, GITHUB_TOKEN, or a ./token file)")
    return GhApi(owner, repo, token)


@dataclass
class _PyprojectConfig:
    root: Path
    pyproject: Path
    data: dict
    branch: str
    changelog_file: Path
    label_groups: dict
    version_files: list[Path]


@dataclass
class ShipConfig(_PyprojectConfig):
    pkg: str | None
    pkg_path: Path | None
    init_file: Path | None
    wheel_only: bool

    @property
    def version(self) -> str: return _static_version(self.pyproject) or _read_version(self.init_file)

    @property
    def gh_only(self) -> bool: return self.pkg is None


@dataclass
class RustConfig(_PyprojectConfig):
    manifest_path: Path

    @property
    def version(self) -> str: return _cargo_version(self.manifest_path)


def _pyproject_settings(pyproj:Path, data:dict) -> dict:
    "Fields shared by `ShipConfig` and `RustConfig`, read from `pyproj` and its `[tool.fastship]` table."
    root, ship = pyproj.parent, _ship_cfg(data)
    return dict(root=root, pyproject=pyproj, data=data, branch=_branch(ship), changelog_file=root/ship.get("changelog_file", "CHANGELOG.md"),
        label_groups=_load_release_yml(root) or ship.get("label_groups") or DEFAULT_LABEL_GROUPS, version_files=_version_files(root, ship))


def get_config(start: str | Path | None = None) -> ShipConfig:
    "Load fastship config from `pyproject.toml`."
    pyproj = _find_pyproject(start)
    root, data = pyproj.parent, _load_toml(pyproj)
    pkg = pkg_path = init_file = None
    if _gh_only(root, data):
        if _is_uv_workspace(data): raise CliError(f"{pyproj} is a uv workspace root. It has no package to release.")
    else:
        pkg_path = _find_pkg_path(root, data)
        pkg, init_file = pkg_path.name, pkg_path/_init
    ship = _ship_cfg(data)
    if ship.get("release") not in (None, "tag"): raise ValueError('[tool.fastship].release must be "tag" when set')
    return ShipConfig(pkg=pkg, pkg_path=pkg_path, init_file=init_file, wheel_only=ship.get("wheel-only", False), **_pyproject_settings(pyproj, data))


def get_rs_config(start: str | Path | None = None) -> RustConfig:
    "Load fastship config for a maturin/PyO3 project."
    pyproj = _find_pyproject(start)
    data = _load_toml(pyproj)
    proj = data.get("project") or {}
    if proj.get("version") is not None: raise ValueError(f'{pyproj} must use Cargo.toml for ship-rs versions; remove [project].version')
    dyn = proj.get("dynamic") or []
    if not isinstance(dyn, list) or "version" not in dyn: raise ValueError(f'{pyproj} must set [project].dynamic = ["version"] for ship-rs commands')
    manifest_path = pyproj.parent / (nested_idx(_ship_cfg(data), "rs", "manifest_path") or "Cargo.toml")
    return RustConfig(manifest_path=manifest_path, **_pyproject_settings(pyproj, data))


def _version_files(root:Path, ship:dict) -> list[Path]:
    "Synchronized version copies from `[tool.fastship].version-files`, resolved against `root`."
    files = ship.get("version-files") or []
    if not isinstance(files, list) or not all(isinstance(o, str) for o in files):
        raise ValueError("[tool.fastship].version-files must be a list of paths")
    return [root/o for o in files]


def _project_type(root:Path, data:dict)->str:
    if (root/"Cargo.toml").exists() or _is_maturin_project(data): return "rust"
    reqs = nested_idx(data, "build-system", "requires") or []
    if any(re.match(r"^ziglang(?:\W|$)", o) for o in reqs): return "zig"
    return "python"


@dataclass
class NpmConfig:
    root: Path
    pkg_json: Path
    name: str
    branch: str

    @property
    def version(self) -> str:
        v = _load_json(self.pkg_json).get("version")
        if not v: raise ValueError(f"No version field found in {self.pkg_json}")
        return v


def get_npm_config(start: str | Path | None = None) -> NpmConfig:
    "Load fastship config for an npm (package.json, no pyproject.toml) project."
    pkg_json = _find_marker(start, "package.json")
    return NpmConfig(root=pkg_json.parent, pkg_json=pkg_json, name=_load_json(pkg_json).get("name", ""), branch=_branch())


def _write_npm_version(pkg_json: Path, version: str):
    "Rewrite the `version` field in place, preserving the file's formatting."
    txt = pkg_json.read_text(encoding="utf-8")
    new, n = re.subn(r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', txt, count=1)
    if not n: raise ValueError(f"No version field found in {pkg_json}")
    pkg_json.write_text(new, encoding="utf-8")


@dataclass
class CrateConfig:
    root: Path
    manifest_path: Path
    name: str
    branch: str
    version_files: list[Path] = field(default_factory=list)

    @property
    def version(self) -> str: return _cargo_version(self.manifest_path)


def get_crate_config(start: str | Path | None = None) -> CrateConfig:
    "Load fastship config for a pure-Rust crate (Cargo.toml, no pyproject.toml)."
    manifest = _find_marker(start, "Cargo.toml")
    name = nested_idx(_load_toml(manifest), "package", "name") or ""
    return CrateConfig(root=manifest.parent, manifest_path=manifest, name=name, branch=_branch())


def _cargo_bump(cfg, part:int = None, unbump:bool = False):
    "Bump the version in Cargo.toml (`[package]`, or `[workspace.package]` when inherited), printing old and new."
    write = partial(_replace_toml_section_key, cfg.manifest_path, _cargo_version_section(cfg.manifest_path), "version")
    return _bump(cfg.version, write, cfg.version_files, part, unbump)


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
    _write_lines(init_file, out)


def _write_config_version(cfg:ShipConfig, version:str):
    "Write `version` to `[project].version` when the pyproject sets it statically, else to `__version__` in the package's `__init__.py`."
    if _static_version(cfg.pyproject) is not None: _replace_toml_section_key(cfg.pyproject, "project", "version", version)
    else: _write_version(cfg.init_file, version)


def _bump(old:str, write, version_files:list[Path] = (), part:int = None, unbump:bool = False) -> str:
    "Bump `old`, write the new version with `write`, and replace `old` in each of `version_files`, printing both versions. If a version file does not hold `old` exactly once, raise `ValueError` before writing anything."
    print(f"Old version: {old}")
    new = bump_version(old, part=part, unbump=unbump)
    copies = {p: p.read_text(encoding="utf-8") for p in version_files}
    for p, text in copies.items():
        if text.count(old) != 1: raise ValueError(f"Expected exactly one {old!r} in {p}")
    write(new)
    for p, text in copies.items(): p.write_text(text.replace(old, new), encoding="utf-8")
    print(f"New version: {new}")
    return new


def bump_version(version: str, part: int = None, unbump: bool = False) -> str:
    "Bump `.postN` by default when present, otherwise one release part."
    v = Version(version)
    amount = -1 if unbump else 1
    if part is None and v.post is not None: return f"{'.'.join(map(str, v.release))}.post{max(0, v.post + amount)}"
    if part is None: part = 2
    if part not in (0, 1, 2): raise ValueError("part must be 0, 1, or 2")
    rel = list(v.release)
    while len(rel) < 3: rel.append(0)
    rel = rel[:3]
    rel[part] = max(0, rel[part] + amount)
    for i in range(part + 1, 3): rel[i] = 0
    return ".".join(map(str, rel))


# ---------------------------------------------------------------------------
# Rust/PyO3 helpers
# ---------------------------------------------------------------------------

def _q(s) -> str: return shlex.quote(str(s))


def _write_lines(p:Path, lines:list[str]): p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _replace_toml_section_key(p:Path, section:str, key:str, val:str):
    "Replace `key = ...` inside a top-level TOML section."
    lines = p.read_text(encoding="utf-8").splitlines()
    start, end = _toml_section_bounds(lines, section)
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    hits = [i for i in range(start + 1, end) if pat.match(lines[i])] if start is not None else []
    if not hits: raise ValueError(f"Could not find {key!r} in [{section}] of {p}")
    lines[hits[0]] = f'{key} = "{val}"'
    _write_lines(p, lines)


def _is_maturin_project(data:dict) -> bool:
    build_backend = nested_idx(data, "build-system", "build-backend") or ""
    return "maturin" in build_backend or bool(nested_idx(data, "tool", "maturin"))


def _cargo_version_section(manifest:Path) -> str:
    "The version section in Cargo.toml: package, inherited workspace, or virtual workspace."
    data = _load_toml(manifest)
    ver = (data.get("package") or {}).get("version")
    inherited = isinstance(ver, dict) and ver.get("workspace") is True
    virtual = "workspace" in data and "package" not in data
    return "workspace.package" if inherited or virtual else "package"


def _cargo_version(manifest:Path) -> str:
    "Read the version from Cargo.toml, following `version.workspace = true` to `[workspace.package]`."
    sec = _cargo_version_section(manifest)
    ver = nested_idx(_load_toml(manifest), *sec.split("."), "version")
    if not isinstance(ver, str) or not ver: raise ValueError(f"Could not find [{sec}].version in {manifest}")
    return ver


def _fmt_toml_val(v):
    if isinstance(v, bool): return str(v).lower()
    if isinstance(v, (list, tuple)): return "[" + ", ".join(f'"{o}"' for o in v) + "]"
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
    vals = {k: f"{k} = {_fmt_toml_val(v)}" for k, v in items.items()}
    if start is None:
        if lines and lines[-1].strip(): lines.append("")
        lines += [f"[{section}]", *vals.values()]
    else:
        existing = {m.group(1): i for i in range(start + 1, end) if (m := re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", lines[i]))}
        if replace:
            for k in vals.keys() & existing.keys(): lines[existing[k]] = vals[k]
        lines[end:end] = [val for k, val in vals.items() if k not in existing]
    _write_lines(p, lines)


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
    _write_lines(p, lines)


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
        _write_lines(init, lines)
        return
    _warn(f"found literal __version__ in {init} but could not find a simple import from `{pkg}.{ext}`; left Python wrapper unchanged")


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
    return res + "\n".join(map(_issue_txt, iss))


class Release:
    def __init__(self, owner=None, repo=None, token=None, cfg: ShipConfig | None = None, **groups):
        "Create CHANGELOG.md from closed GitHub issues and publish GitHub releases."
        self.cfg = cfg or get_config()
        self.changefile = self.cfg.changelog_file
        self.groups = groups or self.cfg.label_groups
        os.chdir(self.cfg.root)
        self.gh = _gh_api(owner, repo, token, self.cfg.root)

    async def _issues(self, label):
        return await self.gh.issues.list_for_repo(state="closed", sort="created", filter="all", since=self.commit_date, labels=label)

    async def changelog(self, debug: bool = False):
        """Create or update CHANGELOG.md from closed and labeled GitHub issues.

        Issues are pulled since the latest GitHub release's `published_at`.
        If no releases exist, all matching issues are included.
        """
        if not self.changefile.exists(): self.changefile.write_text(_NEW_CHANGELOG, encoding="utf-8")

        try:
            lr = await self.gh.repos.get_latest_release()
            self.commit_date = lr.published_at
        except APIError as e:
            if e.status_code != 404: raise
            lr, self.commit_date = None, "2000-01-01T00:00:00Z"

        if lr and (Version(self.cfg.version) <= Version(lr.tag_name)):
            raise CliError(f"Version bump required: expected: >{lr.tag_name}, got: {self.cfg.version}.")

        res = f"\n## {self.cfg.version}\n\n"
        issues = await parallel_async(self._issues, self.groups.keys())
        res += "\n\n".join(issues.map_zipwith(_issues_txt, self.groups.values()).filter())

        if debug: return res

        txt = self.changefile.read_text(encoding="utf-8")
        if CHANGELOG_MARKER not in txt:
            raise ValueError(
                f"{self.changefile} is missing the fastship changelog marker "
                f"{CHANGELOG_MARKER.strip()!r}. Add it near the top of the file.")
        from nbdev.release import update_changelog
        txt = update_changelog(txt, self.cfg.version, res, CHANGELOG_MARKER)
        self.changefile.write_text(txt, encoding="utf-8")
        run(f"git add {self.changefile}")
        return self

    def latest_notes(self) -> str:
        "Latest CHANGELOG entry (the most recent `## <version>` section)."
        if not self.changefile.exists(): return ""
        its = re.split(r"^## ", self.changefile.read_text(encoding="utf-8"), flags=re.MULTILINE)
        if len(its) <= 1: return ""
        return "\n".join(its[1].splitlines()[1:]).strip()

    async def release(self):
        "Tag and create a release in GitHub for the current version."
        notes = dict(generate_release_notes=True) if self.cfg.gh_only else dict(body=self.latest_notes())
        await self.gh.create_release(self.cfg.version, branch=self.cfg.branch, **notes)
        return self


# ---------------------------------------------------------------------------
# CLI entrypoints
# ---------------------------------------------------------------------------


def _nbdev_release():
    "Return the `nbdev.release` module when the nearest project is an nbdev Python project, else None. nbdev-docs-over-maturin repos get None, because the tag flow releases them."
    ftype, pyproj = _find_project()
    if ftype != "py": return None
    data = _load_toml(pyproj)
    if nested_idx(data, "tool", "nbdev") is None or _project_type(pyproj.parent, data) != "python" or _gh_only(pyproj.parent, data): return None
    import nbdev.release
    return nbdev.release


@call_parse
def ship_bump(
    part: int = None,  # Release part to bump; defaults to post when present, otherwise patch
    unbump: bool = False,  # Reduce version instead of increasing it
):
    "Bump version: nbdev projects delegate to `nbdev-bump-version`; Cargo.toml (then `maturin develop`) for Rust (pure crates skip the reinstall); package.json for npm; else `__init__.py`."
    if (nbr := _nbdev_release()): return nbr.nbdev_bump_version(part=part, unbump=unbump)
    ftype, pyproj = _find_project()
    if ftype == "npm": _npm_bump(part=part, unbump=unbump)
    elif ftype == "crate": _cargo_bump(get_crate_config(), part=part, unbump=unbump)
    elif _project_type(pyproj.parent, _load_toml(pyproj)) == "rust": ship_rs_bump(part=part, unbump=unbump)
    else:
        cfg = get_config()
        _bump(cfg.version, partial(_write_config_version, cfg), cfg.version_files, part, unbump)


def _clean_dist(root: Path):
    for d in ("dist", "build"):
        p = root / d
        if p.exists(): shutil.rmtree(p)
    for p in root.glob("*.egg-info"):
        if p.is_dir(): shutil.rmtree(p)


async def _prepare_release(rel, no_changelog:bool = False, no_editor:bool = False, yes:bool = False):
    if rel.cfg.gh_only: no_changelog = no_editor = True
    if not no_changelog: await rel.changelog()
    if not no_editor: subprocess.run([os.environ.get("EDITOR", "nano"), rel.changefile])
    if not yes and not input("Make release now? (y/n) ").lower().startswith("y"): sys.exit(1)


def _commit_release():
    if _git_has_changes(): run("git commit -am release")
    run("git push")


def _build_dist(cfg, wheel_only:bool = False, quiet:bool = False):
    os.chdir(cfg.root)
    _clean_dist(cfg.root)
    q = " --quiet" if quiet else ""
    wheel = " --wheel" if wheel_only or cfg.wheel_only else ""
    run(f"{sys.executable} -m build{wheel}{q}")
    run("twine check dist/*")


def _upload_dist(repository:str = "pypi", quiet:bool = False, verbose:bool = False):
    p = " --disable-progress-bar" if quiet else ""
    v = " --verbose" if verbose else ""
    run(f"twine upload{v} --repository {repository}{p} dist/*")


@call_parse
def ship_pypi(
    repository: str = "pypi",  # Repository in ~/.pypirc (e.g. "pypi" or "testpypi")
    quiet: bool = False,  # Reduce output verbosity
    wheel_only: bool = False,  # Build a wheel directly instead of building an sdist first
    verbose: bool = False,  # Pass --verbose to twine upload
):
    "Build and upload the package to PyPI (uses `python -m build` + `twine upload`)."
    if (nbr := _nbdev_release()): return nbr.release_pypi(repository=repository, quiet=quiet, verbose=verbose)
    cfg = get_config()
    _build_dist(cfg, wheel_only=wheel_only, quiet=quiet)
    _upload_dist(repository=repository, quiet=quiet, verbose=verbose)


@call_parse
async def ship_pages(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
):
    "Enable GitHub Pages from main:/ and use its URL as the repository homepage."
    gh = _gh_api(token=token)
    pages = await gh.repos.create_pages_site(build_type="legacy", source={"branch":"main", "path":"/"})
    await gh.repos.update(homepage=pages.html_url)
    print(f"GitHub Pages enabled: {pages.html_url}")
    return pages.html_url


@call_parse
async def ship_changelog(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,   # Override repo ("OWNER/REPO")
):
    "Create/update CHANGELOG.md from closed GitHub issues (without opening editor or releasing)."
    if (nbr := _nbdev_release()): return await nbr.changelog(repo=repo, token=token)
    print(f"Updated {(await Release(repo=repo, token=token).changelog()).changefile}")


@call_parse
async def ship_release_gh(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,   # Override repo ("OWNER/REPO")
    no_changelog: bool = False,  # Skip changelog generation (assumes CHANGELOG.md is ready)
    no_editor: bool = False,  # Skip opening CHANGELOG.md in an editor
    yes: bool = False,  # Release without asking for confirmation
):
    "Create/update CHANGELOG.md, optionally edit it, then commit/push and create a GitHub release."
    if (nbr := _nbdev_release()):
        return await nbr.release_gh(token=token, repo=repo, no_changelog=no_changelog, no_editor=no_editor, yes=yes)
    rel = Release(repo=repo, token=token)
    await _prepare_release(rel, no_changelog=no_changelog, no_editor=no_editor, yes=yes)
    _commit_release()
    print(f"GitHub release created: {(await rel.release()).cfg.version}")


def _npm_bump(part:int = None, unbump:bool = False):
    cfg = get_npm_config()
    return _bump(cfg.version, partial(_write_npm_version, cfg.pkg_json), part=part, unbump=unbump)


def _bump_and_push():
    "Bump the version, then commit and push it with a message that skips GitHub Actions `push` workflows."
    ship_bump()
    run(_BUMP_COMMIT)
    run("git push")


def _ship_tag_release(kind:str):
    "Tag `v<version>` and push branch + tag (CI builds, publishes, and writes release notes), then bump."
    cfg = dict(crate=get_crate_config, rust=get_rs_config, npm=get_npm_config).get(kind, get_config)()
    os.chdir(cfg.root)
    if _git_has_changes(): raise CliError("Uncommitted changes: commit or stash before releasing")
    version, tag = cfg.version, f"v{cfg.version}"
    run(f"git tag -a {_q(tag)} -m {_q(tag)}")
    run(f"git push origin {_q(cfg.branch)}")
    run(f"git push origin {_q(tag)}")
    _bump_and_push()
    print(f"Release started: {tag}")
    return version


@call_parse
async def ship_release(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,   # Override repo ("OWNER/REPO")
    repository: str = "pypi",  # PyPI repository in ~/.pypirc
    no_changelog: bool = False,  # Skip changelog generation (Python/nbdev flow only)
    no_editor: bool = False,  # Skip opening CHANGELOG.md in an editor (Python/nbdev flow only)
    yes: bool = False,  # Release without asking for confirmation (Python/nbdev flow; tag-push flows never ask)
    wheel_only: bool = False,  # Build a wheel directly instead of building an sdist first
    verbose: bool = False,  # Pass --verbose to twine upload
):
    "Release the project, bump the version, and push: changelog+PyPI for Python/nbdev; flag-free tag-push (CI publishes) for Rust/Zig/npm/crate projects. A static version with no package is released on GitHub only."
    ftype, proj = _find_project()
    data = _load_toml(proj) if ftype == "py" else {}
    kind = _project_type(proj.parent, data) if ftype == "py" else ftype
    if _ship_cfg(data).get("release") == "tag":
        _ship_tag_release(kind)
        return
    if _nbdev_release():
        await ship_release_gh(token=token, repo=repo, no_changelog=no_changelog, no_editor=no_editor, yes=yes)
        ship_pypi(repository=repository, wheel_only=wheel_only, verbose=verbose)
        _bump_and_push()
        return
    if kind != "python":
        _ship_tag_release(kind)
        return
    rel = Release(repo=repo, token=token)
    await _prepare_release(rel, no_changelog=no_changelog, no_editor=no_editor, yes=yes)
    if not rel.cfg.gh_only: _build_dist(rel.cfg, wheel_only=wheel_only)
    _commit_release()
    version = rel.cfg.version
    await rel.release()
    print(f"GitHub release created: {version}")
    if not rel.cfg.gh_only: _upload_dist(repository=repository, verbose=verbose)
    _bump_and_push()
    print(f"Released {version}")


@call_parse
def ship_rs_init(
    branch: str = None,   # Branch for [tool.fastship] (defaults to existing/current)
    force: bool = False,  # Replace existing [tool.fastship] keys
):
    "Configure an existing maturin/PyO3 project for fastship Rust commands."
    pyproj = _find_pyproject()
    root, data = pyproj.parent, _load_toml(pyproj)
    if not _is_maturin_project(data): raise CliError(f"{pyproj} does not look like a maturin project")
    if not (root/"Cargo.toml").exists(): raise CliError(f"Missing {root/'Cargo.toml'}")
    _ensure_project_dynamic_version(pyproj)
    _ensure_toml_section(pyproj, "project.optional-dependencies", dict(dev=_RS_DEV_DEPS))
    _ensure_rs_runtime_version(root, data)
    _ensure_py_runtime_version(root, data)
    _ensure_toml_section(pyproj, "tool.fastship", dict(branch=branch or _branch(_ship_cfg(data))), replace=force)
    print(f"Updated {pyproj}")



@call_parse
def ship_rs_build(
    profile: str = "release", # Cargo build profile
    target: str = None,    # Optional Rust target triple
    outdir: str = "dist",  # Wheel output directory
    args: str = "",        # Extra arguments passed to maturin's build backend
):
    r"Build a wheel through the project's configured PEP 517 backend."
    cfg = get_rs_config()
    os.chdir(cfg.root)
    options = ['--profile', _q(profile)]
    if target: options += ['--target', _q(target)]
    if args: options.append(args)
    config = _q('maturin.build-args=' + ' '.join(options))
    run(f'{_q(sys.executable)} -m build --wheel --outdir {_q(outdir)} -C {config}')


@call_parse
def ship_zig_build(
    quiet: bool = False,  # Reduce output verbosity
):
    "Build and check the current platform wheel for a fastship Zig project."
    cfg = get_config()
    if _project_type(cfg.root, cfg.data) != "zig": raise CliError("Not a fastship Zig project")
    _build_dist(cfg, wheel_only=True, quiet=quiet)



def ship_rs_bump(part: int = 2, unbump: bool = False):
    "Bump the version in Cargo.toml (`[package]`, or `[workspace.package]` when inherited), then refresh the local editable install."
    cfg = get_rs_config()
    _cargo_bump(cfg, part=part, unbump=unbump)
    os.chdir(cfg.root)
    run("maturin develop")




# ---------------------------------------------------------------------------
# Project scaffolding
# ---------------------------------------------------------------------------

_LICENSE = "Apache-2.0"
_SETUPTOOLS_REQ = "setuptools>=77"
_PIP_DEV = "pip install -e .[dev]"
_PY_CLASSIFIERS = ["Programming Language :: Python :: 3", "Programming Language :: Python :: 3 :: Only"]
_TAG_RELEASE = "`ship-release` pushes a `v<version>` tag, which starts the release workflow in GitHub Actions, then bumps the version."
_ACTIONS = dict(checkout="actions/checkout@v7", setup_python="actions/setup-python@v7", rust="dtolnay/rust-toolchain@stable",
    maturin="PyO3/maturin-action@v1", cibuildwheel="pypa/cibuildwheel@v4.2.0", upload="actions/upload-artifact@v7",
    download="actions/download-artifact@v8", gh_release="softprops/action-gh-release@v3", pypi="pypa/gh-action-pypi-publish@release/v1",
    crates_auth="rust-lang/crates-io-auth-action@v1")

def _slug(name:str, sep:str = "_", lower:bool = False) -> str:
    "Slugify `name`, replacing each run of characters other than ASCII letters and digits with `sep`. An empty slug becomes `pkg`. A slug that starts with a digit gets a `pkg` prefix."
    s = re.sub(r"[^0-9A-Za-z]+", sep, name.lower() if lower else name).strip(sep) or "pkg"
    return f"pkg{sep}{s}" if s[0].isdigit() else s

def _names(name:str, package:str = None) -> tuple[str, str]:
    "Return the distribution name slugged from `name`, and the package name. The package name is `package` when given, else the distribution name slugged with underscores."
    dist = _slug(name, "-", lower=True)
    return dist, package or _slug(dist)

def _authors_toml(proj_name:str)->str:
    "pyproject `authors` entry from git's global config, else a generic contributors entry."
    name,email = _git("config --get user.name"),_git("config --get user.email")
    if not name: return f'{{name = "{proj_name} contributors"}}'
    return f'{{name = "{name}", email = "{email}"}}' if email else f'{{name = "{name}"}}'

def _gh_url(org:str, proj:str) -> str: return f"https://github.com/{org}/{proj}"

def _write(p:Path, s:str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def _read_asset(name:str)->str: return importlib.resources.files("fastship").joinpath(*Path(name).parts).read_text(encoding="utf-8")

def _scaffold(root:Path, files:dict, force:bool = False, site:bool = True) -> Path:
    "Create the project directory `root` with `files` (relative path -> text), a LICENSE and a .gitignore. When `site` is true, also add the GitHub Pages site files."
    root = root.expanduser()
    if root.exists():
        if not force: raise FileExistsError(f"{root} already exists (use force=True to overwrite)")
        shutil.rmtree(root)
    files = {"LICENSE": _read_asset("LICENSE"), ".gitignore": _template_gitignore(), **files}
    if site: files |= {o: _read_asset(o) for o in ("_config.yml", "_layouts/default.html")}
    for name, text in files.items(): _write(root/name, text)
    return root

def _created(root:Path, *cmds:str) -> Path:
    "Print the new project's location and the commands to run next, then return `root`."
    print(f"Created {root}")
    print(f"Next:\n  cd {root}")
    for o in cmds: print(f"  {o}")
    return root

def _bash(*cmds:str) -> str: return "```bash\n%s\n```" % "\n".join(cmds)

def _md(title:str, intro:str, sections:dict) -> str:
    "Build a markdown document with heading `title`, then `intro` when given, then one `##` section per `sections` item."
    return "\n\n".join([f"# {title}", *([intro] if intro else []), *(f"## {k}\n\n{v}" for k, v in sections.items())]) + "\n"

def _tag_readme(proj:str, intro:str, test:str, notes:str, setup:tuple = (_PIP_DEV,), build:str = None) -> str:
    "Build the README of a project released by a version-tag workflow. It lists the `setup` and `test` commands, the optional `build` command, and the release steps, followed by `notes`."
    sections = {"Development": _bash(*setup, test)}
    if build: sections["Build"] = _bash(build)
    sections["Release"] = f"{_bash(test, 'ship-release')}\n\n{_TAG_RELEASE} {notes}"
    return _md(proj, intro, sections)

def _pyproject(
    proj:str, # Distribution name
    desc:str, # Project description
    gh_org:str, # GitHub organization for the project URLs
    classifiers:list, # Trove classifiers
    dev:list, # Requirements for the `dev` extra
    tool:list = (), # Further `[tool.*]` tables, as TOML text
    deps:list = (), # Runtime dependencies
    requires:list = (_SETUPTOOLS_REQ,), # Build requirements
    backend:str = "setuptools.build_meta", # Build backend
    license:str = None, # TOML value for `[project].license`, defaulting to the SPDX expression `_LICENSE`
    ship:dict = None, # `[tool.fastship]` keys besides `branch = "main"`
) -> str:
    "Text of a scaffolded `pyproject.toml`."
    url = _gh_url(gh_org, proj)
    fastship = "\n".join(f"{k} = {_fmt_toml_val(v)}" for k, v in {"branch": "main", **(ship or {})}.items())
    return "\n\n".join([f'[build-system]\nrequires = {_fmt_toml_val(requires)}\nbuild-backend = "{backend}"',
        f"""[project]
name = "{proj}"
dynamic = ["version"]
description = "{desc}"
readme = "README.md"
requires-python = ">=3.10"
license = {license or _fmt_toml_val(_LICENSE)}
authors = [{_authors_toml(proj)}]
classifiers = {_fmt_toml_val(classifiers)}
dependencies = {_fmt_toml_val(deps)}""",
        f"[project.optional-dependencies]\ndev = {_fmt_toml_val(dev)}",
        f'[project.urls]\nHomepage = "{url}"\nRepository = "{url}"\nIssues = "{url}/issues"',
        f"[tool.fastship]\n{fastship}", *tool]) + "\n"

def _setuptools_tables(pkg:str) -> list[str]:
    "Return `[tool.setuptools]` tables that package only `pkg` and read the version from `pkg.__version__`."
    return ['[tool.setuptools.dynamic]\nversion = { attr = "%s.__version__" }' % pkg, f'[tool.setuptools.packages.find]\ninclude = ["{pkg}"]']

def _cargo_package(proj:str, desc:str, gh_org:str) -> str:
    "`[package]` table of a scaffolded `Cargo.toml`."
    return "\n".join(["[package]", f'name = "{proj}"', f'version = "{_NEW_VERSION}"', 'edition = "2024"', f'license = "{_LICENSE}"',
        f'description = "{desc}"', f'repository = "{_gh_url(gh_org, proj)}"'])

_WORKFLOW = """name: CI

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:

permissions:
  contents: read

jobs:
%(jobs)s
  publish:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: %(needs)s
    runs-on: ubuntu-latest
    permissions:
      contents: write
      id-token: write
    steps:
%(publish)s"""

_PYPI_PUBLISH = """      - uses: %(download)s
        with:
          pattern: wheels-*
          path: dist
          merge-multiple: true
      - uses: %(gh_release)s
        with:
          files: dist/*
          generate_release_notes: true
      - uses: %(pypi)s
"""

def _workflow(jobs:str, needs:str, publish:str = _PYPI_PUBLISH) -> str:
    "CI workflow that runs `jobs` on pushes and pull requests, then on `v*` tags runs the `publish` steps after the `needs` jobs. `jobs` and `publish` name actions by their `_ACTIONS` keys."
    return _WORKFLOW % dict(jobs=jobs % _ACTIONS, needs=needs, publish=publish % _ACTIONS)

_RS_HELLO = """pub fn hello(name: &str) -> String {
    format!("Hello, {name}!")
}
"""

def _template_pyproject(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
    return _pyproject(proj_name, desc, gh_org, _PY_CLASSIFIERS, ["fastship", "build", "twine"], _setuptools_tables(pkg_name))

def _template_readme(proj_name:str, pkg_name:str)->str:
    labels = ", ".join(f"`{o}`" for o in DEFAULT_LABEL_GROUPS)
    bump = _bash("ship-bump --part 2   # patch", "ship-bump --part 1   # minor", "ship-bump --part 0   # major")
    return _md(proj_name, "A modern Python package scaffolded by **fastship**.", {
        "Development": _bash(_PIP_DEV),
        "Versioning": f"Version lives in `{pkg_name}/__init__.py` as `__version__`.\nBump it with:\n\n{bump}",
        "Release": f"1) Ensure your GitHub issues are labeled ({labels}).\n2) Run:\n\n{_bash('ship-release')}"})

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
Cargo.lock
"""

def _template_manifest(*extra:str)->str: return "\n".join(["include README.md", "include LICENSE", *extra]) + "\n"

def _template_rs_pyproject(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
    maturin = f'[tool.maturin]\nfeatures = ["extension-module"]\npython-source = "python"\nmodule-name = "{pkg_name}._core"'
    uv = '[tool.uv]\ncache-keys = [{ file = "pyproject.toml" }, { file = "src/**/*.rs" }, { file = "Cargo.toml" }, { file = ".git/fastws-cargo-key" }]'
    classifiers = ["Programming Language :: Rust", "Programming Language :: Python :: Implementation :: CPython"]
    return _pyproject(proj_name, desc, gh_org, classifiers, _RS_DEV_DEPS, [maturin, uv, '[tool.pytest.ini_options]\ntestpaths = ["tests"]'],
        requires=[_MATURIN_REQ], backend="maturin", license='{text = "%s"}' % _LICENSE)

def _template_cargo_toml(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
    return _cargo_package(proj_name, desc, gh_org) + f"""

[lib]
name = "{pkg_name}"
crate-type = ["cdylib", "rlib"]

[profile.release]
lto = false
codegen-units = 16

[profile.release.package.{proj_name}]
incremental = true

[profile.dist]
inherits = "release"
lto = true
incremental = false
codegen-units = 1
strip = true

[profile.dist.package.{proj_name}]
incremental = false

[dependencies]
pyo3 = ">=0.28"

[features]
extension-module = ["pyo3/extension-module"]
"""

def _template_rs_lib()->str:
    return _RS_HELLO + """
use pyo3::prelude::*;

#[pyfunction(name = "hello")]
fn py_hello(name: &str) -> String {
    hello(name)
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_hello, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
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

def _template_rs_readme(proj_name:str, test:str, build:str)->str:
    return _tag_readme(proj_name, "PyO3/maturin package scaffolded by fastship.", test,
        "The workflow builds the wheels and sdist and publishes them to GitHub and PyPI.", build=build)

def _template_rs_dev(test:str, build:str)->str:
    return _md("Development", "", {
        "Commands": _bash(test, build),
        "Versioning": 'The canonical version lives in `Cargo.toml`. `pyproject.toml` gets the Python package version from Cargo via `dynamic = ["version"]`.',
        "Build profiles": "uv builds and `maturin develop --release` use the incremental `release` profile for fast local iteration. CI builds distributed wheels with `dist`, which enables full LTO and one codegen unit, disables incremental compilation, and strips the result.",
        "Release": f"1. Run `{test}`.\n2. Confirm the release version in `Cargo.toml` (`[package].version`).\n3. Run `ship-release`.\n\n{_TAG_RELEASE}"})

def _template_rs_workflow()->str:
    return _workflow("""  test:
    runs-on: ubuntu-latest
    steps:
      - uses: %(checkout)s
      - uses: %(rust)s
      - uses: %(setup_python)s
        with:
          python-version: '3.12'
      - run: pip install -e '.[dev]'
      - run: pytest -q

  build:
    needs: test
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: %(checkout)s
      - uses: %(maturin)s
        with:
          args: --profile dist --out dist -i python3.10 -i python3.11 -i python3.12 -i python3.13
          manylinux: auto
      - uses: %(upload)s
        with:
          name: wheels-${{ matrix.os }}
          path: dist

  sdist:
    runs-on: ubuntu-latest
    steps:
      - uses: %(checkout)s
      - uses: %(maturin)s
        with:
          command: sdist
          args: -o dist
      - uses: %(upload)s
        with:
          name: wheels-sdist
          path: dist
""", "[build, sdist]")

def _template_zig_pyproject(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
    return _pyproject(proj_name, desc, gh_org, _PY_CLASSIFIERS, ["fastship", "build", "cibuildwheel~=3.4", _ZIG_REQ, "pytest"],
        _setuptools_tables(pkg_name) + [f'[tool.setuptools.package-data]\n{pkg_name} = ["_lib/*"]',
            '[tool.cibuildwheel]\nbuild = "cp311-*"\nskip = "*-musllinux_* *-win32"\ntest-requires = "pytest"\ntest-command = "pytest {project}/tests"',
            '[tool.cibuildwheel.macos]\nenvironment = { MACOSX_DEPLOYMENT_TARGET = "13.0" }'],
        deps=["cffi"], requires=[_SETUPTOOLS_REQ, _ZIG_REQ], ship={"wheel-only": True})

def _template_zig_setup()->str:
    return """import subprocess,sys
from pathlib import Path
from setuptools import Distribution,setup
from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel

class BinaryDistribution(Distribution):
    def has_ext_modules(self): return True

class BinaryWheel(_bdist_wheel):
    def get_tag(self):
        _,_,plat = super().get_tag()
        return 'py3','none',plat

    def run(self):
        subprocess.run([sys.executable, str(Path(__file__).with_name('build_lib.py'))], check=True)
        super().run()

setup(cmdclass={'bdist_wheel': BinaryWheel}, distclass=BinaryDistribution)
"""

def _template_zig_libpath(pkg_name:str)->str:
    return """import sys
from pathlib import Path

LIB_PATH = Path(__file__).parent/"_lib"/{"darwin": "lib%(pkg)s.dylib", "win32": "%(pkg)s.dll"}.get(sys.platform, "lib%(pkg)s.so")
""" % dict(pkg=pkg_name)

def _template_zig_build(pkg_name:str)->str:
    return """import importlib.metadata,runpy,subprocess,sys,tomllib
from pathlib import Path

ROOT = Path(__file__).parent

def main():
    with open(ROOT/"pyproject.toml", "rb") as f: reqs = tomllib.load(f)["build-system"]["requires"]
    version = next(o.split("==")[1] for o in reqs if o.startswith("ziglang=="))
    if (found := importlib.metadata.version("ziglang")) != version: sys.exit(f"ziglang is {found}; expected {version}")
    out = runpy.run_path(str(ROOT/"%s"/"_libpath.py"))["LIB_PATH"]
    out.parent.mkdir(exist_ok=True)
    subprocess.run([sys.executable, "-m", "ziglang", "build-lib", "src/lib.zig", "-dynamic", "-O", "ReleaseFast", f"-femit-bin={out}"], cwd=ROOT, check=True)
    print(f"Bundled: {out.name}")

if __name__ == "__main__": main()
""" % pkg_name

def _template_zig_lib()->str:
    return """export fn add(a: c_int, b: c_int) c_int {
    return a + b;
}
"""

def _template_zig_ffi()->str:
    return """from cffi import FFI
from ._libpath import LIB_PATH

ffi = FFI()
ffi.cdef("int add(int a, int b);")
lib = ffi.dlopen(str(LIB_PATH))

def add(a, b): return lib.add(a, b)
"""

def _template_zig_init()->str:
    return f"""__version__ = "{_NEW_VERSION}"

from ._ffi import add

__all__ = ["add"]
"""

def _template_zig_test(pkg_name:str)->str:
    return f"""from {pkg_name} import add

def test_add(): assert add(2, 3) == 5
"""

def _template_zig_readme(proj_name:str, test:str, build:str)->str:
    return _tag_readme(proj_name, "Python CFFI bindings over a bundled Zig shared library, scaffolded by fastship.", test,
        "The workflow builds one Python-ABI-independent wheel for each supported platform and publishes the wheels to GitHub and PyPI.", build=build)

def _template_zig_dev(test:str, build:str)->str:
    return _md("Development", "`src/lib.zig` exports the C ABI consumed by the CFFI declarations in the Python package. `build_lib.py` compiles the shared library to the path in `_libpath.py`, which the package loads.",
        {"Commands": _bash(test, build), "Release": _TAG_RELEASE})

def _template_zig_workflow()->str:
    return _workflow("""  build:
    strategy:
      fail-fast: false
      matrix:
        include:
          - name: linux-x86_64
            os: ubuntu-latest
            arch: x86_64
          - name: linux-aarch64
            os: ubuntu-24.04-arm
            arch: aarch64
          - name: macos-arm64
            os: macos-latest
            arch: arm64
          - name: macos-x86_64
            os: macos-15-intel
            arch: x86_64
    runs-on: ${{ matrix.os }}
    steps:
      - uses: %(checkout)s
      - uses: %(cibuildwheel)s
        env:
          CIBW_ARCHS: ${{ matrix.arch }}
        with:
          output-dir: wheelhouse
      - uses: %(upload)s
        with:
          name: wheels-${{ matrix.name }}
          path: wheelhouse/*.whl
""", "build")

@call_parse
def ship_zig_new(
    name: str,              # Project name (PyPI name), e.g. "my-project"
    package: str = None,    # Python package import name (defaults from `name`)
    description: str = "A Zig-backed Python package",  # Short project description
    path: str = ".",        # Directory to create the project folder in
    gh_org: str = _GH_ORG,  # GitHub organization for project.urls
    force: bool = False,    # Overwrite if the folder already exists
):
    "Create a CFFI/Zig project with platform wheels and trusted tag publishing."
    proj, pkg = _names(name, package)
    test, build = "python build_lib.py && pytest -q", "ship-zig-build"
    root = _scaffold(Path(path)/proj, {
        "pyproject.toml": _template_zig_pyproject(proj, pkg, description, gh_org),
        "setup.py": _template_zig_setup(),
        "build_lib.py": _template_zig_build(pkg),
        "src/lib.zig": _template_zig_lib(),
        f"{pkg}/__init__.py": _template_zig_init(),
        f"{pkg}/_libpath.py": _template_zig_libpath(pkg),
        f"{pkg}/_ffi.py": _template_zig_ffi(),
        "tests/test_basic.py": _template_zig_test(pkg),
        "README.md": _template_zig_readme(proj, test, build),
        "DEV.md": _template_zig_dev(test, build),
        "MANIFEST.in": _template_manifest("include build_lib.py", "include setup.py", "recursive-include src *.zig", f"recursive-exclude {pkg}/_lib *"),
        ".github/workflows/ci.yml": _template_zig_workflow()}, force)
    return _created(root, _PIP_DEV, test)

@call_parse
def ship_rs_new(
    name: str,              # Project name (PyPI/Cargo name), e.g. "my-project"
    package: str = None,    # Python package import name, e.g. "my_project" (defaults from `name`)
    description: str = "A PyO3 package",  # Short project description
    path: str = ".",        # Directory to create the project folder in
    gh_org: str = _GH_ORG,  # GitHub organization for project.urls
    force: bool = False,    # Overwrite if the folder already exists
):
    "Create a maturin/PyO3 project wired for fastship Rust commands."
    proj, pkg = _names(name, package)
    test, build = "maturin develop && pytest -q", "ship-rs-build"
    root = _scaffold(Path(path)/proj, {
        "pyproject.toml": _template_rs_pyproject(proj, pkg, description, gh_org),
        "Cargo.toml": _template_cargo_toml(proj, pkg, description, gh_org),
        "src/lib.rs": _template_rs_lib(),
        f"python/{pkg}/__init__.py": _template_rs_init(),
        "tests/test_basic.py": _template_rs_test(pkg),
        "README.md": _template_rs_readme(proj, test, build),
        "DEV.md": _template_rs_dev(test, build),
        ".github/workflows/ci.yml": _template_rs_workflow()}, force)
    return _created(root, _PIP_DEV, test)


def _template_crate_lib()->str:
    return _RS_HELLO + """
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hello_works() {
        assert_eq!(hello("fastship"), "Hello, fastship!");
    }
}
"""

def _template_crate_readme(proj_name:str, desc:str, test:str)->str:
    return _tag_readme(proj_name, desc, test, "The workflow publishes the crate to crates.io and creates the GitHub release. "
        "For the first release, publish manually with a token (`cargo publish`), then add this repo's `ci.yml` as a trusted publisher in the crate's crates.io settings. "
        "Later tags publish through CI without a token.", setup=())

def _template_crate_workflow()->str:
    return _workflow("""  test:
    runs-on: ubuntu-latest
    steps:
      - uses: %(checkout)s
      - uses: %(rust)s
      - run: cargo test
""", "test", """      - uses: %(checkout)s
      - uses: %(rust)s
      - id: auth
        uses: %(crates_auth)s
      - run: cargo publish
        env:
          CARGO_REGISTRY_TOKEN: ${{ steps.auth.outputs.token }}
      - uses: %(gh_release)s
        with:
          generate_release_notes: true
""")

@call_parse
def ship_crate_new(
    name: str,              # Crate name (crates.io name), e.g. "my-crate"
    description: str = "A Rust crate",  # Short crate description
    path: str = ".",        # Directory to create the project folder in
    gh_org: str = _GH_ORG,  # GitHub organization for [package].repository
    force: bool = False,    # Overwrite if the folder already exists
):
    "Create a pure-Rust crate wired for fastship tag releases and crates.io trusted publishing."
    proj, _ = _names(name)
    test = "cargo test"
    root = _scaffold(Path(path)/proj, {
        "Cargo.toml": _cargo_package(proj, description, gh_org) + "\n\n[dependencies]\n",
        "src/lib.rs": _template_crate_lib(),
        "README.md": _template_crate_readme(proj, description, test),
        ".github/workflows/ci.yml": _template_crate_workflow()}, force, site=False)
    return _created(root, test)


@call_parse
def ship_new(
    name: str,              # Project name (PyPI name), e.g. "my-project"
    package: str = None,    # Python package import name, e.g. "my_project" (defaults from `name`)
    description: str = "A Python package",  # Short project description
    path: str = ".",        # Directory to create the project folder in
    gh_org: str = _GH_ORG,  # GitHub organization for project.urls
    force: bool = False,    # Overwrite if the folder already exists
):
    "Create a modern setuptools project wired for fastship."
    proj, pkg = _names(name, package)
    root = _scaffold(Path(path)/proj, {
        "pyproject.toml": _template_pyproject(proj, pkg, description, gh_org),
        "README.md": _template_readme(proj, pkg),
        "CHANGELOG.md": _NEW_CHANGELOG,
        "MANIFEST.in": _template_manifest("include CHANGELOG.md"),
        f"{pkg}/__init__.py": f'__version__ = "{_NEW_VERSION}"\n'}, force)
    return _created(root, _PIP_DEV)


# ---------------------------------------------------------------------------
# Quick PR workflow
# ---------------------------------------------------------------------------

@call_parse
async def ship_pr(
    title: str,             # PR title (also used for commit message if needed)
    branch: str = None,     # Branch name (auto-generated from title if not provided)
    label: str = "enhancement",  # GitHub label for the PR
    body: str = "",         # PR body text, path to a file containing it, or '-' to read from stdin
    token: str = None,      # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,       # Override repo ("OWNER/REPO")
    path: str = ".",        # Repo directory; the default is the current directory
):
    "Create a PR from uncommitted/unpushed work, merge it, and clean up."
    g = Git(path, raise_exc=True)
    if not g.exists: raise CliError("Not a git repository")

    try: default = g.remote('show', 'origin').split("HEAD branch:")[1].split()[0]
    except Exception: default = "main"

    current = g.branch(show_current=True).strip()
    if current != default: raise CliError(f"Must be on {default} branch (currently on {current})")

    g.fetch('origin')
    try: behind = bool(g.log(f'HEAD..origin/{default}', oneline=True).strip())
    except Exception: behind = False
    if behind: raise CliError(f"Local {default} is behind origin. Run: git pull")

    try: has_commits = bool(g.log(f'origin/{default}..HEAD', oneline=True).strip())
    except Exception: has_commits = False
    has_changes = bool(g.status(porcelain=True))
    if not has_commits and not has_changes: raise CliError("Nothing to PR: no unpushed commits and no uncommitted changes")

    slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')[:50]
    if len(slug) == 50: slug = slug.rsplit('-', 1)[0]
    pr_branch = branch or f"pr/{slug}"
    g.switch('-c', pr_branch)

    try:
        if has_changes: g.commit('-am', title)
        g.push('-u', 'origin', pr_branch)

        gh = _gh_api(repo=repo, token=token, root=Path(path))
        if body == '-': pr_body = sys.stdin.read().strip()
        else: pr_body = Path(body).read_text().strip() if body and '\n' not in body and os.path.exists(body) else body
        pr = await gh.pulls.create(title=title, head=pr_branch, base=default, body=pr_body)
        print(f"Created PR #{pr.number}: {pr.html_url}")

        await gh.issues.add_labels(pr.number, labels=[label])

        await gh.pulls.merge(pr.number, merge_method="squash", commit_title=title)
        print(f"Merged PR #{pr.number}")

        try: await gh.git.delete_ref(f"heads/{pr_branch}")
        except Exception: pass

    finally: g.switch(default)

    g.fetch('origin')
    g.reset('--hard', f'origin/{default}')
    g.branch('-D', pr_branch)
    print(f"Done! {default} updated to include squashed commit.")
