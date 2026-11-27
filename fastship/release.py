"""Local-first release helpers for non-nbdev Python projects.

`fastship` is inspired by the tight, simple workflow in `nbdev.release`:
bump a plain `__version__`, upload with `python -m build` + `twine`,
and create GitHub releases directly via `ghapi` (no GitHub Actions required).
"""


__all__ = ["GH_HOST", "DEFAULT_LABEL_GROUPS", "ShipConfig", "RustConfig", "NpmConfig", "CrateConfig", "get_config", "get_rs_config", "get_npm_config", "get_crate_config", "bump_version", "Release",
    "ship_bump", "ship_pages", "ship_llms", "ship_pypi", "ship_changelog", "ship_release_gh", "ship_release", "ship_new", "ship_pr",
    "ship_rs_new", "ship_rs_init", "ship_rs_build", "ship_rs_bump", "ship_npm_init", "ship_zig_new", "ship_zig_build"]

import os, re, sys, shutil, subprocess, importlib.resources
import yaml

from fastcore.all import *  # Path, nested_idx, run, call_parse, CliError, ...
from fastgit import Git
from ghapi.core import GhApi, APIError
import nbdev.config, nbdev.release

from fastship.config import *
from fastship.config import branch as default_branch
from fastship.docs import refresh_llms, ship_llms

_NEW_VERSION = "0.1.0"
_GH_ORG = "AnswerDotAI"
_ZIG_REQ = "ziglang==0.16.0"
_MATURIN_REQ = "maturin>=1.13,<2.0"
_FASTWS_REQ = "fastws-cli>=0.0.42"
_RS_DEV_DEPS = ["fastship[all]", _MATURIN_REQ, _FASTWS_REQ, "pytest"]
_BUMP_COMMIT = 'git commit -am "bump [skip ci]"'


def _load_release_yml(root: Path) -> dict | None:
    "Load label groups from .github/release.yml if it exists."
    for name in ("release.yml", "release.yaml"):
        p = root / ".github" / name
        if p.exists():
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




def _get_token(root: Path = None) -> str | None:
    "Find GitHub token from env vars or token file."
    token = os.getenv("FASTSHIP_TOKEN")
    if not token and root and (root / "token").exists(): token = (root / "token").read_text().strip()
    if not token and Path("token").exists(): token = Path("token").read_text().strip()
    return token or os.getenv("GITHUB_TOKEN")


def _gh_api(owner:str = None, repo:str = None, token:str = None, root:Path = None):
    "Return a `GhApi` for `owner`/`repo`, authenticated with `token`, else `_get_token(root)`. Without `owner`, `parse_repo` reads the owner from `repo` or from the git origin in `root`."
    if root: root = root.expanduser()
    if not owner: owner, repo = parse_repo(repo, root)
    if not owner or not repo: raise CliError("Could not infer GitHub owner/repo. Pass --repo OWNER/REPO or set a git remote `origin`.")
    token = token or _get_token(root)
    if not token: raise CliError("Failed to find token (FASTSHIP_TOKEN, GITHUB_TOKEN, or a ./token file)")
    return GhApi(owner, repo, token)



def _ensure_project_dynamic_version(p:Path):
    "Make `[project]` use maturin's Cargo.toml-derived version."
    data = p.read_toml()
    proj = data.get("project") or {}
    dyn = proj.get("dynamic") or []
    if not isinstance(dyn, list): raise ValueError(f"{p} [project].dynamic must be a list")
    if "version" not in dyn: dyn.append("version")

    lines = p.read_text(encoding="utf-8").splitlines()
    start, end = toml_section_bounds(lines, "project")
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
    val = f"dynamic = {fmt_toml_val(dyn)}"
    if dynamic_range is not None: lines[dynamic_range[0]:dynamic_range[1]] = [val]
    else: lines.insert((name_idx or start) + 1, val)
    write_lines(p, lines)


def _rs_module_name(data:dict) -> str | None:
    mod = nested_idx(data, "tool", "maturin", "module-name") or nested_idx(data, "project", "name")
    return norm_mod(mod.rsplit(".", 1)[-1]) if mod else None


