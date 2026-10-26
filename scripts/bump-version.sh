#!/usr/bin/env bash
set -euo pipefail

# Sets the release version everywhere it is written: VERSION (also the git tag without the "v" and the .deb version),
# services/pyproject.toml and cli/setup.cfg (Python packages, where 1.0.0-rc1 is spelled 1.0.0rc1).
#   scripts/bump-version.sh 1.0.0-rc1
# A pre-release becomes 1.0.0~rc1 in the .deb packages by itself (scripts/cloudx_deb/build.py).

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEW="${1:-}"

[[ "${NEW}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-(rc|alpha|beta)[0-9]+)?$ ]] \
    || { echo "usage: $0 X.Y.Z or X.Y.Z-rcN (also -alphaN, -betaN), got '${NEW}'" >&2; exit 2; }

PY="${NEW/-rc/rc}"; PY="${PY/-alpha/a}"; PY="${PY/-beta/b}"

printf '%s\n' "${NEW}" > "${ROOT}/VERSION"
sed -i -E "s/^version = \"[^\"]+\"/version = \"${PY}\"/" "${ROOT}/services/pyproject.toml"
sed -i -E "s/^version = .*/version = ${PY}/" "${ROOT}/cli/setup.cfg"

echo "VERSION=${NEW}  python=${PY}"
echo "next: commit, merge, then git tag v${NEW} && git push origin v${NEW}"
