#!/usr/bin/env bash
set -euo pipefail

# Smoke test for the packages in a directory (default dist/deb): every .deb must be readable by dpkg-deb, carry the
# control fields apt needs plus a copyright file, and pass lintian without errors (warnings are printed, not fatal).
# Needs dpkg-deb and lintian.

DEB_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/dist/deb}"

for tool in dpkg-deb lintian; do
    command -v "${tool}" >/dev/null 2>&1 || { echo "Error: ${tool} not found (apt install dpkg-dev lintian)" >&2; exit 1; }
done

shopt -s nullglob
debs=("${DEB_DIR}"/*.deb)
[[ ${#debs[@]} -gt 0 ]] || { echo "Error: no .deb files in ${DEB_DIR}" >&2; exit 1; }

failed=0
for deb in "${debs[@]}"; do
    name="$(basename "${deb}")"
    pkg="$(dpkg-deb --field "${deb}" Package)"
    for field in Package Version Architecture Maintainer Description Homepage; do
        [[ -n "$(dpkg-deb --field "${deb}" "${field}")" ]] || { echo "FAIL ${name}: no ${field} field" >&2; failed=1; }
    done
    # Read the listing completely before searching it: `dpkg-deb --contents | grep -q` makes grep exit at the first match,
    # dpkg-deb then dies of SIGPIPE and pipefail reports a missing file, depending on the order of the entries.
    contents="$(dpkg-deb --contents "${deb}")"
    grep -q " \./usr/share/doc/${pkg}/copyright$" <<<"${contents}" \
        || { echo "FAIL ${name}: no copyright file" >&2; failed=1; }
done

# Tags that describe how CloudX is packaged on purpose, not mistakes:
#   dir-or-file-in-opt, wrong-path-for-interpreter, package-installs-python-pycache-dir, embedded-library,
#   shared-library-is-executable, unstripped-binary-or-object, missing-dependency-on-libc
#       the Python environments live in /opt/cloudx/venv and /opt/cloudx/cli/venv and arrive as pip built them
#   no-changelog, extended-description-is-empty
#       the changelog is CHANGELOG.md in the repo and each package has a one-line description
#   depends-on-essential-package-without-using-version
#       init-system-helpers, which the maintainer scripts call
# Anything else at error level fails the check; warnings are printed and ignored.
SUPPRESSED="dir-or-file-in-opt,wrong-path-for-interpreter,package-installs-python-pycache-dir,embedded-library"
SUPPRESSED+=",shared-library-is-executable,unstripped-binary-or-object,missing-dependency-on-libc"
SUPPRESSED+=",no-changelog,extended-description-is-empty,depends-on-essential-package-without-using-version"
lintian --fail-on error --tag-display-limit 0 --suppress-tags "${SUPPRESSED}" "${debs[@]}" || failed=1

[[ ${failed} -eq 0 ]] || { echo "Package check failed" >&2; exit 1; }
echo "✓ ${#debs[@]} packages passed"
