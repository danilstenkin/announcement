#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="${PROJECT_DIR}/deploy"

echo "=== 1. Собираю образ API (linux/amd64) ==="
docker buildx build --platform linux/amd64 -t announcements-api:latest --load "${PROJECT_DIR}"

echo ""
echo "=== 2. Экспортирую образ в tar ==="

echo "  api.tar..."
echo "FROM announcements-api:latest" | docker buildx build --platform linux/amd64 \
  -t announcements-api:latest --output type=docker,dest="${DEPLOY_DIR}/api.tar" -

echo ""
echo "=== 3. Готово ==="
ls -lh "${DEPLOY_DIR}"/*.tar
echo ""
echo "Папка deploy/ готова к переносу на сервер."
echo "На сервере: cd deploy && ./start.sh"
