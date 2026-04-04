#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# Автоматизированный бэкап PostgreSQL с настройками из .env файлов
# ===================================================================

# Определяем корень проекта
PROJECT_ROOT="$(cd "$(dirname "$0")/../../" && pwd)"
GLOBAL_ENV="$PROJECT_ROOT/.env"


# Функция чтения переменной из .env файла
get_env_var() {
    local var_name="$1"
    local env_file="$2"
    local default_value="${3:-}"
    
    if [[ -f "$env_file" ]]; then
        grep -E "^[[:space:]]*$var_name[[:space:]]*=" "$env_file" 2>/dev/null | tail -n1 \
            | sed -E 's/^[^=]+=[[:space:]]*//; s/^[\"\x27]|[\"\x27]$//g' || echo "$default_value"
    else
        echo "$default_value"
    fi
}

# Функция получения настройки с приоритетом: персональная > глобальная > дефолт
get_setting() {
    local var_name="$1"
    local service_env="$2"
    local default_value="${3:-}"
    
    # Сначала пробуем персональный .env файл
    local value=$(get_env_var "$var_name" "$service_env" "")
    
    # Если не найдено - берем из глобального
    if [[ -z "$value" ]]; then
        value=$(get_env_var "$var_name" "$GLOBAL_ENV" "$default_value")
    fi
    
    echo "$value"
}

# Читаем глобальные настройки
BACKUP_DIR=$(get_env_var "BACKUP_DIR" "$GLOBAL_ENV" "$PROJECT_ROOT/db_backups")
RETENTION_DAYS=$(get_env_var "BACKUP_RETENTION_DAYS" "$GLOBAL_ENV" "14")
LOG_FILE=$(get_env_var "BACKUP_LOG_FILE" "$GLOBAL_ENV" "$PROJECT_ROOT/ops/backup/backup.log")
COMPRESSION=$(get_env_var "BACKUP_COMPRESSION" "$GLOBAL_ENV" "gzip")
RUNNING_ONLY=$(get_env_var "BACKUP_RUNNING_ONLY" "$GLOBAL_ENV" "true")
TIMEOUT=$(get_env_var "BACKUP_TIMEOUT" "$GLOBAL_ENV" "300")

# Если передали имя сервиса как аргумент - бэкапим только его
SVC_ONLY="${1:-}"

# Инициализация
DATE_DIR="$(date +%F)"
STAMP="$(date +%F_%H-%M)"
mkdir -p "$BACKUP_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

# Настройка логирования
exec > >(tee -a "$LOG_FILE") 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Начало бэкапа (PID: $$)"

# Автоочистка контейнеров-призраков перед бэкапом
if [[ -f "$PROJECT_ROOT/ops/cleanup_orphans.sh" ]]; then
    bash "$PROJECT_ROOT/ops/cleanup_orphans.sh" --kill 2>/dev/null || true
fi

# Список запущенных контейнеров
if [[ "$RUNNING_ONLY" == "true" ]]; then
    RUNNING_NAMES="$(docker ps --format '{{.Names}}')"
else
    RUNNING_NAMES="$(docker ps -a --format '{{.Names}}')"
fi

# Счетчики
TOTAL_SERVICES=0
SUCCESSFUL_BACKUPS=0
FAILED_BACKUPS=0

