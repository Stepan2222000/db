#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# Скрипт безопасного удаления PostgreSQL сервиса
# ===================================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/../" && pwd)"

# Функция показа справки
show_help() {
    echo "🗑️  Безопасное удаление PostgreSQL сервиса"
    echo ""
    echo "Использование: $0 <имя_сервиса> [--force]"
    echo ""
    echo "Параметры:"
    echo "  имя_сервиса  - имя сервиса для удаления"
    echo "  --force      - пропустить все подтверждения (ОПАСНО!)"
    echo ""
    echo "Примеры:"
    echo "  $0 test_service           # Интерактивное удаление"
    echo "  $0 old_db --force        # Удалить без подтверждений"
    echo ""
    echo "⚠️  ВНИМАНИЕ: Удаление необратимо!"
    echo ""
    echo "Что удаляется:"
    echo "  🗃️  .env.{сервис} файл"
    echo "  🐳 Сервис из compose.yaml"
    echo "  🗂️  Переменная из главного .env"
    echo "  📁 Папка с данными ./data/{сервис}"
    echo "  💾 Папка с бэкапами ./db_backups/{сервис}"
    echo "  🛑 Остановка и удаление контейнера"
}

# Проверка аргументов
SERVICE_NAME="${1:-}"
FORCE_MODE="${2:-}"

if [[ -z "$SERVICE_NAME" ]]; then
    show_help
    exit 1
fi

# Проверка существования сервиса
ENV_FILE="$PROJECT_ROOT/.env.$SERVICE_NAME"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ Сервис $SERVICE_NAME не найден!"
    echo "   Файл не существует: $ENV_FILE"
    echo ""
    echo "📋 Доступные сервисы:"
    for env in "$PROJECT_ROOT"/.env.*; do
        [[ -f "$env" ]] || continue
        svc_name="$(basename "$env" | sed 's/^\.env\.//')"
        echo "  - $svc_name"
    done
    exit 1
fi

echo "🗑️  Удаление сервиса: $SERVICE_NAME"
echo ""

# Собираем информацию о сервисе
CONTAINER_NAME="${SERVICE_NAME}_db"
DATA_DIR="$PROJECT_ROOT/data/$SERVICE_NAME"
BACKUP_DIR="$PROJECT_ROOT/db_backups/$SERVICE_NAME"
SERVICE_UPPER="$(echo "${SERVICE_NAME}" | tr '[:lower:]' '[:upper:]')"

# Показываем что будет удалено
echo "📋 Что будет удалено:"
echo "  🗃️  Конфигурация: .env.$SERVICE_NAME"
echo "  🐳 Контейнер: $CONTAINER_NAME"
if [[ -d "$DATA_DIR" ]]; then
    DATA_SIZE=$(du -sh "$DATA_DIR" 2>/dev/null | cut -f1 || echo "неизвестно")
    echo "  📁 Данные БД: $DATA_DIR ($DATA_SIZE)"
else
    echo "  📁 Данные БД: $DATA_DIR (не существует)"
fi
if [[ -d "$BACKUP_DIR" ]]; then
    BACKUP_COUNT=$(find "$BACKUP_DIR" -name "*.sql*" 2>/dev/null | wc -l || echo "0")
    BACKUP_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1 || echo "неизвестно")
    echo "  💾 Бэкапы: $BACKUP_DIR ($BACKUP_COUNT файлов, $BACKUP_SIZE)"
else
    echo "  💾 Бэкапы: $BACKUP_DIR (не существует)"
fi

# Проверяем статус контейнера
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    CONTAINER_STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "неизвестно")
    echo "  🐳 Статус контейнера: $CONTAINER_STATUS"
    
    # Проверяем подключения к БД
    if [[ "$CONTAINER_STATUS" == "running" ]]; then
        CONNECTIONS=$(docker exec "$CONTAINER_NAME" psql -U admin -d postgres -t -c "SELECT count(*) FROM pg_stat_activity WHERE datname='$SERVICE_NAME';" 2>/dev/null | tr -d ' ' || echo "?")
        if [[ "$CONNECTIONS" != "0" && "$CONNECTIONS" != "?" ]]; then
            echo "  ⚠️  Активные подключения к БД: $CONNECTIONS"
        fi
    fi
else
    echo "  🐳 Контейнер не найден"
fi

echo ""

