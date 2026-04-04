#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# Скрипт обновления настроек PostgreSQL сервиса
# ===================================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/../" && pwd)"

show_help() {
    echo "⚙️  Обновление настроек PostgreSQL сервиса"
    echo ""
    echo "Использование: $0 <имя_сервиса>"
    echo ""
    echo "Примеры:"
    echo "  $0 analytics_db        # Интерактивное изменение настроек"
    echo "  $0 test_service        # Изменить любые параметры сервиса"
    echo ""
    echo "Что можно изменить:"
    echo "  🔒 Пароль базы данных"
    echo "  🚪 Порт подключения"
    echo "  🗃️  Имя базы данных"
    echo "  👤 Имя пользователя"
    echo "  🐳 Имя контейнера"
    echo ""
    echo "⚠️  ВНИМАНИЕ: Контейнер будет перезапущен с новыми настройками!"
}

# Функция чтения текущего значения из .env
get_env_value() {
    local var_name="$1"
    local env_file="$2"
    grep "^${var_name}=" "$env_file" 2>/dev/null | cut -d'=' -f2- || echo ""
}

# Функция обновления значения в .env файле
update_env_value() {
    local var_name="$1"
    local new_value="$2"
    local env_file="$3"
    
    if grep -q "^${var_name}=" "$env_file"; then
        # Обновляем существующую переменную
        sed -i.bak "s|^${var_name}=.*|${var_name}=${new_value}|" "$env_file"
    else
        # Добавляем новую переменную
        echo "${var_name}=${new_value}" >> "$env_file"
    fi
    rm -f "${env_file}.bak"
}

SERVICE_NAME="${1:-}"

if [[ -z "$SERVICE_NAME" ]]; then
    show_help
    exit 1
fi

ENV_FILE="$PROJECT_ROOT/.env.$SERVICE_NAME"
COMPOSE_FILE="$PROJECT_ROOT/compose.yaml"
MAIN_ENV="$PROJECT_ROOT/.env"

# Проверяем существование сервиса
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

# Читаем текущие значения
CURRENT_USER=$(get_env_value "POSTGRES_USER" "$ENV_FILE")
CURRENT_PASSWORD=$(get_env_value "POSTGRES_PASSWORD" "$ENV_FILE")
CURRENT_DB=$(get_env_value "POSTGRES_DB" "$ENV_FILE")
CURRENT_PORT=$(get_env_value "POSTGRES_PORT" "$ENV_FILE")
CURRENT_CONTAINER=$(get_env_value "CONTAINER_NAME" "$ENV_FILE")

echo "⚙️  Обновление настроек сервиса: $SERVICE_NAME"
echo "================================================"
echo ""
echo "📋 Текущие настройки:"
echo "  👤 Пользователь: $CURRENT_USER"
echo "  🔒 Пароль: $CURRENT_PASSWORD"
echo "  🗃️  База данных: $CURRENT_DB"
echo "  🚪 Порт: $CURRENT_PORT"
echo "  🐳 Контейнер: $CURRENT_CONTAINER"
echo ""

# Переменные для новых значений
NEW_USER="$CURRENT_USER"
NEW_PASSWORD="$CURRENT_PASSWORD"
NEW_DB="$CURRENT_DB"
NEW_PORT="$CURRENT_PORT"
NEW_CONTAINER="$CURRENT_CONTAINER"
CHANGES_MADE=false

echo "🔧 Изменение настроек (нажмите Enter для сохранения текущего значения):"
echo ""

# 1. Имя пользователя
read -r -p "👤 Новое имя пользователя [$CURRENT_USER]: " input_user
if [[ -n "$input_user" && "$input_user" != "$CURRENT_USER" ]]; then
    NEW_USER="$input_user"
    CHANGES_MADE=true
fi

# 2. Пароль
read -r -p "🔒 Новый пароль [$CURRENT_PASSWORD]: " input_password
if [[ -n "$input_password" && "$input_password" != "$CURRENT_PASSWORD" ]]; then
    NEW_PASSWORD="$input_password"
    CHANGES_MADE=true
fi

# 3. Имя базы данных
read -r -p "🗃️  Новое имя базы данных [$CURRENT_DB]: " input_db
if [[ -n "$input_db" && "$input_db" != "$CURRENT_DB" ]]; then
    NEW_DB="$input_db"
    CHANGES_MADE=true
fi