# Обходим все .env.<service> файлы
for ENV_FILE in "$PROJECT_ROOT"/.env.*; do
    [[ -f "$ENV_FILE" ]] || continue
    
    SVC_NAME="$(basename "$ENV_FILE" | sed 's/^\.env\.//')"
    
    # Фильтр по имени сервиса (если задан)
    if [[ -n "$SVC_ONLY" && "$SVC_NAME" != "$SVC_ONLY" ]]; then
        continue
    fi
    
    TOTAL_SERVICES=$((TOTAL_SERVICES + 1))
    
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Обработка сервиса: $SVC_NAME"
    
    # Проверяем, включены ли бэкапы для этого сервиса
    BACKUP_ENABLED=$(get_setting "BACKUP_ENABLED" "$ENV_FILE" "true")
    if [[ "$BACKUP_ENABLED" != "true" ]]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] Бэкапы отключены для $SVC_NAME"
        continue
    fi
    
    # Читаем настройки базы данных
    DB_NAME=$(get_env_var "POSTGRES_DB" "$ENV_FILE" "$SVC_NAME")
    DB_USER=$(get_env_var "POSTGRES_USER" "$ENV_FILE" "admin")
    DB_PASS=$(get_env_var "POSTGRES_PASSWORD" "$ENV_FILE" "")
    CONTAINER_NAME=$(get_env_var "CONTAINER_NAME" "$ENV_FILE" "${SVC_NAME}_db")
    
    # Определяем имя контейнера (приоритет: CONTAINER_NAME > {service}_db > {service})
    CONTAINER=""
    if grep -qw "$CONTAINER_NAME" <<<"$RUNNING_NAMES"; then
        CONTAINER="$CONTAINER_NAME"
    elif grep -qw "${SVC_NAME}_db" <<<"$RUNNING_NAMES"; then
        CONTAINER="${SVC_NAME}_db"
    elif grep -qw "$SVC_NAME" <<<"$RUNNING_NAMES"; then
        CONTAINER="$SVC_NAME"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Контейнер для $SVC_NAME не найден или не запущен"
        FAILED_BACKUPS=$((FAILED_BACKUPS + 1))
        continue
    fi
    
    # Персональные настройки бэкапа для этого сервиса
    SVC_RETENTION=$(get_setting "BACKUP_RETENTION_DAYS" "$ENV_FILE" "$RETENTION_DAYS")
    SVC_COMPRESSION=$(get_setting "BACKUP_COMPRESSION" "$ENV_FILE" "$COMPRESSION")
    SVC_TIMEOUT=$(get_setting "BACKUP_TIMEOUT" "$ENV_FILE" "$TIMEOUT")
    
    # Создаем папки для бэкапа
    OUT_DIR="$BACKUP_DIR/$SVC_NAME/$DATE_DIR"
    mkdir -p "$OUT_DIR"
    
    # Определяем расширение файла
    case "$SVC_COMPRESSION" in
        "gzip") EXT=".sql.gz" ;;
        "none") EXT=".sql" ;;
        *) EXT=".sql.gz" ;;
    esac
    
    OUT_FILE="$OUT_DIR/${DB_NAME}_${STAMP}${EXT}"
    
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Создание бэкапа: $DB_NAME -> $OUT_FILE"
    
    # Создаем бэкап с таймаутом (для macOS используем gtimeout если есть, иначе без таймаута)
    TIMEOUT_CMD=""
    if command -v timeout >/dev/null 2>&1; then
        TIMEOUT_CMD="timeout $SVC_TIMEOUT"
    elif command -v gtimeout >/dev/null 2>&1; then
        TIMEOUT_CMD="gtimeout $SVC_TIMEOUT"
    fi
    
    if $TIMEOUT_CMD bash -c "
        if [[ -n '$DB_PASS' ]]; then
            docker exec -e PGPASSWORD='$DB_PASS' '$CONTAINER' pg_dump -U '$DB_USER' -d '$DB_NAME'
        else
            docker exec '$CONTAINER' pg_dump -U '$DB_USER' -d '$DB_NAME'
        fi
    " > "$OUT_FILE.tmp"; then
        
        # Применяем сжатие если нужно
        if [[ "$SVC_COMPRESSION" == "gzip" ]]; then
            gzip < "$OUT_FILE.tmp" > "$OUT_FILE"
            rm "$OUT_FILE.tmp"
        else
            mv "$OUT_FILE.tmp" "$OUT_FILE"
        fi
        
        echo "$(date '+%Y-%m-%d %H:%M:%S') [SUCCESS] Бэкап создан: $OUT_FILE"
        SUCCESSFUL_BACKUPS=$((SUCCESSFUL_BACKUPS + 1))
        
        # Ротация старых бэкапов для этого сервиса
        echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Удаление бэкапов старше $SVC_RETENTION дней для $SVC_NAME"
        find "$BACKUP_DIR/$SVC_NAME" -type f -name "*.sql*" -mtime "+$SVC_RETENTION" -delete 2>/dev/null || true
        find "$BACKUP_DIR/$SVC_NAME" -type d -empty -delete 2>/dev/null || true
        
    else
        rm -f "$OUT_FILE.tmp"
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Ошибка создания бэкапа для $SVC_NAME"
        FAILED_BACKUPS=$((FAILED_BACKUPS + 1))
    fi
done

# Итоговая статистика
echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Завершение бэкапа"
echo "$(date '+%Y-%m-%d %H:%M:%S') [STATS] Обработано сервисов: $TOTAL_SERVICES"
echo "$(date '+%Y-%m-%d %H:%M:%S') [STATS] Успешных бэкапов: $SUCCESSFUL_BACKUPS"
echo "$(date '+%Y-%m-%d %H:%M:%S') [STATS] Неудачных бэкапов: $FAILED_BACKUPS"

# Управление размером лог-файла
MAX_LOG_SIZE=$(get_env_var "BACKUP_LOG_MAX_SIZE_MB" "$GLOBAL_ENV" "50")
if [[ "$MAX_LOG_SIZE" -gt 0 ]] && [[ -f "$LOG_FILE" ]]; then
    LOG_SIZE_MB=$(du -m "$LOG_FILE" | cut -f1)
    if [[ "$LOG_SIZE_MB" -gt "$MAX_LOG_SIZE" ]]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Ротация лог-файла (размер: ${LOG_SIZE_MB}MB)"
        tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
    fi
fi

exit 0