def _warn(msg:str): print(f"ship-rs-init: warning: {msg}", file=sys.stderr)


def _ensure_rs_runtime_version(root:Path, data:dict):
    "Expose Cargo.toml's version as `__version__` in the PyO3 module, in the crate that `[tool.maturin].manifest-path` names."
    mod = _rs_module_name(data)
    if not mod:
        _warn("could not infer PyO3 module name; skipped Rust __version__ export")
        return
    pat = re.compile(rf"^\s*fn\s+{re.escape(mod)}\s*\(\s*(\w+)\s*:", re.MULTILINE)
    found = False
    for p in (root / Path(nested_idx(data, "tool", "maturin", "manifest-path") or "Cargo.toml").parent / "src").rglob("*.rs"):
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
    init = root / (nested_idx(data, "tool", "maturin", "python-source") or "python") / pkg / INIT_PY
    if not init.exists(): return
    txt = init.read_text(encoding="utf-8")
    if not RE_VERSION_ANY.search(txt): return

    lines = [ln for ln in txt.splitlines() if not RE_VERSION_ANY.match(ln)]
    import_pat = re.compile(rf"^from\s+({re.escape(pkg + '.' + ext)}|\.{re.escape(ext)})\s+import\s+(.+)$")
    for i, ln in enumerate(lines):
        m = import_pat.match(ln)
        if not m: continue
        names = [o.strip() for o in m.group(2).split(",")]
        if "__version__" not in names: names.insert(0, "__version__")
        lines[i] = f"from {m.group(1)} import {', '.join(names)}"
        write_lines(init, lines)
        return
    _warn(f"found literal __version__ in {init} but could not find a simple import from `{pkg}.{ext}`; left Python wrapper unchanged")


# ---------------------------------------------------------------------------
# GitHub release notes (changelog from issues) + release creation
# ---------------------------------------------------------------------------

class Release(nbdev.release.Release):
    "nbdev's `Release`, configured from `ShipConfig` instead of nbdev's settings, and releasing to the configured branch."
    def __init__(self, owner=None, repo=None, token=None, cfg: ShipConfig | None = None, **groups):
        self.cfg = cfg or get_config()
        self.changefile = self.cfg.changelog_file
        self.groups = groups or _load_release_yml(self.cfg.root) or ship_cfg(self.cfg.data).get("label_groups") or DEFAULT_LABEL_GROUPS
        os.chdir(self.cfg.root)
        self.gh = _gh_api(owner, repo, token, self.cfg.root)

    async def release(self):
        "Tag and create a release in GitHub for the current version, on `cfg.branch`."
        notes = dict(generate_release_notes=True) if self.cfg.gh_only else dict(body=self.latest_notes())
        await self.gh.create_release(self.cfg.version, branch=self.cfg.branch, **notes)
        return self


# ---------------------------------------------------------------------------
# CLI entrypoints
# ---------------------------------------------------------------------------


def _nbdev_release():
    "Return `nbdev.release` for nbdev Python projects whose version lives in `__init__.py`."
    ftype, pyproj = find_project()
    if ftype != "py": return None
    return nbdev.release if is_nbdev_py(pyproj.parent, pyproj.read_toml()) else None

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
    if git_has_changes(): run("git commit -am release")
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
    "Configure GitHub Pages Actions docs workflow (`.github/workflows/docs.yml`), remove legacy Jekyll files, and enable Pages."
    root, data, pyproj = project_root()
    skip_install = not data or nested_idx(data, "tool", "maturin", "bindings") == "bin" or gh_only(root, data)
    rust = not skip_install and project_type(root, data) == "rust"
    backend = nested_idx(data, "build-system", "build-backend") or ""
    install = "" if skip_install else (_fastws_docs_install(nested_idx(data, "project", "name") or root.name) if rust and "fastws.build_backend" in backend else None)
    wf = root/".github"/"workflows"/"docs.yml"
    if not wf.exists() and not (root/".github"/"workflows"/"docs.yaml").exists(): _write(wf, _template_docs_workflow(rust=rust, install=install))
    if (root/"_config.yml").exists(): (root/"_config.yml").unlink()
    if (root/"_layouts").is_dir(): shutil.rmtree(root/"_layouts")
    gh = _gh_api(token=token)
    try: pages = await gh.repos.create_pages_site(build_type="workflow")
    except APIError as e:
        if e.status_code != 409: raise
        await gh.repos.update_information_about_pages_site(build_type="workflow")
        pages = await gh.repos.get_pages()
    await gh.repos.update(homepage=pages.html_url)
    if pyproj is not None: ensure_toml_section(pyproj, "project.urls", {"Documentation": pages.html_url.rstrip("/") + "/"})
    print(f"GitHub Pages enabled: {pages.html_url}")
    return pages.html_url


