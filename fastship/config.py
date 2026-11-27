"Core project configuration and version bumping for `fastship`."

__all__ = [
    "GH_HOST", "DEFAULT_LABEL_GROUPS", "PYPROJ", "INIT_PY", "RE_VERSION_ANY",
    "PyprojectConfig", "ShipConfig", "RustConfig", "NpmConfig", "CrateConfig",
    "find_marker", "find_pyproject", "find_project", "project_root", "ship_cfg",
    "is_uv_workspace", "is_maturin_project", "project_type", "norm_mod",
    "find_pkg_path", "gh_only", "is_nbdev_py", "git_cmd", "git_has_changes", "branch",
    "parse_repo", "gh_url", "pages_url", "q", "write_lines", "fmt_toml_val",
    "toml_section_bounds", "replace_toml_section_key", "ensure_toml_section",
    "get_config", "get_rs_config", "get_npm_config", "get_crate_config",
    "read_version", "set_version", "bump_version", "version_copies",
    "write_npm_version", "cargo_bump", "ship_bump", "ship_rs_bump",
]

import os, re, shlex
from dataclasses import dataclass, field

from fastcore.all import *  # Path, nested_idx, partial, run, repo_details, call_parse, CliError
from fastcore.project import *
from fastcore.project import find_pkg_path as _fc_find_pkg_path

GH_HOST = "https://api.github.com"
DEFAULT_LABEL_GROUPS = dict(breaking="Breaking Changes", enhancement="New Features", bug="Bugs Squashed")
PYPROJ = "pyproject.toml"
INIT_PY = "__init__.py"
RE_VERSION_ANY = re.compile(r"^__version__\s*=.*$", re.MULTILINE)
_MARKER_TYPES = {PYPROJ: "py", "package.json": "npm", "Cargo.toml": "crate"}


def q(s) -> str: return shlex.quote(str(s))

def write_lines(p: Path, lines: list[str]): p.write_text("\n".join(lines) + "\n", encoding="utf-8")

def ship_cfg(data: dict) -> dict: return nested_idx(data, "tool", "fastship") or {}

def gh_url(org: str, proj: str) -> str: return f"https://github.com/{org}/{proj}"

def pages_url(org: str, proj: str) -> str: return f"https://{org}.github.io/{proj}/"


def find_project(start: Path | None = None) -> tuple[str, Path]:
    "Nearest project marker up the tree: ('py', pyproject.toml), ('npm', package.json), or ('crate', Cargo.toml), in that priority order."
    p = find_marker(start, *_MARKER_TYPES)
    return _MARKER_TYPES[p.name], p


def find_pkg_path(root: Path, data: dict) -> Path:
    "Locate the package directory under `root`, honoring `[tool.fastship].package`."
    return _fc_find_pkg_path(root, data, pkg=ship_cfg(data).get("package"))


def gh_only(root: Path, data: dict) -> bool:
    "True when the project has a static `[project].version` and no package to build. Such a project is released on GitHub only."
    if nested_idx(data, "project", "version") is None or ship_cfg(data).get("package"): return False
    try: find_pkg_path(root, data)
    except FileNotFoundError: return True
    return False


def is_maturin_project(data: dict) -> bool:
    bs = data.get("build-system") or {}
    return "maturin" in (bs.get("build-backend") or "") or any("maturin" in r for r in bs.get("requires") or ()) or bool(nested_idx(data, "tool", "maturin"))


def project_type(root: Path, data: dict) -> str:
    if (root/"Cargo.toml").exists() or is_maturin_project(data): return "rust"
    reqs = nested_idx(data, "build-system", "requires") or []
    if any(re.match(r"^ziglang(?:\W|$)", o) for o in reqs): return "zig"
    return "python"


def is_nbdev_py(root: Path, data: dict) -> bool:
    "True for nbdev Python projects whose version lives in `__init__.py`."
    return (nested_idx(data, "project", "version") is None and nested_idx(data, "tool", "nbdev") is not None
        and project_type(root, data) == "python" and not gh_only(root, data))


def git_cmd(cmd: str, default: str = "", path: Path = None) -> str:
    "Run `git {cmd}` in `path` (default: the current directory) and return its stripped output, or `default` when git fails or prints nothing."
    try: return run(f"git -C {q(path)} {cmd}" if path else f"git {cmd}").strip() or default
    except Exception: return default


def git_has_changes() -> bool:
    "Return `True` if the current git worktree has staged or unstaged changes."
    return bool(run("git status --porcelain --untracked-files=no").strip())


