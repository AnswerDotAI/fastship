#!/usr/bin/env bash
# Smoke test for fastship's PyO3 project templates.
#
# Scaffolds a fresh project with the *current working-tree* fastship, builds its
# extension, and runs its generated test suite — all inside a throwaway venv and
# temp dir that are removed on exit. Nothing touches your dev environment.
#
# Run after changing templates (_template_cargo_toml / _template_rs_lib /
# _template_rs_workflow) or the ship-rs-* commands:
#
#   tools/smoke.sh
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "==> repo:    $repo"
echo "==> workdir: $work"

python3 -m venv "$work/venv"
# shellcheck disable=SC1091
source "$work/venv/bin/activate"
python -m pip install -q --upgrade pip
python -m pip install -q -e "$repo" maturin pytest

echo "==> scaffolding project with ship_rs_new"
python -c "import sys; from fastship.release import ship_rs_new; ship_rs_new('smoke-proj', path=sys.argv[1], force=True)" "$work"
cd "$work/smoke-proj"

echo "==> cargo fmt --check"
cargo fmt --check

echo "==> maturin develop"
maturin develop

echo "==> pytest"
pytest -q

echo "==> SMOKE OK"
