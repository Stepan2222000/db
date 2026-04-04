#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
#  Автовосстановление БД из .sql.gz без ручных правок
#  Определяет контейнер/БД/юзера по .env проекта
#  Usage:
#    restore.sh <project|container|dbname> [backup.sql.gz] [-y]
#  Примеры:
#    restore.sh my_service -y
#    restore.sh my_service /root/postgres-docker/db_backups/my_service/2025-08-10/my_service_2025-08-10_13-26.sql.gz -y
#    echo /root/postgres-docker/db_backups/my_service/2025-08-10/my_service_*.sql.gz > /root/postgres-docker/db_backups/my_service/SELECTED
#    restore.sh my_service -y
# ------------------------------------------------------------

# --- локации ------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# корень проекта = ../../ от ops/backup
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/db_backups}"
LOG="${LOG:-$ROOT_DIR/ops/backup/restore.log}"
mkdir -p "$(dirname "$LOG")"
# логируем всё
exec > >(tee -a "$LOG") 2>&1

# --- аргументы ----------------------------------------------
TARGET="${1-}"              # проект/контейнер/имя БД
FILE_ARG="${2-}"            # путь к .sql.gz (опционально)
AUTO="${3-}"                # -y (опционально)
# поддержка форм: <db> -y
if [[ "${FILE_ARG:-}" == "-y" ]]; then AUTO="-y"; FILE_ARG=""; fi
[[ -n "${TARGET:-}" ]] || { echo "Usage: $0 <project|container|dbname> [backup.sql.gz] [-y]"; exit 1; }

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

best_score=-1
SVC=""            # имя папки проекта
CONTAINER=""      # имя контейнера
DBNAME=""         # имя базы
DBUSER=""         # пользователь
DBPASS=""         # пароль (может быть пустым)

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

  # контейнер обязателен для restore
  [[ -n "$local_container" ]] || continue

  # оценка совпадения:
  # 3 — точное по имени папки (проект),
  # 2 — точное по имени контейнера,
  # 1 — точное по имени базы,
  # 0 — нет совпадения
  score=0
  [[ "$TARGET" == "$local_svc"      ]] && score=3
  [[ "$TARGET" == "$local_cname"    ]] && score=$(( score<2 ? 2 : score ))
  [[ "$TARGET" == "$local_container" ]] && score=$(( score<2 ? 2 : score ))
  [[ "$TARGET" == "$local_db"       ]] && score=$(( score<1 ? 1 : score ))

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
  echo "Не нашёл запущенный сервис по ключу '$TARGET'."
  echo "Сейчас запущены: $RUNNING_NAMES"
  echo "Проверь: есть ли папка с .env, поднят ли контейнер, верно ли указал TARGET."
  exit 1
fi

# --- выбираем файл бэкапа ----------------------------------
BACKUP_FILE=""
if [[ -n "${FILE_ARG:-}" ]]; then
  BACKUP_FILE="$FILE_ARG"
else
  # 1) пробуем "отмеченный" файл
  sel="$BACKUP_DIR/$SVC/SELECTED"
  if [[ -f "$sel" ]]; then
    BACKUP_FILE="$(<"$sel")"
  fi
  # 2) иначе — самый свежий *.sql.gz этого проекта
  if [[ -z "${BACKUP_FILE:-}" || ! -f "$BACKUP_FILE" ]]; then
    # Сначала ищем файлы с именем текущей базы данных
    BACKUP_FILE="$(ls -t "$BACKUP_DIR/$SVC"/*/"${DBNAME}_"*.sql.gz 2>/dev/null | head -1 || true)"
    
    # Если не найдено, ищем любые .sql.gz файлы в папке сервиса (для переименованных)
    if [[ -z "$BACKUP_FILE" ]]; then
      BACKUP_FILE="$(ls -t "$BACKUP_DIR/$SVC"/*/*.sql.gz 2>/dev/null | head -1 || true)"
    fi
    
    # Альтернативный поиск через find (сначала по имени БД, потом любые)
    if [[ -z "$BACKUP_FILE" ]]; then
      BACKUP_FILE="$(find "$BACKUP_DIR/$SVC" -name "${DBNAME}_*.sql.gz" -type f 2>/dev/null | sort | tail -1 || true)"
      if [[ -z "$BACKUP_FILE" ]]; then
        BACKUP_FILE="$(find "$BACKUP_DIR/$SVC" -name "*.sql.gz" -type f 2>/dev/null | sort | tail -1 || true)"
      fi
    fi
  fi
fi

[[ -n "${BACKUP_FILE:-}" && -f "$BACKUP_FILE" ]] || { echo "Бэкап не найден для '$SVC' в папке ${BACKUP_DIR}/${SVC}/ (искал ${DBNAME}_*.sql.gz и любые *.sql.gz файлы)."; exit 1; }

echo "About to RESTORE"
echo "  Project   : $SVC"
echo "  Container : $CONTAINER"
echo "  DB/User   : $DBNAME / $DBUSER"
echo "  Backup    : $BACKUP_FILE"

# --- подтверждение -----------------------------------------
if [[ "${AUTO:-}" != "-y" ]]; then
  read -r -p "Это УДАЛИТ БД $DBNAME и зальёт данные из бэкапа. Продолжить? [y/N] " ans || ans=""
  ans="$(printf '%s' "$ans" | tr -d ' \r\n\t' | tr '[:upper:]' '[:lower:]')"
  [[ "$ans" == "y" ]] || { echo "Cancelled."; exit 0; }
fi

# --- ждём готовность контейнера -----------------------------
docker exec -i "$CONTAINER" pg_isready -U "$DBUSER" -d postgres -t 60 >/dev/null

# --- drop/create базы ---------------------------------------
docker exec -i "$CONTAINER" psql -U "$DBUSER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DBNAME';
DROP DATABASE IF EXISTS $DBNAME;
CREATE DATABASE $DBNAME;
SQL

# --- восстановление -----------------------------------------
# если есть пароль — передаём в окружение psql
PASS_FLAG=()
[[ -n "${DBPASS:-}" ]] && PASS_FLAG=(--env PGPASSWORD="$DBPASS")

# распаковываем на хосте и льём внутрь контейнера
gzip -dc "$BACKUP_FILE" | docker exec -i "${PASS_FLAG[@]}" "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -v ON_ERROR_STOP=1

echo "[OK] Restored $DBNAME from $BACKUP_FILE"
