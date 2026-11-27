"Pure-Markdown Quarto site generator and `llms.txt` helper for non-nbdev `fastship` projects."

__all__ = ["docs_base_url", "docs_mod_urls", "refresh_llms", "ship_llms", "ship_docs"]

import json, shutil, subprocess, tempfile

from fastcore.all import *  # Path, nested_idx, call_parse, CliError, repo_details
from pyskills.core import has_public_api, mod_md, pkg_mods
from pyskills.llms import update_llms_txt
from fastship.config import branch, find_pkg_path, find_pyproject, gh_url, pages_url, parse_repo, project_root, ship_cfg


def _urls(data: dict) -> dict[str, str]:
    return {k.lower(): v.strip() for k, v in (nested_idx(data, "project", "urls") or {}).items() if isinstance(v, str)}


def _repo_slug(root: Path, data: dict) -> tuple[str | None, str | None]:
    urls = _urls(data)
    for k in ("repository", "source", "homepage"):
        if "github.com/" in (u := urls.get(k, "")):
            try:
                if all(slug := repo_details(u.rstrip("/"))): return tuple(slug)
            except Exception: pass
    return parse_repo(path=root)


def docs_base_url(root: Path, data: dict) -> str:
    "Published docs base URL (ending with `/`): `[project.urls].Documentation` else GitHub Pages URL."
    if doc := _urls(data).get("documentation"): return doc.rstrip("/") + "/"
    owner, repo = _repo_slug(root, data)
    return pages_url(owner, repo) if owner and repo else ""


def docs_mod_urls(pkg_path: Path | None) -> tuple[dict[str, str], str | None]:
    "AST-only `(mod_urls, api_url)` mapping discovered submodules to `api/<mod>.html.md` and top-level API to `api.html.md`."
    if not pkg_path or not (pkg_path := Path(pkg_path)).is_dir(): return {}, None
    pkg_name = pkg_path.name
    _, submods = pkg_mods(pkg_path, pkg_name)
    mod_urls = {nm: f"api/{nm[len(pkg_name)+1:].replace('.', '/')}.html.md" for nm in submods}
    return mod_urls, ("api.html.md" if has_public_api(pkg_path/"__init__.py") else None)


def refresh_llms(root: Path, data: dict, base_url: str = None) -> list[Path]:
    "Generate or refresh repo-root `llms.txt` from `pkg/skill.py` for non-nbdev repos; return changed paths."
    if nested_idx(data, "tool", "nbdev") is not None: return []
    try: target = find_pkg_path(root, data)
    except FileNotFoundError: target = root
    wfs = Path(root)/".github"/"workflows"
    if (wfs/"docs.yml").exists() or (wfs/"docs.yaml").exists():
        mod_urls, api_url = docs_mod_urls(target)
        kw = dict(mod_urls=mod_urls, api_url=api_url, base_url=docs_base_url(root, data))
    else: kw = {}
    if base_url is not None: kw["base_url"] = base_url
    return update_llms_txt(target, fn=Path(root)/"llms.txt", **kw)


@call_parse
def ship_llms(
    path: str = None,     # Project directory (defaults to nearest pyproject.toml)
    base_url: str = None, # Fallback base URL when skill.py omits __base_url__
):
    "Generate repo-root `llms.txt` from `pkg/skill.py` for non-nbdev projects."
    pyproj = find_pyproject(path)
    refresh_llms(pyproj.parent, pyproj.read_toml(), base_url=base_url)


