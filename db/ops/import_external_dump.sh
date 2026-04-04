#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
#  Импорт внешних дампов PostgreSQL в активные сервисы
#  Автоматически определяет сервис, полностью заменяет данные
#  
#  Usage:
#    import_external_dump.sh <service_name> <dump_file> [-y] [--backup]
#
#  Примеры:
#    import_external_dump.sh my_service /path/to/dump.sql -y
#    import_external_dump.sh my_service /path/to/dump.sql.gz -y --backup
#
#  Поддерживаемые форматы:
#    - .sql (обычный SQL дамп)
#    - .sql.gz (сжатый gzip дамп)
# ------------------------------------------------------------

# --- локации ------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG="${LOG:-$ROOT_DIR/ops/backup/import.log}"
mkdir -p "$(dirname "$LOG")"

# функция логирования: в консоль чистый текст, в лог с временными метками
log_message() {
    local message="$1"
    echo "$message"  # в консоль без меток
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $message" >> "$LOG"  # в файл с метками
}

log_message "========================================="
log_message "ИМПОРТ ВНЕШНЕГО ДАМПА POSTGRESQL"  
log_message "========================================="

# --- аргументы ----------------------------------------------
TARGET="${1-}"              # имя сервиса
DUMP_FILE="${2-}"           # путь к файлу дампа
AUTO="${3-}"                # -y (опционально)
BACKUP="${4-}"              # --backup (опционально)

# обработка порядка аргументов
if [[ "${3-}" == "--backup" ]]; then BACKUP="--backup"; AUTO="${4-}"; fi
if [[ "${AUTO:-}" == "--backup" ]]; then BACKUP="$AUTO"; AUTO=""; fi

[[ -n "${TARGET:-}" ]] || { 
    echo "Usage: $0 <service_name> <dump_file> [-y] [--backup]"
    echo "Примеры:"
    echo "  $0 ebay /path/to/dump.sql -y"
    echo "  $0 ebay /path/to/dump.sql.gz -y --backup"
    exit 1
}

[[ -n "${DUMP_FILE:-}" ]] || { 
    log_message "ERROR: Не указан путь к файлу дампа"
    exit 1
}

[[ -f "${DUMP_FILE}" ]] || {
    log_message "ERROR: Файл дампа не найден: ${DUMP_FILE}"
    exit 1
}

# --- helpers ------------------------------------------------
env_val() {                  # env_val VAR FILE
  local var="$1" file="$2"
  grep -E "^[[:space:]]*$var[[:space:]]*=" "$file" | tail -n1 \
    | sed -E 's/^[^=]+=[[:space:]]*//; s/^[\"\x27]|[\"\x27]$//g'
}

in_running() {               # in_running name
  grep -qw -- "$1" <<<"$RUNNING_NAMES"
}

# --- соберём инфу по всем сервисам -------------------------
RUNNING_NAMES="$(docker ps --format '{{.Names}}' | tr '\n' ' ')"
log_message "Запущенные контейнеры: $RUNNING_NAMES"

best_score=-1
SVC=""            # имя папки проекта
CONTAINER=""      # имя контейнера
DBNAME=""         # имя базы
DBUSER=""         # пользователь
DBPASS=""         # пароль

for ENV_FILE in "$ROOT_DIR"/.env.*; do
  [[ -f "$ENV_FILE" ]] || continue
  local_svc="$(basename "$ENV_FILE" | sed 's/^\.env\.//')"

  local_db="$(env_val POSTGRES_DB "$ENV_FILE")"
  [[ -n "$local_db" ]] || local_db="$local_svc"

  local_user="$(env_val POSTGRES_USER "$ENV_FILE")"
  [[ -n "$local_user" ]] || local_user="admin"

  local_pass="$(env_val POSTGRES_PASSWORD "$ENV_FILE")" || true
  local_cname="$(env_val CONTAINER_NAME "$ENV_FILE")" || true

  # определим реально запущенный контейнер для сервиса
  local_container=""
  if [[ -n "$local_cname" ]] && in_running "$local_cname"; then
    local_container="$local_cname"
  elif in_running "${local_svc}_db"; then
    local_container="${local_svc}_db"
  elif in_running "$local_svc"; then
    local_container="$local_svc"
  fi

  # контейнер обязателен для импорта
  [[ -n "$local_container" ]] || continue

  # оценка совпадения: точное по имени папки (проект)
  score=0
  [[ "$TARGET" == "$local_svc" ]] && score=3
  [[ "$TARGET" == "$local_cname" ]] && score=$(( score<2 ? 2 : score ))
  [[ "$TARGET" == "$local_container" ]] && score=$(( score<2 ? 2 : score ))
  [[ "$TARGET" == "$local_db" ]] && score=$(( score<1 ? 1 : score ))

  # если явного совпадения нет — пропускаем
  [[ $score -gt 0 ]] || continue

  if [[ $score -gt $best_score ]]; then
    best_score="$score"
    SVC="$local_svc"
    CONTAINER="$local_container"
    DBNAME="$local_db"
    DBUSER="$local_user"
    DBPASS="$local_pass"
  fi
done

if [[ -z "$CONTAINER" ]]; then
  log_message "ERROR: Не найден запущенный сервис по ключу '$TARGET'."
  log_message "Сейчас запущены: $RUNNING_NAMES"
  log_message "Проверьте: есть ли файл .env.$TARGET, поднят ли контейнер"
  exit 1
fi

# --- информация о файле дампа -------------------------------
DUMP_SIZE="$(ls -lh "$DUMP_FILE" | awk '{print $5}')"
DUMP_TYPE="sql"
[[ "$DUMP_FILE" =~ \.gz$ ]] && DUMP_TYPE="sql.gz"

