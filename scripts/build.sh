#!/usr/bin/env bash
# scripts/build.sh (#1) — headless Quartus compile wrapper referenced by quartus/README.md.
#
# Usage: scripts/build.sh <project> [revision]   (revision defaults to <project>)
set -euo pipefail

project="${1:?usage: scripts/build.sh <project> [revision]}"
revision="${2:-$project}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_root/scripts/env.sh"

proj_dir="$repo_root/quartus/$project"
[ -d "$proj_dir" ] || { echo "scripts/build.sh: no such project quartus/$project" >&2; exit 1; }

cd "$proj_dir"
quartus_sh --flow compile "$project" -c "$revision"