def _stage_project(root: Path, data: dict, pkg_path: Path | None, stage: Path) -> Path:
    proj_name, desc = nested_idx(data, "project", "name") or root.name, nested_idx(data, "project", "description") or ""
    site_url = docs_base_url(root, data)
    owner, repo = _repo_slug(root, data)
    repo_url = gh_url(owner, repo) if owner and repo else ""
    if (readme := root/"README.md").is_file(): shutil.copy2(readme, stage/"index.md")
    else: (stage/"index.md").write_text(f"# {proj_name}\n\n{desc}\n", encoding="utf-8")

    has_docs = False
    if (docs_dir := root/"docs").is_dir():
        shutil.copytree(docs_dir, stage/"docs", ignore=shutil.ignore_patterns("_*", ".*", "llms*.txt", "_site", "_docs", "_freeze"))
        has_docs = any((stage/"docs").rglob("*.md")) or any((stage/"docs").rglob("*.qmd"))

    mod_urls, api_url = docs_mod_urls(pkg_path)
    api_pages = []
    if pkg_path is not None:
        pkg_name = pkg_path.name
        if api_url and (api_md := mod_md(pkg_name, exclude_submods=set(mod_urls), title=f"{pkg_name} API")):
            (stage/"api.md").write_text(api_md, encoding="utf-8")
            api_pages.append("api.md")
        else: api_url = None
        for mod_nm, rel_html_md in mod_urls.items():
            dest_rel = Path(rel_html_md.removesuffix(".html.md") + ".md")
            dest = stage/dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(mod_md(mod_nm), encoding="utf-8")
            api_pages.append(dest_rel.as_posix())
    if pkg_path is not None and (pkg_path/"skill.py").exists():
        update_llms_txt(pkg_path, fn=stage/"llms.txt", base_url=site_url, mod_urls=mod_urls, api_url=api_url)
    elif (root/"llms.txt").is_file(): shutil.copy2(root/"llms.txt", stage/"llms.txt")

    sidebar = [{"href": "index.md", "text": "Overview"}]
    if has_docs: sidebar.append({"auto": "docs"})
    if api_pages: sidebar.append({"section": "API", "contents": api_pages})
    website = {"title": proj_name, "twitter-card": True, "open-graph": True,
        "navbar": {"background": "primary", "search": True}, "sidebar": {"style": "floating", "contents": sidebar}}
    if desc: website["description"] = desc
    if site_url: website["site-url"] = site_url.rstrip("/")
    if repo_url: website |= {"repo-url": repo_url, "repo-branch": branch(ship_cfg(data)), "repo-actions": ["issue"]}
    cfg = {"project": {"type": "website", "output-dir": "_docs", "resources": ["llms*.txt"]},
        "format": {"html": {"theme": "cosmo", "toc": True}, "commonmark": {"output-ext": "html.md", "wrap": "preserve"}},
        "format-links": ["html", {"format": "commonmark", "text": "Markdown"}], "website": website}
    (stage/"_quarto.yml").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return stage


@call_parse
def ship_docs(
    path: str = None,      # Project directory (defaults to nearest pyproject.toml or current directory)
    out: str = "_docs",    # Output directory for rendered site (relative to project root unless absolute)
    preview: bool = False, # Run `quarto preview` instead of rendering to `out`
):
    "Build a pure-Markdown Quarto website (HTML + `.html.md` + `llms.txt`, zero Jupyter) for a non-nbdev project."
    if not shutil.which("quarto"): raise CliError("quarto is not installed (see https://quarto.org)")
    root, data, _ = project_root(path)
    try: pkg_path = find_pkg_path(root, data) if data else None
    except FileNotFoundError: pkg_path = None
    out_dir = Path(out).expanduser()
    if not out_dir.is_absolute(): out_dir = root/out_dir

    with tempfile.TemporaryDirectory(prefix="fastship-docs-") as td:
        stage = _stage_project(root, data, pkg_path, Path(td))
        cmd = ["quarto", "preview"] if preview else ["quarto", "render", "--output-dir", "_docs"]
        r = subprocess.run(cmd, cwd=stage)
        if r.returncode != 0: raise CliError(f"{' '.join(cmd)} failed with exit code {r.returncode}")
        if not preview:
            if out_dir.exists(): shutil.rmtree(out_dir)
            out_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage/"_docs"), str(out_dir))
            print(f"Docs built: {out_dir}")