def _refresh_llms(root: Path, data: dict) -> list[Path]:
    return (nbdev.config.update_llms_txt(root) or []) if nested_idx(data, "tool", "nbdev") is not None else refresh_llms(root, data)


def _stage_llms(root: Path, paths: list[Path]):
    if not paths: return None, []
    g, rels = Git(root, raise_exc=True), [str(p.resolve().relative_to(root.resolve())) for p in paths]
    g.add("-A", "--", *rels)
    return g, rels


def _commit_llms(root: Path, paths: list[Path], msg: str = "update llms"):
    g, rels = _stage_llms(root, paths)
    if rels and g.diff("--cached", "--name-only", "--", *rels).strip(): g.commit("--only", "-m", msg, "--", *rels)

@call_parse
async def ship_changelog(
    token: str = None,  # GitHub token (FASTSHIP_TOKEN/GITHUB_TOKEN/token file used otherwise)
    repo: str = None,   # Override repo ("OWNER/REPO")
):
    "Create/update CHANGELOG.md from closed GitHub issues (without opening editor or releasing)."
    if (nbr := _nbdev_release()): return await nbr.changelog(repo=repo, token=token)
    rel = Release(repo=repo, token=token)
    await rel.changelog()
    print(f"Updated {rel.changefile}")


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



def _bump_and_push():
    "Bump the version, then commit and push it with a message that skips GitHub Actions `push` workflows."
    ship_bump()
    run(_BUMP_COMMIT)
    run("git push")


def _ship_tag_release(kind:str):
    "Tag `v<version>` and push branch + tag (CI builds, publishes, and writes release notes), then bump."
    cfg = dict(crate=get_crate_config, rust=get_rs_config, npm=get_npm_config).get(kind, get_config)()
    version_copies(cfg.version, getattr(cfg, "version_files", ()))
    os.chdir(cfg.root)
    if git_has_changes(): raise CliError("Uncommitted changes: commit or stash before releasing")
    if isinstance(cfg, PyprojectConfig) and (changed := _refresh_llms(cfg.root, cfg.data)):
        msg = "update llms" if nested_idx(cfg.data, "tool", "nbdev") is not None else "update llms [skip ci]"
        _commit_llms(cfg.root, changed, msg)
    version, tag = cfg.version, f"v{cfg.version}"
    run(f"git tag -a {q(tag)} -m {q(tag)}")
    run(f"git push origin {q(cfg.branch)}")
    run(f"git push origin {q(tag)}")
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
    ftype, proj = find_project()
    data = proj.read_toml() if ftype == "py" else {}
    kind = project_type(proj.parent, data) if ftype == "py" else ftype
    if ship_cfg(data).get("release") == "tag":
        _ship_tag_release(kind)
        return
    if _nbdev_release():
        _commit_llms(proj.parent, _refresh_llms(proj.parent, data))
        await ship_release_gh(token=token, repo=repo, no_changelog=no_changelog, no_editor=no_editor, yes=yes)
        ship_pypi(repository=repository, wheel_only=wheel_only, verbose=verbose)
        _bump_and_push()
        return
    if kind != "python":
        _ship_tag_release(kind)
        return
    rel = Release(repo=repo, token=token)
    await _prepare_release(rel, no_changelog=no_changelog, no_editor=no_editor, yes=yes)
    _stage_llms(rel.cfg.root, _refresh_llms(rel.cfg.root, rel.cfg.data))
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
    pyproj = find_pyproject()
    root, data = pyproj.parent, pyproj.read_toml()
    if not is_maturin_project(data): raise CliError(f"{pyproj} does not look like a maturin project")
    if not (root/"Cargo.toml").exists(): raise CliError(f"Missing {root/'Cargo.toml'}")
    _ensure_project_dynamic_version(pyproj)
    ensure_toml_section(pyproj, "project.optional-dependencies", dict(dev=_RS_DEV_DEPS))
    _ensure_rs_runtime_version(root, data)
    _ensure_py_runtime_version(root, data)
    ensure_toml_section(pyproj, "tool.fastship", dict(branch=branch or default_branch(ship_cfg(data))), replace=force)
    print(f"Updated {pyproj}")



