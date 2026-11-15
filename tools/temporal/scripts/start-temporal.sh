#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! docker info >/dev/null 2>&1; then
  echo "STATUS: DOCKER_UNAVAILABLE"
  echo "Start Docker Desktop first, then rerun:"
  echo "  bash scripts/start-temporal.sh"
  exit 2
fi

echo "Starting Temporal server + UI (PostgreSQL + auto-setup)..."
docker compose down >/dev/null 2>&1 || true
docker compose up -d

echo
echo "Waiting for Temporal UI on http://localhost:8233 ..."
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:8233" >/dev/null 2>&1; then
    echo "Temporal UI is up: http://localhost:8233"
    echo "gRPC frontend: localhost:7233"
  echo
  echo "Next:"
  echo "  terminal 1: python worker.py --mode real --repo C:/Users/vkita/my_code/game"
  echo "  terminal 2: python start.py --mode temporal-real --repo C:/Users/vkita/my_code/game"
    exit 0
  fi
  sleep 2
done

echo "Temporal UI did not become ready in time."
echo "Check: docker compose ps"
echo "Logs:  docker compose logs -f"
exit 1