def branch(ship: dict = None) -> str:
    "Return `[tool.fastship].branch`, else `$FASTSHIP_BRANCH`, else the current git branch, else `main`."
    return (ship or {}).get("branch") or os.getenv("FASTSHIP_BRANCH") or git_cmd("branch --show-current", "main")


def parse_repo(repo: str = None, path: Path = None) -> tuple[str | None, str | None]:
    "Parse 'OWNER/REPO' string, falling back to the git origin in `path`."
    if repo and "/" in repo: return repo.split("/", 1)
    try: g_owner, g_repo = repo_details(git_cmd("config --get remote.origin.url", path=path))
    except Exception: g_owner = g_repo = None
    return g_owner, repo or g_repo


# ---------------------------------------------------------------------------
# Config dataclasses + loaders
# ---------------------------------------------------------------------------

def _version_files(root: Path, ship: dict) -> list[Path]:
    "Synchronized version copies from `[tool.fastship].version-files`, resolved against `root`."
    files = ship.get("version-files") or []
    if not isinstance(files, list) or not all(isinstance(o, str) for o in files):
        raise ValueError("[tool.fastship].version-files must be a list of paths")
    return [root/o for o in files]


def _cargo_version_section(manifest: Path) -> str:
    "The version section in Cargo.toml: package, inherited workspace, or virtual workspace."
    data = manifest.read_toml()
    ver = (data.get("package") or {}).get("version")
    inherited = isinstance(ver, dict) and ver.get("workspace") is True
    virtual = "workspace" in data and "package" not in data
    return "workspace.package" if inherited or virtual else "package"


def _cargo_version(manifest: Path) -> str:
    "Read the version from Cargo.toml, following `version.workspace = true` to `[workspace.package]`."
    sec = _cargo_version_section(manifest)
    ver = nested_idx(manifest.read_toml(), *sec.split("."), "version")
    if not isinstance(ver, str) or not ver: raise ValueError(f"Could not find [{sec}].version in {manifest}")
    return ver


def _read_version(init_file: Path) -> str:
    if not init_file.exists(): raise FileNotFoundError(f"Missing {init_file}")
    if (v := read_version(init_file)) is None: raise ValueError(f'Could not find __version__ = "..." in {init_file}')
    return v


@dataclass
class PyprojectConfig:
    root: Path
    pyproject: Path
    data: dict
    branch: str
    changelog_file: Path
    version_files: list[Path]


@dataclass
class ShipConfig(PyprojectConfig):
    pkg: str | None
    pkg_path: Path | None
    init_file: Path | None
    wheel_only: bool

    @property
    def version(self) -> str: return nested_idx(self.pyproject.read_toml(), "project", "version") or _read_version(self.init_file)

    @property
    def gh_only(self) -> bool: return self.pkg is None


@dataclass
class RustConfig(PyprojectConfig):
    manifest_path: Path

    @property
    def version(self) -> str: return _cargo_version(self.manifest_path)


def _pyproject_settings(pyproj: Path, data: dict) -> dict:
    "Fields shared by `ShipConfig` and `RustConfig`, read from `pyproj` and its `[tool.fastship]` table."
    root, ship = pyproj.parent, ship_cfg(data)
    return dict(root=root, pyproject=pyproj, data=data, branch=branch(ship),
        changelog_file=root/ship.get("changelog_file", "CHANGELOG.md"), version_files=_version_files(root, ship))


def get_config(start: str | Path | None = None) -> ShipConfig:
    "Load fastship config from `pyproject.toml`."
    pyproj = find_pyproject(start)
    root, data = pyproj.parent, pyproj.read_toml()
    pkg = pkg_path = init_file = None
    if gh_only(root, data):
        if is_uv_workspace(data): raise CliError(f"{pyproj} is a uv workspace root. It has no package to release.")
    else:
        pkg_path = find_pkg_path(root, data)
        pkg, init_file = pkg_path.name, pkg_path/INIT_PY
    ship = ship_cfg(data)
    if ship.get("release") not in (None, "tag"): raise ValueError('[tool.fastship].release must be "tag" when set')
    return ShipConfig(pkg=pkg, pkg_path=pkg_path, init_file=init_file, wheel_only=ship.get("wheel-only", False), **_pyproject_settings(pyproj, data))


def get_rs_config(start: str | Path | None = None) -> RustConfig:
    "Load fastship config for a maturin/PyO3 project."
    pyproj = find_pyproject(start)
    data = pyproj.read_toml()
    proj = data.get("project") or {}
    if proj.get("version") is not None: raise ValueError(f'{pyproj} must use Cargo.toml for ship-rs versions; remove [project].version')
    dyn = proj.get("dynamic") or []
    if not isinstance(dyn, list) or "version" not in dyn: raise ValueError(f'{pyproj} must set [project].dynamic = ["version"] for ship-rs commands')
    manifest_path = pyproj.parent / (nested_idx(ship_cfg(data), "rs", "manifest_path") or "Cargo.toml")
    return RustConfig(manifest_path=manifest_path, **_pyproject_settings(pyproj, data))