@call_parse
def ship_npm_init(
    branch: str = None,   # Branch for [tool.fastship] (defaults to existing/current)
    force: bool = False,  # Overwrite an existing .github/workflows/ci.yml
):
    "Configure an existing Python+npm or npm-only project for tag-triggered OIDC releases via GitHub Actions."
    ftype, marker = find_project()
    root = marker.parent
    pkg_json = root/"package.json"
    if not pkg_json.exists(): raise CliError(f"Missing {pkg_json}")
    pkg = pkg_json.read_json()
    if not pkg.get("name") or not pkg.get("version"): raise CliError(f"{pkg_json} must define name and version")
    wf = root/".github/workflows/ci.yml"
    if wf.exists() and not force: raise CliError(f"{wf} already exists (use --force to overwrite)")
    is_py = ftype == "py"
    if is_py:
        data = marker.read_toml()
        kind = project_type(root, data)
        if kind != "python": raise CliError(f"{marker} is a {kind} project; ship-npm-init supports Python+npm and npm-only projects")
        cfg = get_config(root)
        if cfg.gh_only: raise CliError(f"{marker} has no Python package to publish")
        if pkg["version"] != cfg.version:
            write_npm_version(pkg_json, cfg.version)
            print(f"Synced {pkg_json} version: {pkg['version']} -> {cfg.version}")
        ship = ship_cfg(data)
        vf = list(ship.get("version-files") or [])
        if "package.json" not in vf: vf.append("package.json")
        ensure_toml_section(marker, "tool.fastship", {"branch": branch or default_branch(ship), "release": "tag", "version-files": vf}, replace=True)
        print(f"Updated {marker}")
    has_build = bool((pkg.get("scripts") or {}).get("build"))
    has_deps = bool(pkg.get("dependencies") or pkg.get("devDependencies"))
    _write(wf, _template_npm_workflow(py=is_py, has_build=has_build, has_deps=has_deps))
    print(f"Wrote {wf}")
    return wf



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
    options = ['--profile', q(profile)]
    if target: options += ['--target', q(target)]
    if args: options.append(args)
    config = q('maturin.build-args=' + ' '.join(options))
    run(f'{q(sys.executable)} -m build --wheel --outdir {q(outdir)} -C {config}')


@call_parse
def ship_zig_build(
    quiet: bool = False,  # Reduce output verbosity
):
    "Build and check the current platform wheel for a fastship Zig project."
    cfg = get_config()
    if project_type(cfg.root, cfg.data) != "zig": raise CliError("Not a fastship Zig project")
    _build_dist(cfg, wheel_only=True, quiet=quiet)






# ---------------------------------------------------------------------------
# Project scaffolding
# ---------------------------------------------------------------------------

