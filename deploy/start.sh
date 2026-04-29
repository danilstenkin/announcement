#!/usr/bin/env bash
set -e

echo "=== 1. Загружаю Docker-образ ==="
docker load -i api.tar

echo ""
echo "=== 2. Поднимаю сервис ==="
docker compose up -d

echo ""
echo "=== 3. Жду готовности API ==="

echo -n "API..."
until curl -sf http://localhost:8004/announce/health > /dev/null 2>&1; do
    sleep 2
done
echo " OK"

echo ""
echo "========================================="
echo "  Сервис запущен!"
echo "========================================="
echo ""
echo "  API:   http://localhost:8004/announce/docs"
echo ""
