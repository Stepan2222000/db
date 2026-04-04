#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# Автоматизированный бэкап PostgreSQL с отправкой на storage сервер
# и умной GFS (Grandfather-Father-Son) ротацией
# v2.0 - с исправлениями безопасности и надёжности
# ===================================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/../../" && pwd)"
GLOBAL_ENV="$PROJECT_ROOT/.env"

# =====================================================================
# FIX #1: Lock файл для предотвращения параллельного запуска
# =====================================================================
LOCK_FILE="/tmp/backup_pg.lock"

acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Скрипт уже запущен (PID: $old_pid)"
            echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Если это ошибка, удалите: $LOCK_FILE"
            exit 1
        else
            # Старый процесс мёртв, удаляем lock
            rm -f "$LOCK_FILE"
        fi
    fi

    # Создаём lock файл
    echo $$ > "$LOCK_FILE"

    # Удаляем lock при выходе (любом)
    trap 'rm -f "$LOCK_FILE"' EXIT INT TERM
}

acquire_lock

# Функция чтения переменной из .env файла
get_env_var() {
    local var_name="$1"
    local env_file="$2"
    local default_value="${3:-}"

    if [[ -f "$env_file" ]]; then
        grep -E "^[[:space:]]*$var_name[[:space:]]*=" "$env_file" 2>/dev/null | tail -n1 \
            | sed -E 's/^[^=]+=[[:space:]]*//; s/^["'"'"']|["'"'"']$//g' || echo "$default_value"
    else
        echo "$default_value"
    fi
}

# Функция получения настройки с приоритетом: персональная > глобальная > дефолт
get_setting() {
    local var_name="$1"
    local service_env="$2"
    local default_value="${3:-}"

    local value
    value=$(get_env_var "$var_name" "$service_env" "")
    if [[ -z "$value" ]]; then
        value=$(get_env_var "$var_name" "$GLOBAL_ENV" "$default_value")
    fi
    echo "$value"
}

# Глобальные настройки
BACKUP_DIR=$(get_env_var "BACKUP_DIR" "$GLOBAL_ENV" "/tmp/db_backups")
LOG_FILE=$(get_env_var "BACKUP_LOG_FILE" "$GLOBAL_ENV" "$PROJECT_ROOT/ops/backup/backup.log")
COMPRESSION=$(get_env_var "BACKUP_COMPRESSION" "$GLOBAL_ENV" "gzip")
RUNNING_ONLY=$(get_env_var "BACKUP_RUNNING_ONLY" "$GLOBAL_ENV" "true")
TIMEOUT=$(get_env_var "BACKUP_TIMEOUT" "$GLOBAL_ENV" "300")

# Storage настройки
STORAGE_SERVER=$(get_env_var "BACKUP_STORAGE_SERVER" "$GLOBAL_ENV" "")
STORAGE_PATH=$(get_env_var "BACKUP_STORAGE_PATH" "$GLOBAL_ENV" "")

# GFS настройки
DAILY_KEEP=$(get_env_var "BACKUP_DAILY_KEEP" "$GLOBAL_ENV" "7")
WEEKLY_KEEP=$(get_env_var "BACKUP_WEEKLY_KEEP" "$GLOBAL_ENV" "4")
MONTHLY_KEEP=$(get_env_var "BACKUP_MONTHLY_KEEP" "$GLOBAL_ENV" "3")

# =====================================================================
# FIX #4: Минимальное свободное место (в MB)
# =====================================================================
MIN_FREE_SPACE_MB=$(get_env_var "BACKUP_MIN_FREE_SPACE_MB" "$GLOBAL_ENV" "2000")

# Фильтр по имени сервиса
SVC_ONLY="${1:-}"

# Инициализация
STAMP="$(date +%Y-%m-%d_%H-%M)"
mkdir -p "$BACKUP_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

# Логирование
exec > >(tee -a "$LOG_FILE") 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] =========================================="
echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Начало бэкапа v2.0 (PID: $$)"
echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Storage: $STORAGE_SERVER:$STORAGE_PATH"