_LICENSE = "Apache-2.0"
_SETUPTOOLS_REQ = "setuptools>=77"
_PIP_DEV = "pip install -e .[dev]"
_PY_CLASSIFIERS = ["Programming Language :: Python :: 3", "Programming Language :: Python :: 3 :: Only"]
_TAG_RELEASE = "`ship-release` pushes a `v<version>` tag, which starts the release workflow in GitHub Actions, then bumps the version."
_PY_VERSION = "3.13"
_NODE_VERSION = "lts/*"
_ACTIONS = dict(checkout="actions/checkout@v7", setup_python="actions/setup-python@v7", setup_node="actions/setup-node@v7",
    rust="dtolnay/rust-toolchain@stable", maturin="AnswerDotAI/workflows/maturin-build@master", cibuildwheel="pypa/cibuildwheel@v4.2.0",
    upload="actions/upload-artifact@v7", download="actions/download-artifact@v8", gh_release="softprops/action-gh-release@v3",
    pypi="pypa/gh-action-pypi-publish@release/v1", crates_auth="rust-lang/crates-io-auth-action@v1",
    setup_uv="AnswerDotAI/workflows/setup-uv@master", sccache="AnswerDotAI/workflows/sccache@master",
    fastship_docs="AnswerDotAI/workflows/fastship-docs@master")
_WORKFLOW_VARS = dict(_ACTIONS, py_version=_PY_VERSION, node_version=_NODE_VERSION)

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
    name,email = git_cmd("config --get user.name"),git_cmd("config --get user.email")
    if not name: return f'{{name = "{proj_name} contributors"}}'
    return f'{{name = "{name}", email = "{email}"}}' if email else f'{{name = "{name}"}}'


def _fastws_docs_install(proj:str) -> str:
    return f"uv pip install --system --config-settings-package {proj}:metadata-only=1 -e . fastws-cli\ncargo develop"

def _template_docs_workflow(rust:bool = False, install:str = None) -> str:
    opts = []
    if rust: opts += ["rust: 'true'", "r2-url: ${{ secrets.SCCACHE_R2 }}"]
    if install == "": opts.append("install: ''")
    elif install:
        body = "\n".join(f"            {ln}" for ln in install.splitlines())
        opts.append(f"install: |\n{body}")
    with_blk = ("\n        with:\n" + "\n".join(f"          {o}" for o in opts)) if opts else ""
    return f"""name: Docs

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.docs.outputs.page_url }}}}
    steps:
      - id: docs
        uses: {_ACTIONS['fastship_docs']}{with_blk}
"""

def _write(p:Path, s:str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def _read_asset(name:str)->str: return importlib.resources.files("fastship").joinpath(*Path(name).parts).read_text(encoding="utf-8")

def _scaffold(root:Path, files:dict, force:bool = False) -> Path:
    "Create the project directory `root` with `files` (relative path -> text), a LICENSE and a .gitignore."
    root = root.expanduser()
    if root.exists():
        if not force: raise FileExistsError(f"{root} already exists (use force=True to overwrite)")
        shutil.rmtree(root)
    files = {"LICENSE": _read_asset("LICENSE"), ".gitignore": _template_gitignore(), **files}
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
    extras:dict = None, # Additional `[project.optional-dependencies]` entries alongside `dev`
) -> str:
    "Text of a scaffolded `pyproject.toml`."
    url = gh_url(gh_org, proj)
    fastship = "\n".join(f"{k} = {fmt_toml_val(v)}" for k, v in {"branch": "main", **(ship or {})}.items())
    return "\n\n".join([f'[build-system]\nrequires = {fmt_toml_val(requires)}\nbuild-backend = "{backend}"',
        f"""[project]
name = "{proj}"
dynamic = ["version"]
description = "{desc}"
readme = "README.md"
requires-python = ">=3.11"
license = {license or fmt_toml_val(_LICENSE)}
authors = [{_authors_toml(proj)}]
classifiers = {fmt_toml_val(classifiers)}
dependencies = {fmt_toml_val(deps)}""",
        "[project.optional-dependencies]\n" + "\n".join(f"{k} = {fmt_toml_val(v)}" for k, v in ({**(extras or {}), "dev": dev}).items()),
        f'[project.urls]\nHomepage = "{url}"\nDocumentation = "{pages_url(gh_org, proj)}"\nRepository = "{url}"\nIssues = "{url}/issues"',
        f"[tool.fastship]\n{fastship}", *tool]) + "\n"