@dataclass
class NpmConfig:
    root: Path
    pkg_json: Path
    name: str
    branch: str

    @property
    def version(self) -> str:
        v = self.pkg_json.read_json().get("version")
        if not v: raise ValueError(f"No version field found in {self.pkg_json}")
        return v


def get_npm_config(start: str | Path | None = None) -> NpmConfig:
    "Load fastship config for an npm (package.json, no pyproject.toml) project."
    pkg_json = find_marker(start, "package.json")
    return NpmConfig(root=pkg_json.parent, pkg_json=pkg_json, name=pkg_json.read_json().get("name", ""), branch=branch())


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
    manifest = find_marker(start, "Cargo.toml")
    name = nested_idx(manifest.read_toml(), "package", "name") or ""
    return CrateConfig(root=manifest.parent, manifest_path=manifest, name=name, branch=branch())


# ---------------------------------------------------------------------------
# Version writing + bump
# ---------------------------------------------------------------------------

def _write_config_version(cfg: ShipConfig, version: str):
    "Write `version` to `[project].version` when the pyproject sets it statically, else to `__version__` in the package's `__init__.py`."
    if nested_idx(cfg.pyproject.read_toml(), "project", "version") is not None: replace_toml_section_key(cfg.pyproject, "project", "version", version)
    else: set_version(cfg.init_file, version)


def write_npm_version(pkg_json: Path, version: str):
    "Rewrite the `version` field in place, preserving the file's formatting."
    txt = pkg_json.read_text(encoding="utf-8")
    new, n = re.subn(r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', txt, count=1)
    if not n: raise ValueError(f"No version field found in {pkg_json}")
    pkg_json.write_text(new, encoding="utf-8")


def version_copies(old: str, version_files: list[Path]) -> dict[Path, str]:
    "Read synchronized copies, validating every file before any version is changed."
    copies = {p: p.read_text(encoding="utf-8") for p in version_files}
    for p, text in copies.items():
        if text.count(old) != 1: raise ValueError(f"Expected exactly one {old!r} in {p}")
    return copies


def _bump(old: str, write, version_files: list[Path] = (), part: int = None, unbump: bool = False) -> str:
    "Bump `old`, write the new version with `write`, and replace `old` in each of `version_files`, printing both versions."
    print(f"Old version: {old}")
    new = bump_version(old, part=part, unbump=unbump)
    copies = version_copies(old, version_files)
    write(new)
    for p, text in copies.items(): p.write_text(text.replace(old, new), encoding="utf-8")
    print(f"New version: {new}")
    return new


def cargo_bump(cfg, part: int = None, unbump: bool = False):
    "Bump the version in Cargo.toml (`[package]`, or `[workspace.package]` when inherited), printing old and new."
    write = partial(replace_toml_section_key, cfg.manifest_path, _cargo_version_section(cfg.manifest_path), "version")
    return _bump(cfg.version, write, cfg.version_files, part, unbump)


def _npm_bump(part: int = None, unbump: bool = False):
    cfg = get_npm_config()
    return _bump(cfg.version, partial(write_npm_version, cfg.pkg_json), part=part, unbump=unbump)


def ship_rs_bump(part: int = 2, unbump: bool = False):
    "Bump the version in Cargo.toml (`[package]`, or `[workspace.package]` when inherited)."
    cargo_bump(get_rs_config(), part=part, unbump=unbump)


@call_parse
def ship_bump(
    part: int = None,  # Release part to bump; defaults to post when present, otherwise patch (2)
    unbump: bool = False,  # Reduce version instead of increasing it
):
    "Bump Cargo.toml for Rust, package.json for npm, or the Python version source; export nbdev notebooks afterward."
    ftype, pyproj = find_project()
    if ftype == "npm": _npm_bump(part=part, unbump=unbump)
    elif ftype == "crate": cargo_bump(get_crate_config(), part=part, unbump=unbump)
    elif project_type(pyproj.parent, (data := pyproj.read_toml())) == "rust": ship_rs_bump(part=part, unbump=unbump)
    else:
        cfg = get_config()
        _bump(cfg.version, partial(_write_config_version, cfg), cfg.version_files, part, unbump)
        if is_nbdev_py(pyproj.parent, data): run("nbdev-export")