# =====================================================================
# FIX #4: Проверка свободного места перед началом
# =====================================================================
check_free_space() {
    local path="$1"
    local min_mb="$2"

    # Получаем свободное место в MB
    local free_mb
    free_mb=$(df -m "$path" 2>/dev/null | tail -1 | awk '{print $4}')

    if [[ -z "$free_mb" ]] || [[ "$free_mb" -lt "$min_mb" ]]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Недостаточно места на диске!"
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Свободно: ${free_mb:-0} MB, требуется: $min_mb MB"
        return 1
    fi

    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Свободное место: ${free_mb} MB (мин: $min_mb MB)"
    return 0
}

if ! check_free_space "$BACKUP_DIR" "$MIN_FREE_SPACE_MB"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Бэкап прерван из-за нехватки места"
    exit 1
fi

# Проверка storage сервера
if [[ -z "$STORAGE_SERVER" ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] BACKUP_STORAGE_SERVER не настроен!"
    exit 1
fi

if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "root@$STORAGE_SERVER" "echo OK" >/dev/null 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Не удалось подключиться к storage серверу $STORAGE_SERVER"
    exit 1
fi

# Автоочистка контейнеров-призраков
if [[ -f "$PROJECT_ROOT/ops/cleanup_orphans.sh" ]]; then
    bash "$PROJECT_ROOT/ops/cleanup_orphans.sh" --kill 2>/dev/null || true
fi

# Список контейнеров
if [[ "$RUNNING_ONLY" == "true" ]]; then
    RUNNING_NAMES="$(docker ps --format '{{.Names}}')"
else
    RUNNING_NAMES="$(docker ps -a --format '{{.Names}}')"
fi

# Счетчики
TOTAL_SERVICES=0
SUCCESSFUL_BACKUPS=0
FAILED_BACKUPS=0

# =====================================================================
# FIX #6: GFS ротация с retry логикой
# =====================================================================
gfs_rotate() {
    local svc_name="$1"
    local daily="$2"
    local weekly="$3"
    local monthly="$4"
    local max_retries=3
    local retry_count=0

    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] GFS ротация для $svc_name (D:$daily W:$weekly M:$monthly)"

    while [[ $retry_count -lt $max_retries ]]; do
        if ssh -o ConnectTimeout=30 -o ServerAliveInterval=10 "root@$STORAGE_SERVER" bash -s "$STORAGE_PATH" "$svc_name" "$daily" "$weekly" "$monthly" << 'ROTATE'
STORAGE_PATH="$1"
SVC_NAME="$2"
DAILY_KEEP="$3"
WEEKLY_KEEP="$4"
MONTHLY_KEEP="$5"

cd "$STORAGE_PATH/$SVC_NAME" 2>/dev/null || exit 0

TODAY=$(date +%s)
DAILY_SEC=$((DAILY_KEEP * 86400))
WEEKLY_SEC=$((WEEKLY_KEEP * 7 * 86400))
MONTHLY_SEC=$((MONTHLY_KEEP * 30 * 86400))

for file in *.sql.gz *.sql; do
    [ -f "$file" ] || continue

    # FIX #7: Извлекаем и валидируем дату из имени файла
    file_date=$(echo "$file" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1)
    [ -z "$file_date" ] && continue

    # Валидация даты - пропускаем невалидные
    if ! date -d "$file_date" +%s >/dev/null 2>&1; then
        echo "[WARNING] Невалидная дата в файле: $file, пропускаем"
        continue
    fi

    file_ts=$(date -d "$file_date" +%s 2>/dev/null) || continue
    age_sec=$((TODAY - file_ts))

    # Пропускаем будущие даты (ошибка в имени)
    if [[ $age_sec -lt 0 ]]; then
        echo "[WARNING] Дата в будущем: $file, пропускаем"
        continue
    fi

    day_of_week=$(date -d "$file_date" +%u 2>/dev/null)  # 7 = воскресенье
    day_of_month=$(date -d "$file_date" +%d 2>/dev/null)

    keep=false

    # Правила:
    # 1. Последние N дней - храним всё
    [ $age_sec -le $DAILY_SEC ] && keep=true

    # 2. Воскресенья за N недель
    [ $age_sec -le $WEEKLY_SEC ] && [ "$day_of_week" = "7" ] && keep=true

    # 3. 1-е числа месяца за N месяцев
    [ $age_sec -le $MONTHLY_SEC ] && [ "$day_of_month" = "01" ] && keep=true

    if [ "$keep" = "false" ]; then
        rm -f "$file"
        echo "  Удалён: $file (возраст: $((age_sec / 86400)) дней)"
    fi
done

echo "GFS_ROTATE_OK"
ROTATE
        then
            # Проверяем что ротация завершилась успешно
            return 0
        fi

        retry_count=$((retry_count + 1))
        echo "$(date '+%Y-%m-%d %H:%M:%S') [WARNING] GFS retry $retry_count/$max_retries для $svc_name"
        sleep 5
    done

    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] GFS ротация не удалась после $max_retries попыток для $svc_name"
    return 1
}

