#!/usr/bin/env bash
set -e

IMAGE_NAME="announcements-api"
IMAGE_TAG="latest"
TAR_FILE="${IMAGE_NAME}.tar"

echo "=== 1. Собираю Docker-образ ==="
docker build --platform linux/amd64 -t ${IMAGE_NAME}:${IMAGE_TAG} .

echo ""
echo "=== 2. Сохраняю образ в ${TAR_FILE} ==="
docker save -o ${TAR_FILE} ${IMAGE_NAME}:${IMAGE_TAG}

echo ""
echo "=== Готово! ==="
echo "Размер: $(du -h ${TAR_FILE} | cut -f1)"
echo ""
echo "Теперь скопируй на сервер эту папку:"
echo "  scp -r . user@server:/path/to/announcements/"
echo ""
echo "Или только нужные файлы:"
echo "  scp ${TAR_FILE} docker-compose.yml .env start.sh monitoring/ user@server:/path/to/announcements/"
