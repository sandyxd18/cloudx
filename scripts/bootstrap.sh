#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"

docker compose up -d --build

echo "waiting for API health checks..."
for url in \
  http://localhost:8001/health \
  http://localhost:8002/health \
  http://localhost:8003/health \
  http://localhost:8004/health; do
  until curl -fsS "$url" >/dev/null; do
    sleep 2
  done
  echo "ok: $url"
done

echo "mini OpenStack lab is ready"