# Обработка сервисов
for ENV_FILE in "$PROJECT_ROOT"/.env.*; do
    [[ -f "$ENV_FILE" ]] || continue

    SVC_NAME="$(basename "$ENV_FILE" | sed 's/^\.env\.//')"

    # Фильтр
    if [[ -n "$SVC_ONLY" && "$SVC_NAME" != "$SVC_ONLY" ]]; then
        continue
    fi

    TOTAL_SERVICES=$((TOTAL_SERVICES + 1))

    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Обработка: $SVC_NAME"

    # Проверка включения бэкапов
    BACKUP_ENABLED=$(get_setting "BACKUP_ENABLED" "$ENV_FILE" "true")
    if [[ "$BACKUP_ENABLED" != "true" ]]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] Бэкапы отключены для $SVC_NAME"
        continue
    fi

    # Настройки БД
    DB_NAME=$(get_env_var "POSTGRES_DB" "$ENV_FILE" "$SVC_NAME")
    DB_USER=$(get_env_var "POSTGRES_USER" "$ENV_FILE" "admin")
    DB_PASS=$(get_env_var "POSTGRES_PASSWORD" "$ENV_FILE" "")
    CONTAINER_NAME=$(get_env_var "CONTAINER_NAME" "$ENV_FILE" "${SVC_NAME}_db")

    # Поиск контейнера
    CONTAINER=""
    if grep -qw "$CONTAINER_NAME" <<<"$RUNNING_NAMES"; then
        CONTAINER="$CONTAINER_NAME"
    elif grep -qw "${SVC_NAME}_db" <<<"$RUNNING_NAMES"; then
        CONTAINER="${SVC_NAME}_db"
    elif grep -qw "$SVC_NAME" <<<"$RUNNING_NAMES"; then
        CONTAINER="$SVC_NAME"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Контейнер для $SVC_NAME не найден"
        FAILED_BACKUPS=$((FAILED_BACKUPS + 1))
        continue
    fi

    # Персональные GFS настройки
    SVC_DAILY=$(get_setting "BACKUP_DAILY_KEEP" "$ENV_FILE" "$DAILY_KEEP")
    SVC_WEEKLY=$(get_setting "BACKUP_WEEKLY_KEEP" "$ENV_FILE" "$WEEKLY_KEEP")
    SVC_MONTHLY=$(get_setting "BACKUP_MONTHLY_KEEP" "$ENV_FILE" "$MONTHLY_KEEP")
    SVC_TIMEOUT=$(get_setting "BACKUP_TIMEOUT" "$ENV_FILE" "$TIMEOUT")
    SVC_COMPRESSION=$(get_setting "BACKUP_COMPRESSION" "$ENV_FILE" "$COMPRESSION")

    # Расширение файла
    case "$SVC_COMPRESSION" in
        "gzip") EXT=".sql.gz" ;;
        *) EXT=".sql" ;;
    esac

    LOCAL_FILE="$BACKUP_DIR/${DB_NAME}_${STAMP}${EXT}"
    LOCAL_ERR="$BACKUP_DIR/${DB_NAME}_${STAMP}.err"
    REMOTE_FILE="${DB_NAME}_${STAMP}${EXT}"

    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Создание бэкапа: $DB_NAME"

    # =====================================================================
    # FIX #5: Логирование ошибок pg_dump (не скрываем через 2>/dev/null)
    # =====================================================================
    TIMEOUT_CMD=""
    command -v timeout >/dev/null 2>&1 && TIMEOUT_CMD="timeout $SVC_TIMEOUT"

    # Создаём бэкап с сохранением ошибок в отдельный файл
    if $TIMEOUT_CMD bash -c "
        if [[ -n '$DB_PASS' ]]; then
            docker exec -e PGPASSWORD='$DB_PASS' '$CONTAINER' pg_dump -U '$DB_USER' -d '$DB_NAME' --no-password
        else
            docker exec '$CONTAINER' pg_dump -U '$DB_USER' -d '$DB_NAME'
        fi
    " > "$LOCAL_FILE.tmp" 2>"$LOCAL_ERR"; then

        # Проверяем размер дампа (защита от пустых/corrupted дампов)
        local_size=$(stat -c%s "$LOCAL_FILE.tmp" 2>/dev/null || stat -f%z "$LOCAL_FILE.tmp" 2>/dev/null || echo "0")
        if [[ "$local_size" -lt 100 ]]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') [WARNING] Подозрительно маленький дамп ($local_size байт) для $SVC_NAME"
        fi

        # Сжатие
        if [[ "$SVC_COMPRESSION" == "gzip" ]]; then
            gzip < "$LOCAL_FILE.tmp" > "$LOCAL_FILE"
            rm "$LOCAL_FILE.tmp"
        else
            mv "$LOCAL_FILE.tmp" "$LOCAL_FILE"
        fi

        # Удаляем файл ошибок если пустой
        [[ ! -s "$LOCAL_ERR" ]] && rm -f "$LOCAL_ERR"

        # =====================================================================
        # FIX #2: Проверяем mkdir перед scp
        # =====================================================================
        if ! ssh -o ConnectTimeout=10 "root@$STORAGE_SERVER" "mkdir -p '$STORAGE_PATH/$SVC_NAME'"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Не удалось создать папку на storage для $SVC_NAME"
            FAILED_BACKUPS=$((FAILED_BACKUPS + 1))
            rm -f "$LOCAL_FILE" "$LOCAL_ERR"
            continue
        fi

        if scp -q "$LOCAL_FILE" "root@$STORAGE_SERVER:$STORAGE_PATH/$SVC_NAME/$REMOTE_FILE"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') [SUCCESS] Отправлен на storage: $SVC_NAME/$REMOTE_FILE"
            SUCCESSFUL_BACKUPS=$((SUCCESSFUL_BACKUPS + 1))

            # Удаляем локальный файл
            rm -f "$LOCAL_FILE"

            # GFS ротация с retry
            gfs_rotate "$SVC_NAME" "$SVC_DAILY" "$SVC_WEEKLY" "$SVC_MONTHLY" || true
        else
            echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Не удалось отправить на storage: $SVC_NAME"
            FAILED_BACKUPS=$((FAILED_BACKUPS + 1))
            # НЕ удаляем локальный файл - можно восстановить вручную
            echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Локальный файл сохранён: $LOCAL_FILE"
        fi
    else
        # =====================================================================
        # FIX #5: Выводим реальные ошибки pg_dump
        # =====================================================================
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] pg_dump ошибка для $SVC_NAME:"
        if [[ -s "$LOCAL_ERR" ]]; then
            # Показываем первые 10 строк ошибки
            head -10 "$LOCAL_ERR" | while IFS= read -r line; do
                echo "$(date '+%Y-%m-%d %H:%M:%S') [PG_ERROR] $line"
            done
        fi
        rm -f "$LOCAL_FILE.tmp" "$LOCAL_ERR"
        FAILED_BACKUPS=$((FAILED_BACKUPS + 1))
    fi
done

# Итоги
echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] =========================================="
echo "$(date '+%Y-%m-%d %H:%M:%S') [STATS] Сервисов: $TOTAL_SERVICES"
echo "$(date '+%Y-%m-%d %H:%M:%S') [STATS] Успешно: $SUCCESSFUL_BACKUPS"
echo "$(date '+%Y-%m-%d %H:%M:%S') [STATS] Ошибок: $FAILED_BACKUPS"

# Ротация лога
MAX_LOG_SIZE=$(get_env_var "BACKUP_LOG_MAX_SIZE_MB" "$GLOBAL_ENV" "50")
if [[ "$MAX_LOG_SIZE" -gt 0 ]] && [[ -f "$LOG_FILE" ]]; then
    LOG_SIZE_MB=$(du -m "$LOG_FILE" 2>/dev/null | cut -f1 || echo "0")
    if [[ "$LOG_SIZE_MB" -gt "$MAX_LOG_SIZE" ]]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Ротация лога (${LOG_SIZE_MB}MB)"
        tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
    fi
fi

# Lock файл удалится автоматически через trap
exit 0