log_message "About to IMPORT EXTERNAL DUMP"
log_message "  Service    : $SVC"
log_message "  Container  : $CONTAINER"  
log_message "  DB/User    : $DBNAME / $DBUSER"
log_message "  Dump file  : $DUMP_FILE"
log_message "  Dump size  : $DUMP_SIZE"
log_message "  Dump type  : $DUMP_TYPE"
log_message ""

# --- создаём бэкап перед импортом (если запрошено) ---------
if [[ "${BACKUP:-}" == "--backup" ]]; then
    log_message ""
    log_message "📦 СОЗДАНИЕ БЭКАПА ПЕРЕД ИМПОРТОМ"
    log_message "   Текущие данные в базе '$DBNAME' будут сохранены в бэкап"
    log_message ""
    
    if [[ "${AUTO:-}" != "-y" ]]; then
        read -r -p "Создать бэкап перед импортом? [y/N] " backup_ans || backup_ans=""
        backup_ans="$(printf '%s' "$backup_ans" | tr -d ' \r\n\t' | tr '[:upper:]' '[:lower:]')"
        if [[ "$backup_ans" != "y" ]]; then
            log_message "Бэкап пропущен по выбору пользователя"
        else
            BACKUP_SCRIPT="$ROOT_DIR/ops/backup/backup_pg.sh"
            if [[ -f "$BACKUP_SCRIPT" ]]; then
                log_message "Создаём бэкап текущей базы..."
                bash "$BACKUP_SCRIPT" "$SVC" || {
                    log_message "WARNING: Не удалось создать бэкап, но продолжаем импорт"
                }
            else
                log_message "WARNING: Скрипт бэкапа не найден: $BACKUP_SCRIPT"
            fi
        fi
    else
        # автоматический режим - создаём бэкап без вопросов
        BACKUP_SCRIPT="$ROOT_DIR/ops/backup/backup_pg.sh"
        if [[ -f "$BACKUP_SCRIPT" ]]; then
            log_message "Создаём бэкап текущей базы (автоматический режим)..."
            bash "$BACKUP_SCRIPT" "$SVC" || {
                log_message "WARNING: Не удалось создать бэкап, но продолжаем импорт"
            }
        else
            log_message "WARNING: Скрипт бэкапа не найден: $BACKUP_SCRIPT"
        fi
    fi
    log_message ""
fi

# --- подтверждение -----------------------------------------
if [[ "${AUTO:-}" != "-y" ]]; then
  log_message "⚠️  ВНИМАНИЕ!"
  log_message "   Это ПОЛНОСТЬЮ УДАЛИТ базу данных '$DBNAME'"
  log_message "   и заменит её данными из внешнего дампа."
  log_message ""
  read -r -p "Продолжить импорт? [y/N] " ans || ans=""
  ans="$(printf '%s' "$ans" | tr -d ' \r\n\t' | tr '[:upper:]' '[:lower:]')"
  [[ "$ans" == "y" ]] || { log_message "Отменено."; exit 0; }
fi

# --- ждём готовность контейнера -----------------------------
log_message "Проверяем готовность контейнера..."
docker exec -i "$CONTAINER" pg_isready -U "$DBUSER" -d postgres -t 60 >/dev/null || {
    log_message "ERROR: Контейнер $CONTAINER не отвечает"
    exit 1
}

# --- drop/create базы ---------------------------------------
log_message "Пересоздаём базу данных '$DBNAME'..."
docker exec -i "$CONTAINER" psql -U "$DBUSER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DBNAME';
DROP DATABASE IF EXISTS $DBNAME;
CREATE DATABASE $DBNAME;
SQL

# --- импорт дампа -------------------------------------------
log_message "Импортируем дамп (размер: $DUMP_SIZE)..."

# если есть пароль — передаём в окружение psql
PASS_FLAG=()
[[ -n "${DBPASS:-}" ]] && PASS_FLAG=(--env PGPASSWORD="$DBPASS")

# импорт в зависимости от типа файла
if [[ "$DUMP_TYPE" == "sql.gz" ]]; then
    # сжатый дамп
    log_message "Импорт сжатого дампа..."
    gzip -dc "$DUMP_FILE" | docker exec -i "${PASS_FLAG[@]}" "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -v ON_ERROR_STOP=1 2>&1 | while IFS= read -r line; do log_message "  $line"; done
else
    # обычный SQL дамп
    log_message "Импорт SQL дампа..."
    docker exec -i "${PASS_FLAG[@]}" "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -v ON_ERROR_STOP=1 < "$DUMP_FILE" 2>&1 | while IFS= read -r line; do log_message "  $line"; done
fi

# --- финальные проверки ------------------------------------
log_message "Проверяем результат импорта..."

# подсчитываем таблицы
TABLE_COUNT=$(docker exec -i "${PASS_FLAG[@]}" "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null | tr -d ' \t\n' || echo "0")

# проверяем размер базы
DB_SIZE=$(docker exec -i "${PASS_FLAG[@]}" "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -t -c "SELECT pg_size_pretty(pg_database_size('$DBNAME'));" 2>/dev/null | tr -d ' \t' || echo "unknown")

log_message ""
log_message "========================================="
log_message "✅ ИМПОРТ ЗАВЕРШЁН УСПЕШНО!"
log_message "========================================="
log_message "  База данных: $DBNAME"
log_message "  Количество таблиц: $TABLE_COUNT"
log_message "  Размер базы: $DB_SIZE"
log_message "  Дамп: $(basename "$DUMP_FILE")"
log_message ""
log_message "Лог операции: $LOG"
