#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CloudX Release Tarball Builder
# Generates a clean, production-ready tarball containing pre-built frontend,
# microservices, CLI, systemd units, configuration templates, and requirements.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${SCRIPT_DIR}/VERSION"
VERSION="1.0.0"

if [[ -f "${VERSION_FILE}" ]]; then
    VERSION="$(cat "${VERSION_FILE}" | tr -d '[:space:]')"
fi

DIST_DIR="${SCRIPT_DIR}/dist"
STAGING_DIR="${SCRIPT_DIR}/build/staging"
TARBALL_NAME="cloudx-${VERSION}.tar.gz"
TARBALL_PATH="${DIST_DIR}/${TARBALL_NAME}"
LATEST_LINK="${DIST_DIR}/cloudx-latest.tar.gz"

echo "============================================================"
echo " Packaging CloudX Release: v${VERSION}"
echo "============================================================"

# 1. Build frontend dashboard if needed
echo "===> Checking and building frontend dashboard..."
FRONTEND_DIR="${SCRIPT_DIR}/services/dashboard/frontend"
STATIC_DIR="${SCRIPT_DIR}/services/dashboard/app/static"

if [[ -d "${FRONTEND_DIR}" ]]; then
    if command -v npm >/dev/null 2>&1; then
        echo "Building dashboard React assets with npm..."
        (cd "${FRONTEND_DIR}" && npm run build)
        mkdir -p "${STATIC_DIR}"
        rm -rf "${STATIC_DIR:?}"/*
        cp -r "${FRONTEND_DIR}/dist/"* "${STATIC_DIR}/"
        echo "✓ Dashboard built successfully into ${STATIC_DIR}"
    else
        echo "Warning: npm not found. Checking existing static build..."
        if [[ ! -f "${STATIC_DIR}/index.html" ]]; then
            echo "Error: Static assets not found in ${STATIC_DIR} and npm is unavailable." >&2
            exit 1
        fi
        echo "✓ Using pre-existing static assets in ${STATIC_DIR}"
    fi
fi

# 2. Prepare staging directory
echo "===> Preparing clean staging directory..."
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}"
mkdir -p "${DIST_DIR}"

# 3. Copy services (excluding frontend source code, node_modules, and git files)
echo "===> Copying backend microservices..."
mkdir -p "${STAGING_DIR}/services"
cp -r "${SCRIPT_DIR}/services/"* "${STAGING_DIR}/services/"
# Exclude raw frontend directory from release package
rm -rf "${STAGING_DIR}/services/dashboard/frontend"

# 4. Copy CLI tool
echo "===> Copying CloudX CLI..."
mkdir -p "${STAGING_DIR}/cli"
cp -r "${SCRIPT_DIR}/cli/"* "${STAGING_DIR}/cli/"
rm -rf "${STAGING_DIR}/cli/build" "${STAGING_DIR}/cli/dist" "${STAGING_DIR}/cli/"*.egg-info

# 5. Copy shared modules (if present)
if [[ -d "${SCRIPT_DIR}/shared" ]]; then
    echo "===> Copying shared modules..."
    cp -r "${SCRIPT_DIR}/shared" "${STAGING_DIR}/"
fi

# 6. Copy systemd units and cloudx configuration samples
echo "===> Copying system configurations and service units..."
mkdir -p "${STAGING_DIR}/etc"
if [[ -d "${SCRIPT_DIR}/etc" ]]; then
    cp -r "${SCRIPT_DIR}/etc/"* "${STAGING_DIR}/etc/"
fi

# 7. Copy metadata files & consolidated requirements
echo "===> Copying metadata & requirements..."
cp "${SCRIPT_DIR}/requirements.txt" "${STAGING_DIR}/"
cp "${VERSION_FILE}" "${STAGING_DIR}/"

# 8. Clean up python bytecode and cache files
echo "===> Cleaning up temporary and cache files..."
find "${STAGING_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}" -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true
find "${STAGING_DIR}" -type f -name "*.pyo" -delete 2>/dev/null || true
find "${STAGING_DIR}" -type f -name ".DS_Store" -delete 2>/dev/null || true

# 9. Create compressed tarball
echo "===> Creating release tarball: ${TARBALL_NAME}..."
tar -czf "${TARBALL_PATH}" -C "${STAGING_DIR}" .

# Create symlink for latest
(cd "${DIST_DIR}" && ln -sf "${TARBALL_NAME}" "cloudx-latest.tar.gz")

# Generate sha256 checksum
(cd "${DIST_DIR}" && sha256sum "${TARBALL_NAME}" > "${TARBALL_NAME}.sha256")

# 10. Output summary
TAR_SIZE="$(du -h "${TARBALL_PATH}" | awk '{print $1}')"
FILE_COUNT="$(tar -ztf "${TARBALL_PATH}" | wc -l)"

echo "============================================================"
echo "✓ Release tarball successfully generated!"
echo "  Artifact: ${TARBALL_PATH} (${TAR_SIZE})"
echo "  Latest:   ${LATEST_LINK}"
echo "  Files:    ${FILE_COUNT} files packaged"
echo "  Checksum: ${TARBALL_PATH}.sha256"
echo "============================================================"
