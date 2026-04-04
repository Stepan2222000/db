#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# Простая очистка контейнеров-призраков
# ===================================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/../" && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/compose.yaml"
ENV_FILE="$PROJECT_ROOT/.env"

# Функция чтения переменной из .env
get_env_var() {
    local var_name="$1"
    local env_file="${2:-$ENV_FILE}"
    local default_value="${3:-}"
    
    if [[ -f "$env_file" ]]; then
        grep -E "^[[:space:]]*$var_name[[:space:]]*=" "$env_file" 2>/dev/null | tail -n1 \
            | sed -E 's/^[^=]+=[[:space:]]*//; s/^[\"\x27]|[\"\x27]$//g' || echo "$default_value"
    else
        echo "$default_value"
    fi
}

# Основная функция поиска и удаления призраков
find_and_cleanup_orphans() {
    local quiet_mode="${1:-false}"
    local kill_mode="${2:-false}"
    
    [[ "$quiet_mode" == "false" ]] && echo "🔍 Поиск контейнеров-призраков..."

    # Получаем законные контейнеры из всех источников
    local LEGAL_CONTAINERS=()

    # 1. Из compose.yaml (container_name)
    if [[ -f "$COMPOSE_FILE" ]]; then
        while IFS= read -r line; do
            if [[ "$line" =~ ^[[:space:]]*container_name:[[:space:]]*(.+)$ ]]; then
                container_name="${BASH_REMATCH[1]}"
                container_name="${container_name//\"/}"  # убираем кавычки
                LEGAL_CONTAINERS+=("$container_name")
            fi
        done < "$COMPOSE_FILE"
    fi

    # 2. Из переменных *_CONTAINER_NAME в главном .env
    if [[ -f "$ENV_FILE" ]]; then
        while IFS= read -r line; do
            if [[ "$line" =~ ^[[:space:]]*[A-Z_]*CONTAINER_NAME[[:space:]]*=[[:space:]]*(.+)$ ]]; then
                container_name="${BASH_REMATCH[1]}"
                container_name="${container_name//\"/}"  # убираем кавычки
                if [[ -n "$container_name" ]]; then
                    LEGAL_CONTAINERS+=("$container_name")
                fi
            fi
        done < "$ENV_FILE"
    fi

    # 3. Из переменной CONTAINER_NAME в .env.<service> файлах
    for env_file in "$PROJECT_ROOT"/.env.*; do
        if [[ -f "$env_file" && "$env_file" =~ \.env\.[a-zA-Z0-9_]+$ ]]; then
            if grep -q "^[[:space:]]*CONTAINER_NAME[[:space:]]*=" "$env_file" 2>/dev/null; then
                container_name=$(get_env_var "CONTAINER_NAME" "$env_file" "")
                if [[ -n "$container_name" && "$container_name" != "$env_file" ]]; then
                    LEGAL_CONTAINERS+=("$container_name")
                fi
            fi
        fi
    done

    # Убираем дубликаты
    if [[ ${#LEGAL_CONTAINERS[@]} -gt 0 ]]; then
        LEGAL_CONTAINERS=($(printf '%s\n' "${LEGAL_CONTAINERS[@]}" | sort | uniq))
    fi

    [[ "$quiet_mode" == "false" ]] && echo "📋 Законные контейнеры: ${LEGAL_CONTAINERS[*]:-нет}"

    # Получаем все контейнеры и фильтруем PostgreSQL по образу
    local ALL_POSTGRES_CONTAINERS=()
    while IFS= read -r container_info; do
        if [[ -n "$container_info" ]]; then
            container_name=$(echo "$container_info" | cut -d$'\t' -f1)
            image=$(echo "$container_info" | cut -d$'\t' -f2)
            if [[ "$image" =~ postgres ]]; then
                ALL_POSTGRES_CONTAINERS+=("$container_name")
            fi
        fi
    done < <(docker ps -a --format '{{.Names}}\t{{.Image}}' 2>/dev/null || true)

    [[ "$quiet_mode" == "false" ]] && echo "🐳 PostgreSQL контейнеры: ${ALL_POSTGRES_CONTAINERS[*]:-нет}"

    # Ищем призраков
    local ORPHANS=()
    for container in "${ALL_POSTGRES_CONTAINERS[@]}"; do
        is_legal=false
        for legal in "${LEGAL_CONTAINERS[@]}"; do
            if [[ "$container" == "$legal" ]]; then
                is_legal=true
                break
            fi
        done
        
        if [[ "$is_legal" == "false" ]]; then
            ORPHANS+=("$container")
        fi
    done

    if [[ ${#ORPHANS[@]} -eq 0 ]]; then
        [[ "$quiet_mode" == "false" ]] && echo "✅ Контейнеров-призраков не найдено"
        return 0
    fi

    [[ "$quiet_mode" == "false" ]] && echo "👻 Найдены призраки: ${ORPHANS[*]}"

    # Если режим автоочистки или --kill, удаляем
    if [[ "$kill_mode" == "true" ]]; then
        [[ "$quiet_mode" == "false" ]] && echo "🗑️  Удаление призраков..."
        for orphan in "${ORPHANS[@]}"; do
            [[ "$quiet_mode" == "false" ]] && echo "  🛑 Остановка $orphan"
            docker stop "$orphan" 2>/dev/null || true
            [[ "$quiet_mode" == "false" ]] && echo "  ❌ Удаление $orphan"
            docker rm "$orphan" 2>/dev/null || true
        done
        [[ "$quiet_mode" == "false" ]] && echo "✅ Все призраки удалены!"
    else
        # Показываем информацию
        [[ "$quiet_mode" == "false" ]] && echo ""
        [[ "$quiet_mode" == "false" ]] && echo "📊 Подробная информация о призраках:"
        for orphan in "${ORPHANS[@]}"; do
            status=$(docker inspect --format '{{.State.Status}}' "$orphan" 2>/dev/null || echo "unknown")
            image=$(docker inspect --format '{{.Config.Image}}' "$orphan" 2>/dev/null || echo "unknown")
            [[ "$quiet_mode" == "false" ]] && echo "  🐳 $orphan: $status ($image)"
        done
    fi
}

# Функция автоочистки для интеграции в другие скрипты
run_auto_cleanup() {
    local quiet_mode="${1:-false}"
    
    # Определяем переменные если они не заданы
    local project_root="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../" && pwd)}"
    local env_file="${ENV_FILE:-$project_root/.env}"
    
    # Проверяем настройку
    local cleanup_enabled=$(get_env_var "CLEANUP_ORPHANS_ENABLED" "$env_file" "false")
    
    if [[ "$cleanup_enabled" != "true" ]]; then
        [[ "$quiet_mode" == "false" ]] && echo "ℹ️  Автоочистка отключена (CLEANUP_ORPHANS_ENABLED=false)"
        return 0
    fi
    
    [[ "$quiet_mode" == "false" ]] && echo "🧹 Автоочистка контейнеров-призраков..."
    
    # Временно устанавливаем переменные для функции
    local old_project_root="$PROJECT_ROOT"
    local old_env_file="$ENV_FILE"
    local old_compose_file="$COMPOSE_FILE"
    
    PROJECT_ROOT="$project_root"
    ENV_FILE="$env_file"
    COMPOSE_FILE="$project_root/compose.yaml"
    
    # Выполняем поиск и удаление
    find_and_cleanup_orphans "$quiet_mode" "true"
    
    # Восстанавливаем переменные
    PROJECT_ROOT="$old_project_root"
    ENV_FILE="$old_env_file"
    COMPOSE_FILE="$old_compose_file"
}

# ===================================================================
# ОСНОВНАЯ ЛОГИКА СКРИПТА (если вызван напрямую)
# ===================================================================

# Если скрипт запущен напрямую (не sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Проверяем настройку
    CLEANUP_ENABLED=$(get_env_var "CLEANUP_ORPHANS_ENABLED" "$ENV_FILE" "false")

    MODE="${1:-show}"

    if [[ "$MODE" == "--help" ]] || [[ "$MODE" == "-h" ]]; then
        echo "🧹 Очистка контейнеров-призраков"
        echo "Использование: $0 [режим]"
        echo ""
        echo "Режимы:"
        echo "  show (по умолчанию) - показать призраков"
        echo "  --dry-run          - показать что будет удалено"
        echo "  --kill             - удалить призраков"
        exit 0
    fi

    # Действия в зависимости от режима
    case "$MODE" in
        "show")
            find_and_cleanup_orphans "false" "false"
            ;;
        "--dry-run")
            find_and_cleanup_orphans "false" "false"
            echo ""
            echo "🗑️  В режиме --kill будут удалены все найденные призраки"
            ;;
        "--kill")
            if [[ "$CLEANUP_ENABLED" != "true" ]]; then
                echo "❌ Очистка отключена в .env (CLEANUP_ORPHANS_ENABLED=false)"
                echo "   Включите в .env: CLEANUP_ORPHANS_ENABLED=true"
                exit 1
            fi
            find_and_cleanup_orphans "false" "true"
            ;;
        *)
            echo "❌ Неизвестный режим: $MODE"
            echo "   Используйте: show, --dry-run, --kill или --help"
            exit 1
            ;;
    esac
fi