# 4. Порт
while true; do
    read -r -p "🚪 Новый порт [$CURRENT_PORT]: " input_port
    if [[ -z "$input_port" ]]; then
        break  # Оставляем текущий порт
    elif [[ "$input_port" == "$CURRENT_PORT" ]]; then
        break  # Порт не изменился
    elif [[ ! "$input_port" =~ ^[0-9]+$ ]] || [[ "$input_port" -lt 1024 ]] || [[ "$input_port" -gt 65535 ]]; then
        echo "❌ Порт должен быть числом от 1024 до 65535"
        continue
    else
        # Проверяем занятость порта другими сервисами
        if grep -q "POSTGRES_PORT=$input_port" "$PROJECT_ROOT"/.env.* 2>/dev/null && \
           ! grep -q "POSTGRES_PORT=$input_port" "$ENV_FILE" 2>/dev/null; then
            echo "❌ Порт $input_port уже используется другим сервисом!"
            continue
        fi
        NEW_PORT="$input_port"
        CHANGES_MADE=true
        break
    fi
done

# Функция нормализации имени контейнера
normalize_container_name() {
    local n="$1"
    n="$(echo "$n" | tr '[:upper:]' '[:lower:]')"   # в нижний регистр
    n="${n// /_}"                                   # пробелы -> _
    n="$(echo "$n" | tr -cd 'a-z0-9_.-')"          # разрешённые символы
    if [[ ${#n} -lt 2 ]]; then
        n="${SERVICE_NAME}_db"  # Используем SERVICE_NAME вместо NEW_SERVICE_NAME (которая может быть не определена)
    fi
    echo "$n"
}

# 5. Имя контейнера
read -r -p "🐳 Новое имя контейнера [$CURRENT_CONTAINER]: " input_container
if [[ -n "$input_container" && "$input_container" != "$CURRENT_CONTAINER" ]]; then
    NEW_CONTAINER="$(normalize_container_name "$input_container")"
    CHANGES_MADE=true
fi

# Проверяем переименование сервиса (когда имя БД не совпадает с именем сервиса)
if [[ "$CURRENT_DB" != "$SERVICE_NAME" ]]; then
    CHANGES_MADE=true
fi

# Проверяем, были ли изменения
if [[ "$CHANGES_MADE" != "true" ]]; then
    echo ""
    echo "ℹ️  Изменений не было. Настройки остались прежними."
    exit 0
fi

echo ""
echo "📝 Итоговые изменения:"
[[ "$NEW_USER" != "$CURRENT_USER" ]] && echo "  👤 Пользователь: $CURRENT_USER → $NEW_USER"
[[ "$NEW_PASSWORD" != "$CURRENT_PASSWORD" ]] && echo "  🔒 Пароль: $CURRENT_PASSWORD → $NEW_PASSWORD"
[[ "$NEW_DB" != "$CURRENT_DB" ]] && echo "  🗃️  База данных: $CURRENT_DB → $NEW_DB"
[[ "$NEW_PORT" != "$CURRENT_PORT" ]] && echo "  🚪 Порт: $CURRENT_PORT → $NEW_PORT"
[[ "$NEW_CONTAINER" != "$CURRENT_CONTAINER" ]] && echo "  🐳 Контейнер: $CURRENT_CONTAINER → $NEW_CONTAINER"

echo ""
read -r -p "Применить изменения? [y/N] " confirm
confirm="$(printf '%s' "$confirm" | tr -d ' \r\n\t' | tr '[:upper:]' '[:lower:]')"
[[ "$confirm" == "y" ]] || { echo "❌ Отменено"; exit 0; }

echo ""
echo "🔄 Применение изменений..."

# 1. Создаем бэкап перед изменениями
BACKUP_CREATED=false
if docker ps --format '{{.Names}}' | grep -q "^${CURRENT_CONTAINER}$"; then
    echo "💾 Создание бэкапа перед изменениями..."
    if bash "$PROJECT_ROOT/ops/backup/backup_pg.sh" "$SERVICE_NAME"; then
        echo "✅ Бэкап успешно создан"
        BACKUP_CREATED=true
    else
        echo "⚠️  Не удалось создать бэкап, но продолжаем"
    fi
else
    echo "⚠️  Контейнер $CURRENT_CONTAINER не запущен - бэкап не создан"
fi


# 3. Проверяем нужно ли перезапускать контейнер
NEED_CONTAINER_RESTART=false

# Перезапуск нужен только при изменении Docker параметров (порт, имя контейнера)
if [[ "$NEW_PORT" != "$CURRENT_PORT" ]] || [[ "$NEW_CONTAINER" != "$CURRENT_CONTAINER" ]]; then
    NEED_CONTAINER_RESTART=true
    echo "🔄 Требуется перезапуск контейнера для применения Docker настроек"
fi

# Остановка контейнера ТОЛЬКО если нужен перезапуск
if [[ "$NEED_CONTAINER_RESTART" == "true" ]] && docker ps --format '{{.Names}}' | grep -q "^${CURRENT_CONTAINER}$"; then
    echo "✅ Остановка контейнера $CURRENT_CONTAINER для применения Docker изменений"
    docker compose stop "$SERVICE_NAME" 2>/dev/null || true
    docker rm "$CURRENT_CONTAINER" 2>/dev/null || true
fi

# 4. Проверяем изменение имени базы данных (влечет переименование сервиса)
NEW_SERVICE_NAME="$NEW_DB"
# Главное сравнение: изменилось ли имя сервиса (базы данных) относительно исходного?
if [[ "$NEW_DB" != "$SERVICE_NAME" ]]; then
    echo "✅ Переименование сервиса: $SERVICE_NAME → $NEW_SERVICE_NAME"
    
    # Проверяем что новое имя не занято
    if [[ -f "$PROJECT_ROOT/.env.$NEW_SERVICE_NAME" ]] && [[ "$NEW_SERVICE_NAME" != "$SERVICE_NAME" ]]; then
        echo "❌ Сервис $NEW_SERVICE_NAME уже существует!"
        exit 1
    fi
fi

# 5. Обновление .env файла сервиса
echo "✅ Обновление .env.$SERVICE_NAME"
update_env_value "POSTGRES_USER" "$NEW_USER" "$ENV_FILE"
update_env_value "POSTGRES_PASSWORD" "$NEW_PASSWORD" "$ENV_FILE"
update_env_value "POSTGRES_DB" "$NEW_DB" "$ENV_FILE"
update_env_value "POSTGRES_PORT" "$NEW_PORT" "$ENV_FILE"
update_env_value "CONTAINER_NAME" "$NEW_CONTAINER" "$ENV_FILE"

# 6. Переименование .env файла при изменении имени БД  
if [[ "$NEW_DB" != "$SERVICE_NAME" ]]; then
    echo "✅ Переименование .env.$SERVICE_NAME → .env.$NEW_SERVICE_NAME"
    mv "$ENV_FILE" "$PROJECT_ROOT/.env.$NEW_SERVICE_NAME"
    ENV_FILE="$PROJECT_ROOT/.env.$NEW_SERVICE_NAME"
fi

# 7. Обновление главного .env файла
NEED_UPDATE_MAIN_ENV=false
if [[ "$NEW_CONTAINER" != "$CURRENT_CONTAINER" ]] || [[ "$NEW_DB" != "$SERVICE_NAME" ]]; then
    echo "✅ Обновление главного .env файла"
    NEED_UPDATE_MAIN_ENV=true
    
    # Удаляем старую переменную если имя сервиса изменилось
    if [[ "$NEW_DB" != "$SERVICE_NAME" ]]; then
        OLD_SERVICE_UPPER="$(echo "${SERVICE_NAME}" | tr '[:lower:]' '[:upper:]')"
        OLD_CONTAINER_VAR="${OLD_SERVICE_UPPER}_CONTAINER_NAME"
        sed -i.bak "/^${OLD_CONTAINER_VAR}=/d" "$MAIN_ENV"
    fi
    
    # Добавляем/обновляем новую переменную
    NEW_SERVICE_UPPER="$(echo "${NEW_SERVICE_NAME}" | tr '[:lower:]' '[:upper:]')"
    NEW_CONTAINER_VAR="${NEW_SERVICE_UPPER}_CONTAINER_NAME"
    
    if grep -q "^${NEW_CONTAINER_VAR}=" "$MAIN_ENV"; then
        update_env_value "$NEW_CONTAINER_VAR" "$NEW_CONTAINER" "$MAIN_ENV"
    else
        echo "${NEW_CONTAINER_VAR}=${NEW_CONTAINER}" >> "$MAIN_ENV"
    fi
    rm -f "${MAIN_ENV}.bak"
fi

# 7.1. Переименование всех бэкапов при изменении имени БД
if [[ "$NEW_DB" != "$CURRENT_DB" ]]; then
    echo "🔄 Переименование ВСЕХ существующих бэкапов: *_* → ${NEW_DB}_*"
    
    if [[ -d "$PROJECT_ROOT/db_backups/$SERVICE_NAME" ]]; then
        # Находим все файлы бэкапов независимо от их текущего префикса
        find "$PROJECT_ROOT/db_backups/$SERVICE_NAME" -name "*_*.sql*" -type f | while read -r old_file; do
            # Извлекаем только временную часть после последнего префикса_
            filename=$(basename "$old_file")
            # Берем все после первого _ (это и есть временная метка + расширение)
            timestamp_part="${filename#*_}"
            
            # Создаем новое имя с правильным префиксом
            dir_path=$(dirname "$old_file")
            new_file="$dir_path/${NEW_DB}_${timestamp_part}"
            
            if [[ "$old_file" != "$new_file" ]]; then
                mv "$old_file" "$new_file"
                echo "  📄 $(basename "$old_file") → $(basename "$new_file")"
            fi
        done
        echo "✅ Все файлы бэкапов переименованы с префиксом ${NEW_DB}_"
    fi
fi

# 8. Обновление compose.yaml
echo "✅ Обновление compose.yaml"

# Переименование сервиса в compose.yaml если изменилось имя БД
if [[ "$NEW_DB" != "$SERVICE_NAME" ]]; then
    # Используем ту же AWK логику что и в remove_service.sh для замены блока сервиса
    awk "
        # Если нашли старый сервис - заменяем его имя на новое
        /^[[:space:]]*${SERVICE_NAME}:/ {
            in_current_service = 1      # <— ДОБАВЛЕНО: вошли в блок сервиса
            gsub(\"${SERVICE_NAME}:\", \"${NEW_SERVICE_NAME}:\")
            print
            next
        }

        # Обновляем env_file path внутри блока сервиса
        /env_file: \\.env\\.${SERVICE_NAME}/ {
            gsub(\"\\.env\\.${SERVICE_NAME}\", \".env.${NEW_SERVICE_NAME}\")
            print  
            next
        }
        # Обновляем healthcheck команду с именем БД
        /pg_isready -U .* -d ${SERVICE_NAME}/ {
            gsub(\"-d ${SERVICE_NAME}\", \"-d ${NEW_SERVICE_NAME}\")
            print
            next
        }
        # Обновляем volume path
        /\\.\\/data\\/${SERVICE_NAME}:/ {
            gsub(\"\\.\/data\/${SERVICE_NAME}:\", \"./data/${NEW_SERVICE_NAME}:\")
            print
            next
        }
        # Обновляем переменную контейнера только внутри блока текущего сервиса
        /^[[:space:]]*[a-zA-Z_][a-zA-Z0-9_]*:/ && !\$0 ~ /${SERVICE_NAME}:/ && in_current_service == 1 { in_current_service = 0 }
        in_current_service == 1 && /container_name: \\\$\\{[A-Z_]*CONTAINER_NAME\\}/ {
            # Используем ИМЕНА СЕРВИСА (бывш. и новый), т.к. переименовываем сервис
            current_service_upper = toupper(\"${SERVICE_NAME}\")
            new_service_upper = toupper(\"${NEW_SERVICE_NAME}\")
            gsub(\"-\", \"_\", current_service_upper)
            gsub(\"-\", \"_\", new_service_upper)
            gsub(\"\\\$\\{\" current_service_upper \"_CONTAINER_NAME\\}\",
                 \"\\\$\\{\" new_service_upper     \"_CONTAINER_NAME\\}\")
            print
            next
        }
        # Все остальные строки печатаем как есть
        { print }
    " "$COMPOSE_FILE" > "$COMPOSE_FILE.tmp"
    mv "$COMPOSE_FILE.tmp" "$COMPOSE_FILE"
fi

# Дополнительные обновления в compose.yaml (container_name, healthcheck, порт)
COMPOSE_UPDATED=false

# Читаем актуальные значения из compose.yaml для текущего сервиса
ACTUAL_CONTAINER_IN_COMPOSE=$(grep -A 10 "^[[:space:]]*${NEW_SERVICE_NAME}:" "$COMPOSE_FILE" | grep "container_name:" | sed 's/.*container_name: *//' | sed 's/\${[^}]*}//g' || echo "")
ACTUAL_USER_IN_COMPOSE=$(grep -A 20 "^[[:space:]]*${NEW_SERVICE_NAME}:" "$COMPOSE_FILE" | grep "pg_isready -U" | sed 's/.*pg_isready -U *\([^"]*\).*/\1/' | sed 's/\$\$//' | sed 's/]].*//' || echo "")
ACTUAL_PORT_IN_COMPOSE=$(grep -A 15 "^[[:space:]]*${NEW_SERVICE_NAME}:" "$COMPOSE_FILE" | grep -E "(\[\"0\.0\.0\.0:|\- \")" | sed -E 's/.*(0\.0\.0\.0:|")([0-9]+):5432.*/\2/' | head -1 || echo "")

# Обновляем порт если изменился (используем фактический порт из compose.yaml, а не из .env)
if [[ -n "$ACTUAL_PORT_IN_COMPOSE" && "$ACTUAL_PORT_IN_COMPOSE" != "$NEW_PORT" ]]; then
    echo "🔄 Обновление порта: $ACTUAL_PORT_IN_COMPOSE → $NEW_PORT"
    # Простые и надежные замены для двух форматов портов
    # Формат 1: ["0.0.0.0:port:5432"]
    sed -i.bak "s/\"0\\.0\\.0\\.0:${ACTUAL_PORT_IN_COMPOSE}:5432\"/\"0.0.0.0:${NEW_PORT}:5432\"/" "$COMPOSE_FILE"
    # Формат 2: - "port:5432" 
    sed -i.bak "s/\"${ACTUAL_PORT_IN_COMPOSE}:5432\"/\"${NEW_PORT}:5432\"/" "$COMPOSE_FILE"
    COMPOSE_UPDATED=true
fi

# Обновляем container_name если он отличается от нужного
CONTAINER_LINE_IN_COMPOSE=$(grep -A 10 "^[[:space:]]*${NEW_SERVICE_NAME}:" "$COMPOSE_FILE" | grep "container_name:" || echo "")
if [[ -n "$CONTAINER_LINE_IN_COMPOSE" ]]; then
    # Проверяем используется ли переменная или прямое значение
    if [[ "$CONTAINER_LINE_IN_COMPOSE" =~ \$\{ ]]; then
        # Используется переменная типа ${VAR_NAME} - заменяем на прямое значение
        echo "🔄 Обновление container_name: переменная → $NEW_CONTAINER"
        # Исправленный regex без экранирования в sed
        sed -i.bak "/^[[:space:]]*${NEW_SERVICE_NAME}:/,/^[[:space:]]*[a-zA-Z_][a-zA-Z0-9_-]*:/ s/container_name: \${[^}]*}/container_name: ${NEW_CONTAINER}/" "$COMPOSE_FILE"
        COMPOSE_UPDATED=true
    elif [[ -n "$ACTUAL_CONTAINER_IN_COMPOSE" && "$ACTUAL_CONTAINER_IN_COMPOSE" != "$NEW_CONTAINER" ]]; then
        # Прямое значение, просто заменяем
        echo "🔄 Обновление container_name: $ACTUAL_CONTAINER_IN_COMPOSE → $NEW_CONTAINER"
        sed -i.bak "s/container_name: ${ACTUAL_CONTAINER_IN_COMPOSE}/container_name: ${NEW_CONTAINER}/" "$COMPOSE_FILE"
        COMPOSE_UPDATED=true
    fi
fi

# Обновляем healthcheck команду если пользователь отличается от нужного  
# Важно: проверяем нужно ли вообще обновлять healthcheck (если используется $$POSTGRES_USER, то не нужно)
if [[ -n "$ACTUAL_USER_IN_COMPOSE" && "$ACTUAL_USER_IN_COMPOSE" != "$NEW_USER" && "$ACTUAL_USER_IN_COMPOSE" != "POSTGRES_USER" ]]; then
    echo "🔄 Обновление healthcheck: pg_isready -U $ACTUAL_USER_IN_COMPOSE → pg_isready -U $NEW_USER"
    # Простое решение: точная замена строки с pg_isready
    sed -i.bak "s/pg_isready -U ${ACTUAL_USER_IN_COMPOSE}/pg_isready -U ${NEW_USER}/g" "$COMPOSE_FILE"
    COMPOSE_UPDATED=true
elif [[ "$ACTUAL_USER_IN_COMPOSE" == "POSTGRES_USER" ]]; then
    echo "ℹ️  Healthcheck использует переменную $$POSTGRES_USER - обновление не требуется"
fi

if [[ "$COMPOSE_UPDATED" == "true" ]]; then
    rm -f "${COMPOSE_FILE}.bak"
fi

# 7.2. Переименование папок данных при изменении имени сервиса
if [[ "$NEW_DB" != "$SERVICE_NAME" ]]; then
    echo "✅ Переименование папок данных"
    if [[ -d "$PROJECT_ROOT/data/$SERVICE_NAME" ]]; then
        mv "$PROJECT_ROOT/data/$SERVICE_NAME" "$PROJECT_ROOT/data/$NEW_SERVICE_NAME"
    fi
    if [[ -d "$PROJECT_ROOT/db_backups/$SERVICE_NAME" ]]; then
        mv "$PROJECT_ROOT/db_backups/$SERVICE_NAME" "$PROJECT_ROOT/db_backups/$NEW_SERVICE_NAME"
    fi
fi

# 8. Запуск контейнера ТОЛЬКО если он был остановлен
if [[ "$NEED_CONTAINER_RESTART" == "true" ]]; then
    echo "✅ Запуск контейнера с новыми настройками"
    docker compose up "$NEW_SERVICE_NAME" -d
    
    # 9. Проверка готовности после перезапуска
    echo "⏳ Ожидание готовности БД после перезапуска..."
    for i in {1..30}; do
        if docker exec "$NEW_CONTAINER" pg_isready -U "$NEW_USER" -d "$NEW_DB" 2>/dev/null; then
            echo "✅ БД готова к работе"
            break
        fi
        sleep 1
    done
else
    echo "✅ Контейнер остается запущенным - Docker настройки не изменились"
    # Проверяем что БД доступна в работающем контейнере
    if docker exec "$NEW_CONTAINER" pg_isready -U "$NEW_USER" -d "$NEW_DB" 2>/dev/null; then
        echo "✅ БД работает корректно"
    else
        echo "⚠️  Проблема с доступом к БД после изменений"
    fi
fi

# 10. Применение SQL изменений ПОСЛЕ перезапуска контейнера (если нужно)
SQL_CHANGES_NEEDED=false

# Принудительно применяем SQL изменения если был перезапуск контейнера
# (так как при перезапуске SQL изменения теряются)
if [[ "$NEED_CONTAINER_RESTART" == "true" ]]; then
    SQL_CHANGES_NEEDED=true
    echo "🔄 ПРИНУДИТЕЛЬНОЕ применение SQL изменений после перезапуска контейнера"
elif [[ "$NEW_PASSWORD" != "$CURRENT_PASSWORD" ]] || [[ "$NEW_USER" != "$CURRENT_USER" ]] || [[ "$NEW_DB" != "$CURRENT_DB" ]]; then
    SQL_CHANGES_NEEDED=true
    echo "🔄 Применение SQL изменений в работающем контейнере"
fi

if [[ "$SQL_CHANGES_NEEDED" == "true" ]]; then
    
    # Определяем правильный пользователь для аутентификации
    # ВАЖНО: PostgreSQL НЕ создает нового пользователя при перезапуске с существующими данными!
    # Поэтому нужно проверить какой пользователь реально существует
    
    echo "🔄 Применение SQL изменений в контейнере $NEW_CONTAINER..."
    echo "🔍 Определение правильного пользователя для аутентификации..."
    
    # Пробуем подключиться разными пользователями и находим рабочий
    AUTH_USER=""
    AUTH_DB="postgres"  # Используем системную БД postgres для аутентификации
    
    # Сначала пробуем текущего пользователя (если контейнер не перезапускался)
    if [[ "$NEED_CONTAINER_RESTART" != "true" ]]; then
        if docker exec "$NEW_CONTAINER" psql -U "$CURRENT_USER" -d postgres -c "SELECT 1;" &>/dev/null; then
            AUTH_USER="$CURRENT_USER"
            echo "✅ Используем текущего пользователя: $CURRENT_USER"
        fi
    fi
    
    # Если не нашли, пробуем нового пользователя
    if [[ -z "$AUTH_USER" ]]; then
        if docker exec "$NEW_CONTAINER" psql -U "$NEW_USER" -d postgres -c "SELECT 1;" &>/dev/null; then
            AUTH_USER="$NEW_USER"
            echo "✅ Используем нового пользователя: $NEW_USER"
        fi
    fi
    
    # Если не нашли, пробуем стандартных пользователей
    if [[ -z "$AUTH_USER" ]]; then
        for try_user in "admin" "postgres" "$CURRENT_USER" "$NEW_USER"; do
            if docker exec "$NEW_CONTAINER" psql -U "$try_user" -d postgres -c "SELECT 1;" &>/dev/null; then
                AUTH_USER="$try_user"
                echo "✅ Найден рабочий пользователь: $AUTH_USER"
                break
            fi
        done
    fi
    
    if [[ -z "$AUTH_USER" ]]; then
        echo "❌ КРИТИЧЕСКАЯ ОШИБКА: Не найден ни один пользователь для аутентификации!"
        echo "🔍 Список всех пользователей в контейнере:"
        docker exec "$NEW_CONTAINER" psql --help &>/dev/null || echo "PostgreSQL недоступен"
        exit 1
    fi
    
    echo "🔑 Аутентификация: пользователь=$AUTH_USER, БД=$AUTH_DB"
    
    # Определяем РЕАЛЬНУЮ пользовательскую БД и её владельца
    echo "🔍 Определение реальных данных в контейнере..."
    
    # Находим реальную пользовательскую БД (не системную)
    REAL_DB_NAME=$(docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -t -c \
        "SELECT datname FROM pg_database WHERE datname NOT IN ('postgres', 'template0', 'template1') ORDER BY datname LIMIT 1;" 2>/dev/null | xargs)
    
    # Находим владельца этой БД
    REAL_DB_OWNER=""
    if [[ -n "$REAL_DB_NAME" ]]; then
        REAL_DB_OWNER=$(docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -t -c \
            "SELECT datdba::regrole FROM pg_database WHERE datname = '$REAL_DB_NAME';" 2>/dev/null | xargs)
        echo "✅ Найдена реальная БД: '$REAL_DB_NAME' (владелец: $REAL_DB_OWNER)"
    else
        echo "⚠️  Пользовательская БД не найдена"
    fi
    
    # Проверяем что контейнер запущен
    if docker ps --format '{{.Names}}' | grep -q "^${NEW_CONTAINER}$"; then
        
        # Логика уже выполнена выше
        
        # Изменение пароля РЕАЛЬНОГО пользователя
        if [[ "$NEW_PASSWORD" != "$CURRENT_PASSWORD" ]] && [[ "$NEED_CONTAINER_RESTART" != "true" ]]; then
            # Используем реального владельца БД, а не значение из .env
            TARGET_USER="$REAL_DB_OWNER"
            if [[ -z "$TARGET_USER" ]]; then
                TARGET_USER="$CURRENT_USER"  # fallback к .env значению
                echo "⚠️  Владелец БД не определён, используем .env: $TARGET_USER"
            fi
            
            echo "🔑 Изменение пароля реального пользователя: $TARGET_USER"
            docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d "$AUTH_DB" -c \
                "ALTER USER $TARGET_USER PASSWORD '$NEW_PASSWORD';" || {
                echo "❌ Ошибка изменения пароля для $TARGET_USER"; exit 1;
            }
        fi
        
        # Создание нового пользователя если имя изменилось
        if [[ "$NEW_USER" != "$CURRENT_USER" ]]; then
            echo "👤 Создание нового пользователя $NEW_USER с максимальными правами"
            
            # Надёжная проверка существования пользователя через postgres БД
            USER_EXISTS=$(docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -t -c \
                "SELECT 1 FROM pg_roles WHERE rolname='$NEW_USER';" 2>/dev/null | tr -d ' \n\r' || echo "")
            
            if [[ "$USER_EXISTS" != "1" ]]; then
                echo "🔧 Пользователь $NEW_USER не найден, создаю..."
                docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                    "CREATE USER $NEW_USER WITH SUPERUSER CREATEDB CREATEROLE REPLICATION BYPASSRLS PASSWORD '$NEW_PASSWORD';" || {
                    echo "❌ Ошибка создания пользователя $NEW_USER"; exit 1;
                }
                echo "✅ Пользователь $NEW_USER создан с максимальными правами"
            else
                echo "ℹ️  Пользователь $NEW_USER уже существует, обновляю пароль..."
                docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                    "ALTER USER $NEW_USER PASSWORD '$NEW_PASSWORD';" || {
                    echo "❌ Ошибка обновления пароля для $NEW_USER"; exit 1;
                }
            fi
        fi
        
        # Переименование БД если нужно
        # Используем реальную БД, определённую выше
        SOURCE_DB_NAME="$REAL_DB_NAME"
        TARGET_DB_NAME="$NEW_DB"
        
        if [[ -z "$SOURCE_DB_NAME" ]]; then
            echo "⚠️  Пользовательская БД не найдена, используем имя из .env: $CURRENT_DB"
            SOURCE_DB_NAME="$CURRENT_DB"
        fi
        
        echo "📋 Переименование: $SOURCE_DB_NAME → $TARGET_DB_NAME"
        
        # Проверяем нужно ли переименование БД
        if [[ "$TARGET_DB_NAME" != "$SOURCE_DB_NAME" ]]; then
            echo "🗃️  Переименование базы данных: $SOURCE_DB_NAME → $TARGET_DB_NAME"
            
            # СНАЧАЛА запретим новые подключения
            echo "🚫 ПЕРВЫМ ДЕЛОМ: Запрет новых подключений к БД $SOURCE_DB_NAME"
            docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                "UPDATE pg_database SET datallowconn = false WHERE datname = '$SOURCE_DB_NAME';" || {
                echo "❌ Ошибка запрета подключений"; exit 1;
            }
            
            # Агрессивное закрытие подключений с повторными попытками
            echo "🔒 Агрессивное закрытие всех подключений к БД $SOURCE_DB_NAME"
            
            # Повторные попытки закрытия подключений (максимум 5 попыток)
            for attempt in {1..5}; do
                echo "🔄 Попытка $attempt: Принудительное закрытие подключений"
                
                # Закроем все активные подключения
                TERMINATED=$(docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity 
                     WHERE datname='$SOURCE_DB_NAME' AND pid <> pg_backend_pid();" 2>/dev/null | grep -c "t" || echo "0")
                
                # Проверим, остались ли активные подключения
                ACTIVE_CONNECTIONS=$(docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                    "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='$SOURCE_DB_NAME';" 2>/dev/null | grep -o '[0-9]\+' | head -1 || echo "1")
                
                echo "🔍 Закрыто подключений: $TERMINATED, активных подключений: $ACTIVE_CONNECTIONS"
                
                if [[ "$ACTIVE_CONNECTIONS" == "0" ]]; then
                    echo "✅ Все подключения к БД $CURRENT_DB закрыты"
                    break
                fi
                
                echo "⏳ Ожидание 3 секунды перед повторной попыткой..."
                sleep 3
                
                if [[ $attempt == 5 ]]; then
                    echo "❌ Не удалось закрыть все подключения после 5 попыток"
                    echo "🔍 Активные подключения:"
                    docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                        "SELECT pid, usename, datname, application_name, client_addr, state FROM pg_stat_activity WHERE datname='$SOURCE_DB_NAME';" 2>/dev/null || true
                    # Восстанавливаем подключения при неудаче
                    docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                        "UPDATE pg_database SET datallowconn = true WHERE datname = '$CURRENT_DB';" 2>/dev/null || true
                    exit 1
                fi
            done
            
            # Дополнительная пауза для уверенности
            echo "⏳ Финальная пауза перед переименованием..."
            sleep 2
            
            # Финальная проверка перед переименованием
            FINAL_CHECK=$(docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='$SOURCE_DB_NAME';" 2>/dev/null | grep -o '[0-9]\+' | head -1 || echo "1")
            
            if [[ "$FINAL_CHECK" != "0" ]]; then
                echo "❌ КРИТИЧЕСКАЯ ОШИБКА: Все еще есть $FINAL_CHECK активных подключений к БД $CURRENT_DB"
                echo "🔍 Список активных подключений:"
                docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                    "SELECT pid, usename, datname, application_name, client_addr, state FROM pg_stat_activity WHERE datname='$SOURCE_DB_NAME';" 2>/dev/null || true
                # Восстанавливаем подключения при неудаче
                docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                    "UPDATE pg_database SET datallowconn = true WHERE datname = '$SOURCE_DB_NAME';" 2>/dev/null || true
                exit 1
            fi
            
            # Переименование БД
            echo "🚀 Выполняю переименование БД: $SOURCE_DB_NAME → $TARGET_DB_NAME"
            docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                "ALTER DATABASE $SOURCE_DB_NAME RENAME TO $TARGET_DB_NAME;" || {
                echo "❌ Ошибка переименования БД"; 
                # Восстанавливаем подключения при ошибке
                docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                    "UPDATE pg_database SET datallowconn = true WHERE datname = '$SOURCE_DB_NAME';" 2>/dev/null || true
                exit 1;
            }
            
            # Разрешение подключений к переименованной БД
            echo "✅ Разрешение подключений к переименованной БД $TARGET_DB_NAME"
            docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                "UPDATE pg_database SET datallowconn = true WHERE datname = '$TARGET_DB_NAME';" || {
                echo "❌ Ошибка разрешения подключений"; exit 1;
            }
        fi
        
        # Передача владения БД новому пользователю
        if [[ "$NEW_USER" != "$CURRENT_USER" ]]; then
            # Используем актуальное имя БД (после возможного переименования)
            ACTUAL_DB_NAME="$TARGET_DB_NAME"
            echo "👑 Передача владения БД $ACTUAL_DB_NAME пользователю $NEW_USER"
            docker exec "$NEW_CONTAINER" psql -U "$AUTH_USER" -d postgres -c \
                "ALTER DATABASE $ACTUAL_DB_NAME OWNER TO $NEW_USER;" || {
                echo "❌ Ошибка передачи владения БД"; exit 1;
            }
        fi
        
        echo "✅ Все SQL изменения применены в контейнере $NEW_CONTAINER"
    else
        echo "⚠️  Контейнер $NEW_CONTAINER не запущен - SQL изменения пропущены"
    fi
fi


echo ""
echo "🎉 Настройки успешно обновлены!"
echo ""
echo "🔗 Новые данные для подключения:"
echo "  Host: localhost"
echo "  Port: $NEW_PORT"
echo "  Database: $NEW_DB"
echo "  Username: $NEW_USER"
echo "  Password: $NEW_PASSWORD"
echo "  Container: $NEW_CONTAINER"

# Информация о созданном бэкапе
if [[ "$BACKUP_CREATED" == "true" ]]; then
    echo ""
    echo "💾 Бэкап был создан перед изменениями и сохранен в папке db_backups/$SERVICE_NAME"
    echo "📋 Для восстановления данных (при необходимости) используйте:"
    echo "   bash ops/backup/restore.sh $NEW_SERVICE_NAME"
fi