def _setuptools_tables(pkg:str) -> list[str]:
    "Return `[tool.setuptools]` tables that package only `pkg` and read the version from `pkg.__version__`."
    return ['[tool.setuptools.dynamic]\nversion = { attr = "%s.__version__" }' % pkg, f'[tool.setuptools.packages.find]\ninclude = ["{pkg}"]']

_RS_EDITION = "2024"

def _cargo_package(proj:str, desc:str, gh_org:str, inherit:bool = False) -> str:
    "`[package]` table of a scaffolded `Cargo.toml`. With `inherit`, the version and edition come from `[workspace.package]`."
    version, edition = ("version.workspace = true", "edition.workspace = true") if inherit else (f'version = "{_NEW_VERSION}"', f'edition = "{_RS_EDITION}"')
    return "\n".join(["[package]", f'name = "{proj}"', version, edition, f'license = "{_LICENSE}"',
        f'description = "{desc}"', f'repository = "{gh_url(gh_org, proj)}"'])

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
    return _WORKFLOW % dict(jobs=jobs % _WORKFLOW_VARS, needs=needs, publish=publish % _WORKFLOW_VARS)

_CRATES_FIRST = "For the first release, publish manually with a token (`cargo publish`), then add this repo's `ci.yml` as a trusted publisher in the crate's crates.io settings. Later tags publish through CI without a token."

def _crates_publish(pkg:str = None) -> str:
    "Publish steps that push a crate to crates.io through trusted publishing. `pkg` names the workspace package to publish, when the workspace has several."
    return """      - uses: %(checkout)s
      - uses: %(rust)s
      - id: auth
        uses: %(crates_auth)s
      - run: cargo publish""" + (f" -p {pkg}" if pkg else "") + """
        env:
          CARGO_REGISTRY_TOKEN: ${{ steps.auth.outputs.token }}
"""


def _template_npm_workflow(py:bool = True, has_build:bool = False, has_deps:bool = False) -> str:
    "Tag-triggered OIDC release workflow for npm (and optionally PyPI) publishing."
    steps = ["      - uses: %(checkout)s"]
    if py: steps.append("      - uses: %(setup_python)s\n        with:\n          python-version: '%(py_version)s'")
    steps.append("      - uses: %(setup_node)s\n        with:\n          node-version: '%(node_version)s'\n          registry-url: https://registry.npmjs.org")
    if py: steps.append("      - run: pip install -e '.[dev]' build" if has_build else "      - run: pip install build")
    if has_build and has_deps: steps.append("      - run: npm install")
    if has_build: steps.append("      - run: npm run build")
    if py: steps.append("      - run: python -m build")
    steps.append("      - run: npm publish")
    if py: steps.append("      - uses: %(pypi)s")
    gh_with = "\n          files: dist/*" if py else ""
    steps.append(f"      - uses: %(gh_release)s\n        with:{gh_with}\n          generate_release_notes: true")
    body = "\n".join(steps) % _WORKFLOW_VARS
    return f"""name: Release

on:
  push:
    tags: ['v*']

permissions:
  contents: write
  id-token: write

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
{body}
"""

_RS_HELLO = """pub fn hello(name: &str) -> String {
    format!("Hello, {name}!")
}
"""

