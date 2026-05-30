#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="$root_dir/.venv-cli"
bin_dir="${HOME}/.local/bin"

ensure_python_venv() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required. Install python3 first, then re-run make install-cli." >&2
    exit 1
  fi

  if python3 -c 'import venv' >/dev/null 2>&1; then
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "python3 venv module is missing, and apt-get is not available." >&2
    echo "Install the OS package that provides python3 venv, then re-run make install-cli." >&2
    exit 1
  fi

  echo "python3 venv module is missing; installing python3-venv..."
  if [ "$(id -u)" -eq 0 ]; then
    apt-get update
    apt-get install -y python3-venv
  else
    if ! command -v sudo >/dev/null 2>&1; then
      echo "sudo is required to install python3-venv automatically." >&2
      echo "Run as root or install python3-venv manually, then re-run make install-cli." >&2
      exit 1
    fi
    sudo apt-get update
    sudo apt-get install -y python3-venv
  fi

  if ! python3 -c 'import venv' >/dev/null 2>&1; then
    echo "python3-venv was installed, but python3 still cannot import venv." >&2
    exit 1
  fi
}

ensure_python_venv
python3 -m venv "$venv_dir"
"$venv_dir/bin/pip" install --upgrade pip
"$venv_dir/bin/pip" install -r "$root_dir/cli/requirements.txt"

mkdir -p "$bin_dir"
for cmd in networking image compute block-store; do
  cat > "$bin_dir/$cmd" <<EOF
#!/usr/bin/env bash
exec "$venv_dir/bin/python" "$root_dir/cli/$cmd" "\$@"
EOF
  chmod +x "$bin_dir/$cmd"
done

echo "Installed CLI commands to $bin_dir"
echo "Make sure $bin_dir is in PATH."