# Подтверждения (если не --force)
if [[ "$FORCE_MODE" != "--force" ]]; then
    echo "⚠️  ВНИМАНИЕ: Это действие необратимо!"
    echo ""
    
    # Первое подтверждение
    read -r -p "Вы действительно хотите удалить сервис '$SERVICE_NAME'? [y/N] " confirm1
    confirm1="$(printf '%s' "$confirm1" | tr -d ' \r\n\t' | tr '[:upper:]' '[:lower:]')"
    [[ "$confirm1" == "y" ]] || { echo "❌ Отменено"; exit 0; }
    
    # Второе подтверждение с именем сервиса
    echo ""
    echo "Для подтверждения введите точное имя сервиса: $SERVICE_NAME"
    read -r -p "Имя сервиса: " confirm2
    [[ "$confirm2" == "$SERVICE_NAME" ]] || { echo "❌ Имя не совпадает. Отменено"; exit 0; }
    
    # Третье подтверждение для данных
    if [[ -d "$DATA_DIR" ]]; then
        echo ""
        echo "⚠️  Данные базы данных будут БЕЗВОЗВРАТНО удалены!"
        read -r -p "Подтвердите удаление данных (введите 'DELETE'): " confirm3
        [[ "$confirm3" == "DELETE" ]] || { echo "❌ Удаление данных не подтверждено. Отменено"; exit 0; }
    fi
fi

echo ""
echo "🔄 Начинаем удаление..."

# 1. Остановка и удаление контейнера
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "✅ Остановка контейнера $CONTAINER_NAME"
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    echo "✅ Удаление контейнера $CONTAINER_NAME"
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
else
    echo "✅ Контейнер $CONTAINER_NAME не найден (пропускаем)"
fi

# 2. Удаление из compose.yaml
echo "✅ Удаление из compose.yaml"
COMPOSE_FILE="$PROJECT_ROOT/compose.yaml"
if [[ -f "$COMPOSE_FILE" ]]; then
    # Создаем временный файл без нашего сервиса
    awk "
        # Если нашли наш сервис - начинаем удалять
        /^[[:space:]]*${SERVICE_NAME}:/ {
            in_service = 1
            next
        }
        # Если нашли следующий сервис на том же уровне отступов - прекращаем удалять  
        /^  [a-zA-Z_][a-zA-Z0-9_]*:/ && in_service {
            in_service = 0
            print
            next
        }
        # Печатаем строки только если мы не в удаляемом сервисе
        !in_service { print }
    " "$COMPOSE_FILE" > "$COMPOSE_FILE.tmp"
    
    mv "$COMPOSE_FILE.tmp" "$COMPOSE_FILE"
else
    echo "⚠️  compose.yaml не найден"
fi

# 3. Удаление .env файла
echo "✅ Удаление .env.$SERVICE_NAME"
rm -f "$ENV_FILE"

# 4. Удаление переменной из главного .env
echo "✅ Удаление переменной из главного .env"
MAIN_ENV="$PROJECT_ROOT/.env"
if [[ -f "$MAIN_ENV" ]]; then
    grep -v "^${SERVICE_UPPER}_CONTAINER_NAME=" "$MAIN_ENV" > "$MAIN_ENV.tmp" || touch "$MAIN_ENV.tmp"
    mv "$MAIN_ENV.tmp" "$MAIN_ENV"
fi

# 5. Удаление папок с данными
if [[ -d "$DATA_DIR" ]]; then
    echo "✅ Удаление данных $DATA_DIR"
    rm -rf "$DATA_DIR"
else
    echo "✅ Папка данных не найдена (пропускаем)"
fi

# 6. Удаление папок с бэкапами (с дополнительным подтверждением)
if [[ -d "$BACKUP_DIR" ]]; then
    if [[ "$FORCE_MODE" != "--force" ]]; then
        echo ""
        echo "⚠️  Найдены бэкапы в $BACKUP_DIR"
        read -r -p "Удалить бэкапы тоже? [y/N] " delete_backups
        delete_backups="$(printf '%s' "$delete_backups" | tr -d ' \r\n\t' | tr '[:upper:]' '[:lower:]')"
        if [[ "$delete_backups" == "y" ]]; then
            echo "✅ Удаление бэкапов $BACKUP_DIR"
            rm -rf "$BACKUP_DIR"
        else
            echo "✅ Бэкапы сохранены в $BACKUP_DIR"
        fi
    else
        echo "✅ Удаление бэкапов $BACKUP_DIR"
        rm -rf "$BACKUP_DIR"
    fi
else
    echo "✅ Папка бэкапов не найдена (пропускаем)"
fi

echo ""
echo "🎉 Сервис $SERVICE_NAME успешно удален!"
echo ""

# Автоматическая очистка контейнеров-призраков
echo "🧹 Проверка контейнеров-призраков..."
if [[ -f "$PROJECT_ROOT/ops/cleanup_orphans.sh" ]]; then
    bash "$PROJECT_ROOT/ops/cleanup_orphans.sh" --kill
else
    echo "⚠️  Скрипт cleanup_orphans.sh не найден"
fi

echo ""
echo "🧹 Для очистки неиспользуемых Docker образов выполните:"
echo "   docker system prune -f"