def _template_pyproject(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
    return _pyproject(proj_name, desc, gh_org, _PY_CLASSIFIERS, ["fastship[all]"], _setuptools_tables(pkg_name))

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
    maturin = f'[tool.maturin]\nmanifest-path = "py/Cargo.toml"\npython-source = "python"\nmodule-name = "{pkg_name}._core"'
    uv = '[tool.uv]\ncache-keys = [{ file = "pyproject.toml" }, { file = "Cargo.toml" }, { file = "py/Cargo.toml" }]'
    classifiers = ["Programming Language :: Rust", "Programming Language :: Python :: Implementation :: CPython"]
    return _pyproject(proj_name, desc, gh_org, classifiers, ["fastship[all]", _MATURIN_REQ, f"{proj_name}[test]"],
        [maturin, uv, '[tool.pytest.ini_options]\ntestpaths = ["tests"]'], extras=dict(test=[_FASTWS_REQ, "pytest"]),
        requires=[_MATURIN_REQ, _FASTWS_REQ], backend="fastws.build_backend", license='{text = "%s"}' % _LICENSE)

def _template_cargo_toml(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
    "The root `Cargo.toml`. Its own package is the pure Rust crate. Its `py` member is the Python extension."
    return f"""[workspace]
members = ["py"]
default-members = [".", "py"]

[workspace.package]
version = "{_NEW_VERSION}"
edition = "{_RS_EDITION}"

{_cargo_package(proj_name, desc, gh_org, inherit=True)}

[lib]
name = "{pkg_name}"
test = false
doctest = false

[dependencies]

[profile.test]
inherits = "release"

[profile.release]
lto = false
codegen-units = 16

[profile.release.package.{proj_name}]
incremental = true

[profile.release.package.{proj_name}-py]
incremental = true

[profile.dist]
inherits = "release"
lto = "thin"
incremental = false
strip = true

[profile.dist.package.{proj_name}]
incremental = false

[profile.dist.package.{proj_name}-py]
incremental = false
"""

def _template_py_cargo_toml(proj_name:str)->str:
    "The `py/Cargo.toml` of the Python extension. It depends on the root crate and is never published."
    return f"""[package]
name = "{proj_name}-py"
version.workspace = true
edition.workspace = true
publish = false

[lib]
crate-type = ["cdylib", "rlib"]
test = false
doctest = false

[dependencies]
{proj_name} = {{ path = ".." }}
pyo3 = {{ version = ">=0.29", features = ["extension-module", "abi3-py311"] }}

[build-dependencies]
pyo3-build-config = ">=0.29"
"""

def _template_rs_lib(pkg_name:str)->str:
    "The `py/src/lib.rs` of the Python extension. It wraps the root crate's functions as a Python module."
    return f"""use pyo3::prelude::*;

#[pyfunction(name = "hello")]
fn py_hello(name: &str) -> String {{
    {pkg_name}::hello(name)
}}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {{
    m.add_function(wrap_pyfunction!(py_hello, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}}
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


def _template_rs_rust_test(pkg_name:str)->str:
    return f"""#[test]
fn hello_works() {{
    assert_eq!({pkg_name}::hello("fastship"), "Hello, fastship!");
}}
"""

def _template_rs_readme(proj_name:str, test:str, build:str)->str:
    return _tag_readme(proj_name, "PyO3/maturin package scaffolded by fastship. The Rust crate is in `src/`, and its Python extension in `py/`.", test,
        "On a version tag, the workflow builds the wheels and sdist, then publishes the crate to crates.io and the wheels and sdist to GitHub and PyPI. " + _CRATES_FIRST, build=build)

def _template_rs_dev(test:str, build:str)->str:
    return _md("Development", "", {
        "Commands": _bash(test, build),
        "Layout": "The root package is a pure Rust crate, published to crates.io. Rust users who depend on it never build PyO3. The Python extension is the `py` package. It depends on the root crate, is never published, and maturin builds it through `[tool.maturin].manifest-path`.",
        "Versioning": 'The canonical version lives in `[workspace.package]` in `Cargo.toml`, which both crates inherit. `pyproject.toml` gets the Python package version from Cargo via `dynamic = ["version"]`.',
        "Build profiles": "uv builds, `cargo develop`, and `cargo test --release` share the incremental `release` profile (`lto = false`, 16 codegen units) for fast local iteration. CI builds distributed wheels with `dist`, which enables thin LTO, disables incremental compilation, and strips the result.",
        "Release": f"1. Run `{test}`.\n2. Confirm the release version in `Cargo.toml` (`[workspace.package].version`).\n3. Run `ship-release`.\n\n{_TAG_RELEASE}"})

def _template_rs_workflow(proj_name:str)->str:
    return _workflow("""  test:
    runs-on: ubuntu-latest
    steps:
      - uses: %(checkout)s
      - uses: %(rust)s
      - uses: %(sccache)s
        with:
          r2-url: ${{ secrets.SCCACHE_R2 }}
      - uses: %(setup_uv)s
      - uses: %(setup_python)s
        with:
          python-version: '%(py_version)s'
      - run: cargo test --release
      - run: uv pip install --system --config-settings-package __PROJ__:metadata-only=1 -e '.[test]'
      - run: cargo develop
      - run: pytest -q

  build:
    strategy:
      matrix:
        os: [ubuntu-latest, ubuntu-24.04-arm, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: %(checkout)s
      - uses: %(rust)s
      - uses: %(sccache)s
        with:
          r2-url: ${{ secrets.SCCACHE_R2 }}
      - uses: %(setup_python)s
        with:
          python-version: '%(py_version)s'
      - uses: %(maturin)s
        with:
          args: --profile dist --out dist ${{ runner.os == 'Linux' && '--zig' || '' }}
          manylinux: '2_31'
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
""".replace("__PROJ__", proj_name), "[test, build, sdist]", _crates_publish(proj_name) + _PYPI_PUBLISH)

def _template_zig_pyproject(proj_name:str, pkg_name:str, desc:str, gh_org:str)->str:
    return _pyproject(proj_name, desc, gh_org, _PY_CLASSIFIERS, ["fastship[all]", "cibuildwheel~=3.4", _ZIG_REQ, "pytest"],
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
        ".github/workflows/ci.yml": _template_zig_workflow(),
        ".github/workflows/docs.yml": _template_docs_workflow()}, force)
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
    "Create a maturin/PyO3 project wired for fastship Rust commands. Its root crate is pure Rust, and its Python extension is the `py/` crate."
    proj, pkg = _names(name, package)
    test, build = "cargo test --release && cargo develop && pytest -q", "ship-rs-build"
    root = _scaffold(Path(path)/proj, {
        "pyproject.toml": _template_rs_pyproject(proj, pkg, description, gh_org),
        "Cargo.toml": _template_cargo_toml(proj, pkg, description, gh_org),
        "src/lib.rs": _RS_HELLO,
        "py/Cargo.toml": _template_py_cargo_toml(proj),
        "py/build.rs": "fn main() { pyo3_build_config::add_extension_module_link_args(); }\n",
        "py/src/lib.rs": _template_rs_lib(pkg),
        f"python/{pkg}/__init__.py": _template_rs_init(),
        "tests/test_basic.py": _template_rs_test(pkg),
        "tests/hello.rs": _template_rs_rust_test(pkg),
        "README.md": _template_rs_readme(proj, test, build),
        "DEV.md": _template_rs_dev(test, build),
        ".github/workflows/ci.yml": _template_rs_workflow(proj),
        ".github/workflows/docs.yml": _template_docs_workflow(rust=True, install=_fastws_docs_install(proj))}, force)
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
    return _tag_readme(proj_name, desc, test, "The workflow publishes the crate to crates.io and creates the GitHub release. " + _CRATES_FIRST, setup=())

def _template_crate_workflow()->str:
    return _workflow("""  test:
    runs-on: ubuntu-latest
    steps:
      - uses: %(checkout)s
      - uses: %(rust)s
      - uses: %(sccache)s
        with:
          r2-url: ${{ secrets.SCCACHE_R2 }}
      - run: cargo test
""", "test", _crates_publish() + """      - uses: %(gh_release)s
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
        ".github/workflows/ci.yml": _template_crate_workflow()}, force)
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
        "CHANGELOG.md": nbdev.release.CHANGELOG_TMPL,
        "MANIFEST.in": _template_manifest("include CHANGELOG.md"),
        f"{pkg}/__init__.py": f'__version__ = "{_NEW_VERSION}"\n',
        ".github/workflows/docs.yml": _template_docs_workflow()}, force)
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
    has_changes = bool(g.status(porcelain=True, untracked_files='no'))
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
