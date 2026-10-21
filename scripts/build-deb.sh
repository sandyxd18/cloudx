#!/usr/bin/env bash
set -euo pipefail

# Builds the CloudX .deb packages (cloudx-common, cloudx-db, one per unit) from packaging/manifest.toml into dist/deb/.
# Needs python3 3.12, dpkg-deb, fakeroot and network access (pip fills /opt/cloudx/venv inside cloudx-common).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "${SCRIPT_DIR}/VERSION")"
OUT_DIR="${SCRIPT_DIR}/dist/deb"

for tool in dpkg-deb fakeroot; do
    command -v "${tool}" >/dev/null 2>&1 || { echo "Error: ${tool} not found (apt install dpkg-dev fakeroot)" >&2; exit 1; }
done

echo "===> Building CloudX ${VERSION} packages into ${OUT_DIR}..."
rm -rf "${OUT_DIR}"
PYTHONPATH="${SCRIPT_DIR}/scripts" python3 -c '
import sys
from pathlib import Path
from cloudx_deb.build import build_all
for deb in build_all(Path(sys.argv[1]), sys.argv[2]):
    print(deb.name)
' "${OUT_DIR}" "${VERSION}"
echo "✓ $(ls "${OUT_DIR}"/*.deb | wc -l) packages in ${OUT_DIR}"
