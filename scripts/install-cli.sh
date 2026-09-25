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
"$venv_dir/bin/pip" install -e "$root_dir/cli"

mkdir -p "$bin_dir"
for cmd in cloudx networking image compute block-store object-store load-balancer; do
  ln -sf "$venv_dir/bin/cloudx" "$bin_dir/$cmd"
done

echo "✓ Installed CLI commands to $bin_dir"

# Auto-configure ~/.cloudx/credentials if auth-api is running and credentials don't exist
CREDS_FILE="${HOME}/.cloudx/credentials"
if [ ! -f "$CREDS_FILE" ]; then
  mkdir -p "${HOME}/.cloudx"
  echo "Setting up initial credentials in $CREDS_FILE..."
  python3 -c "
import requests, os
try:
    resp = requests.post('http://127.0.0.1:8005/auth/tokens', json={'username': 'admin', 'password': 'admin123'}, timeout=3)
    if resp.status_code == 201:
        token = resp.json()['token']
        headers = {'Authorization': f'Bearer {token}'}
        k_resp = requests.post('http://127.0.0.1:8005/auth/access-keys', json={'name': 'cli-key'}, headers=headers, timeout=3)
        if k_resp.status_code == 201:
            kdata = k_resp.json()
            creds_content = f\"[default]\ncloudx_access_key_id = {kdata['access_key']}\ncloudx_secret_access_key = {kdata['secret_key']}\ncloudx_auth_url = http://127.0.0.1:8005\n\"
            with open('$CREDS_FILE', 'w') as f:
                f.write(creds_content)
            print('✓ Credentials auto-configured in $CREDS_FILE')
except Exception as e:
    print(f'Note: Could not auto-fetch credentials from auth-api ({e}). Run cloudx login when stack is active.')
" || true
fi

echo "Make sure $bin_dir is in PATH."
