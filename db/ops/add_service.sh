#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# Скрипт автоматического добавления нового PostgreSQL сервиса
# ===================================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/../" && pwd)"


# Функция поиска свободного порта
find_next_port() {
    local start_port=5401
    local max_port=5500
    
    # Собираем все используемые порты из .env файлов И из compose.yaml
    local env_ports=($(grep "POSTGRES_PORT=" "$PROJECT_ROOT"/.env.* 2>/dev/null | cut -d'=' -f2 | sort -n || true))
    local compose_ports=($(grep -E '^\s*-\s*"[0-9]+:5432"' "$PROJECT_ROOT/compose.yaml" 2>/dev/null | sed -E 's/.*"([0-9]+):5432".*/\1/' | sort -n || true))
    
    # Объединяем все порты в один массив, безопасно обрабатывая пустые массивы
    local all_ports=()
    if [[ ${#env_ports[@]} -gt 0 ]]; then
        all_ports+=("${env_ports[@]}")
    fi
    if [[ ${#compose_ports[@]} -gt 0 ]]; then
        all_ports+=("${compose_ports[@]}")
    fi
    
    # Убираем дубликаты и сортируем
    if [[ ${#all_ports[@]} -gt 0 ]]; then
        all_ports=($(printf '%s\n' "${all_ports[@]}" | sort -n | uniq))
    fi
    
    # Ищем первый свободный порт начиная со стартового
    for port in $(seq $start_port $max_port); do
        local found=false
        if [[ ${#all_ports[@]} -gt 0 ]]; then
            for used_port in "${all_ports[@]}"; do
                if [[ "$port" == "$used_port" ]]; then
                    found=true
                    break
                fi
            done
        fi
        if [[ "$found" == "false" ]]; then
            echo "$port"
            return
        fi
    done
    
    # Если все порты заняты, предлагаем после последнего
    if [[ ${#all_ports[@]} -gt 0 ]]; then
        echo $((${all_ports[-1]} + 1))
    else
        echo $start_port
    fi
}

# Функция показа справки
show_help() {
    local next_port=$(find_next_port)
    echo "🛠️  Добавление нового PostgreSQL сервиса"
    echo ""
    echo "Использование: $0 <имя_сервиса> [порт] [пароль]"
    echo ""
    echo "Параметры:"
    echo "  имя_сервиса  - имя нового сервиса (например: new_service)"
    echo "  порт         - порт для подключения (необязательно, автоматически: $next_port)"
    echo "  пароль       - пароль для БД (необязательно, по умолчанию: Password123)"
    echo ""
    echo "Примеры:"
    echo "  $0 new_service                    # Автопорт: $next_port"
    echo "  $0 analytics_db 5408             # Свой порт: 5408"  
    echo "  $0 new_service \"\" MySecretPass    # Автопорт + свой пароль"
    echo ""
    echo "Что делает скрипт:"
    echo "  ✅ Создает .env.{сервис} файл с настройками"
    echo "  ✅ Добавляет сервис в compose.yaml"
    echo "  ✅ Обновляет главный .env файл"
    echo "  ✅ Создает папки для данных и бэкапов"
    echo "  ✅ Готовый к запуску сервис!"
}

# Проверка аргументов
SERVICE_NAME="${1:-}"
PORT="${2:-}"
PASSWORD="${3:-Password123}"

if [[ -z "$SERVICE_NAME" ]]; then
    show_help
    exit 1
fi

# Автоматический выбор порта если не указан или пустой
if [[ -z "$PORT" ]]; then
    PORT=$(find_next_port)
    echo "🎯 Автоматически выбран порт: $PORT"
fi

# Проверка корректности имени
if [[ ! "$SERVICE_NAME" =~ ^[a-zA-Z0-9_]+$ ]]; then
    echo "❌ Имя сервиса должно содержать только буквы, цифры и подчеркивания"
    exit 1
fi

# Проверка корректности порта
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || [[ "$PORT" -lt 1024 ]] || [[ "$PORT" -gt 65535 ]]; then
    echo "❌ Порт должен быть числом от 1024 до 65535"
    exit 1
fi

echo "🔧 Создание нового сервиса: $SERVICE_NAME"
echo "📍 Порт: $PORT"
echo "🔒 Пароль: $PASSWORD"
echo ""

# Проверяем, что сервис не существует
ENV_FILE="$PROJECT_ROOT/.env.$SERVICE_NAME"
if [[ -f "$ENV_FILE" ]]; then
    echo "❌ Сервис $SERVICE_NAME уже существует!"
    echo "   Файл: $ENV_FILE"
    exit 1
fi

# Проверяем, что порт не занят (в .env файлах и compose.yaml)
if grep -q "POSTGRES_PORT=$PORT" "$PROJECT_ROOT"/.env.* 2>/dev/null || grep -q "\"$PORT:5432\"" "$PROJECT_ROOT/compose.yaml" 2>/dev/null; then
    echo "❌ Порт $PORT уже используется другим сервисом!"
    echo "   Используемые порты в .env файлах:"
    grep "POSTGRES_PORT=" "$PROJECT_ROOT"/.env.* 2>/dev/null | sed 's/.*POSTGRES_PORT=/  /' || echo "  (нет)"
    echo "   Используемые порты в compose.yaml:"
    grep -E '^\s*-\s*"[0-9]+:5432"' "$PROJECT_ROOT/compose.yaml" 2>/dev/null | sed -E 's/.*"([0-9]+):5432".*/  \1/' || echo "  (нет)"
    exit 1
fi

# 1. Создаем .env файл для нового сервиса
echo "✅ Создание .env.$SERVICE_NAME"
cat > "$ENV_FILE" << EOF
POSTGRES_USER=admin
POSTGRES_PASSWORD=$PASSWORD
POSTGRES_DB=$SERVICE_NAME
POSTGRES_PORT=$PORT
CONTAINER_NAME=${SERVICE_NAME}_db

# Персональные настройки бэкапов (переопределяют глобальные)
# BACKUP_ENABLED=true
# BACKUP_RETENTION_DAYS=14
# BACKUP_COMPRESSION="gzip"
EOF

# 2. Обновляем главный .env файл
echo "✅ Обновление главного .env файла"
MAIN_ENV="$PROJECT_ROOT/.env"
SERVICE_UPPER="$(echo "${SERVICE_NAME}" | tr '[:lower:]' '[:upper:]')"
CONTAINER_VAR="${SERVICE_UPPER}_CONTAINER_NAME"
echo "${CONTAINER_VAR}=${SERVICE_NAME}_db" >> "$MAIN_ENV"

# 3. Добавляем сервис в compose.yaml
echo "✅ Обновление compose.yaml"
COMPOSE_FILE="$PROJECT_ROOT/compose.yaml"

# Создаем временный файл с новым сервисом
NEW_SERVICE_YAML=$(cat << EOF

  $SERVICE_NAME:
    image: postgres:latest
    container_name: ${SERVICE_NAME}_db
    env_file: .env.$SERVICE_NAME
    ports:
      - "${PORT}:5432"
    volumes:
      - ./data/$SERVICE_NAME:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U admin"]
      interval: 10s
      timeout: 5s
      retries: 15
EOF
)

# Добавляем новый сервис в конец секции services
if grep -q "^services:" "$COMPOSE_FILE"; then
    echo "$NEW_SERVICE_YAML" >> "$COMPOSE_FILE"
else
    echo "❌ Не найдена секция services в compose.yaml"
    exit 1
fi

# 4. Создаем необходимые папки
echo "✅ Создание папок для данных и бэкапов"
mkdir -p "$PROJECT_ROOT/data/$SERVICE_NAME"
mkdir -p "$PROJECT_ROOT/db_backups/$SERVICE_NAME"

# 5. Устанавливаем права доступа
chmod 755 "$PROJECT_ROOT/data/$SERVICE_NAME"
chmod 755 "$PROJECT_ROOT/db_backups/$SERVICE_NAME"

echo ""

# Автоочистка контейнеров-призраков после создания сервиса
if [[ -f "$PROJECT_ROOT/ops/cleanup_orphans.sh" ]]; then
    echo "🧹 Проверка контейнеров-призраков..."
    bash "$PROJECT_ROOT/ops/cleanup_orphans.sh" --kill 2>/dev/null || true
fi

echo "🎉 Сервис $SERVICE_NAME успешно создан!"
echo ""
echo "📋 Детали сервиса:"
echo "  Имя контейнера: ${SERVICE_NAME}_db"
echo "  База данных: $SERVICE_NAME"
echo "  Пользователь: admin"
echo "  Пароль: $PASSWORD"
echo "  Порт: $PORT"
echo "  Данные: ./data/$SERVICE_NAME"
echo "  Бэкапы: ./db_backups/$SERVICE_NAME"
echo ""
echo "🚀 Команды для запуска:"
echo "  docker compose up $SERVICE_NAME -d    # Запустить только этот сервис"
echo "  docker compose up -d                  # Запустить все сервисы"
echo ""
echo "🔗 Подключение к БД:"
echo "  Host: localhost"
echo "  Port: $PORT"
echo "  Database: $SERVICE_NAME"
echo "  Username: admin"
echo "  Password: $PASSWORD"
echo ""
echo "📦 Управление бэкапами:"
echo "  bash ops/backup/backup_pg.sh $SERVICE_NAME        # Создать бэкап"
echo "  ops/backup/restore.sh $SERVICE_NAME -y            # Восстановить бэкап"

# 6. Автоматический запуск контейнера
echo ""
echo "🚀 Запуск контейнера..."
cd "$PROJECT_ROOT"
if docker compose up "$SERVICE_NAME" -d; then
    echo "✅ Контейнер $SERVICE_NAME успешно запущен!"
    echo ""
    echo "🔍 Ожидание готовности базы данных..."
    sleep 3
    
    # Проверяем готовность базы
    if docker exec -it "${SERVICE_NAME}_db" pg_isready -U admin -d "$SERVICE_NAME" >/dev/null 2>&1; then
        echo "✅ База данных готова к подключению!"
    else
        echo "⚠️  База данных еще инициализируется. Подождите несколько секунд."
    fi
else
    echo "❌ Ошибка при запуске контейнера!"
    echo "   Попробуйте запустить вручную:"
    echo "   docker compose up $SERVICE_NAME -d"
fi