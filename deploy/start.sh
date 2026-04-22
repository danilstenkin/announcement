#!/usr/bin/env bash
set -e

TAR_FILE="announcements-api.tar"

# 1. Загружаем все образы если есть .tar файл
if [ -f "${TAR_FILE}" ]; then
    echo "=== 1. Загружаю Docker-образы из ${TAR_FILE} ==="
    docker load -i ${TAR_FILE}
else
    echo "=== 1. Файл ${TAR_FILE} не найден, использую существующие образы ==="
fi

# 2. Поднимаем все сервисы
echo ""
echo "=== 2. Поднимаю все сервисы ==="
docker compose up -d

# 3. Ждём пока всё поднимется
echo ""
echo "=== 3. Жду готовности сервисов ==="
echo -n "Postgres..."
until docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-pure_rag}" -d "${POSTGRES_DB:-sme_db}" > /dev/null 2>&1; do
    sleep 1
done
echo " OK"

echo -n "API..."
until curl -sf http://localhost:8050/announce/health > /dev/null 2>&1; do
    sleep 2
done
echo " OK"

# 4. Готово
echo ""
echo "========================================="
echo "  Все сервисы запущены!"
echo "========================================="
echo ""
echo "  API:        http://localhost:8050/announce/docs"
echo "  PostgreSQL: http://localhost:5433"
echo "  MinIO:      http://localhost:9003"
echo ""
