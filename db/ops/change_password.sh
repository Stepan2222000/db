#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# Скрипт изменения пароля PostgreSQL сервиса
# ===================================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/../" && pwd)"

show_help() {
    echo "🔐 Изменение пароля PostgreSQL сервиса"
    echo ""
    echo "Использование: $0 <имя_сервиса> <новый_пароль>"
    echo ""
    echo "Примеры:"
    echo "  $0 analytics_db NewSecretPass123"
    echo "  $0 test_service MySuperPassword"
    echo ""
    echo "⚠️  ВНИМАНИЕ: Контейнер будет перезапущен!"
}

SERVICE_NAME="${1:-}"
NEW_PASSWORD="${2:-}"

if [[ -z "$SERVICE_NAME" || -z "$NEW_PASSWORD" ]]; then
    show_help
    exit 1
fi

ENV_FILE="$PROJECT_ROOT/.env.$SERVICE_NAME"
CONTAINER_NAME="${SERVICE_NAME}_db"

# Проверяем существование сервиса
if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ Сервис $SERVICE_NAME не найден!"
    echo "   Файл не существует: $ENV_FILE"
    exit 1
fi

echo "🔐 Изменение пароля для сервиса: $SERVICE_NAME"
echo "🔒 Новый пароль: $NEW_PASSWORD"
echo ""

# Показываем текущий пароль
CURRENT_PASSWORD=$(grep "^POSTGRES_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2 || echo "не найден")
echo "📋 Текущий пароль: $CURRENT_PASSWORD"

# Подтверждение
read -r -p "Изменить пароль? [y/N] " confirm
confirm="$(printf '%s' "$confirm" | tr -d ' \r\n\t' | tr '[:upper:]' '[:lower:]')"
[[ "$confirm" == "y" ]] || { echo "❌ Отменено"; exit 0; }

echo ""
echo "🔄 Изменение пароля..."

# 1. Обновляем .env файл
echo "✅ Обновление .env.$SERVICE_NAME"
sed -i.bak "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$NEW_PASSWORD/" "$ENV_FILE"
rm -f "$ENV_FILE.bak"

# 2. Перезапускаем контейнер
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "✅ Остановка контейнера $CONTAINER_NAME"
    docker compose stop "$SERVICE_NAME"
    
    echo "✅ Удаление старого контейнера"
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
    
    echo "✅ Запуск с новым паролем"
    docker compose up "$SERVICE_NAME" -d
    
    # Ждем готовности
    echo "⏳ Ожидание готовности БД..."
    for i in {1..30}; do
        if docker exec "$CONTAINER_NAME" pg_isready -U admin 2>/dev/null; then
            echo "✅ БД готова к работе"
            break
        fi
        sleep 1
    done
else
    echo "⚠️  Контейнер не запущен, просто обновили пароль в конфигурации"
fi

echo ""
echo "🎉 Пароль успешно изменен!"
echo ""
echo "🔗 Новые данные для подключения:"
echo "  Host: localhost"
echo "  Port: $(grep "^POSTGRES_PORT=" "$ENV_FILE" | cut -d'=' -f2 || echo "неизвестен")"
echo "  Database: $SERVICE_NAME"
echo "  Username: admin"
echo "  Password: $NEW_PASSWORD"