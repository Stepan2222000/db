#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# Скрипт управления автоматическими бэкапами PostgreSQL
# ===================================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/../../" && pwd)"
GLOBAL_ENV="$PROJECT_ROOT/.env"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/backup_pg.sh"

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

# Функция установки cron задачи
install_cron() {
    echo "🔧 Установка автоматических бэкапов..."
    
    # Читаем настройки
    BACKUP_ENABLED=$(get_env_var "BACKUP_ENABLED" "$GLOBAL_ENV" "true")
    BACKUP_SCHEDULE=$(get_env_var "BACKUP_SCHEDULE" "$GLOBAL_ENV" "0 2 * * *")
    LOG_FILE=$(get_env_var "BACKUP_LOG_FILE" "$GLOBAL_ENV" "$PROJECT_ROOT/ops/backup/backup.log")
    
    if [[ "$BACKUP_ENABLED" != "true" ]]; then
        echo "❌ Автобэкапы отключены в .env (BACKUP_ENABLED=false)"
        exit 1
    fi
    
    # Создаем задачу cron
    CRON_JOB="$BACKUP_SCHEDULE cd $PROJECT_ROOT && bash $BACKUP_SCRIPT >> $LOG_FILE 2>&1"
    
    # Добавляем в crontab (удаляем старую версию если есть)
    (crontab -l 2>/dev/null | grep -v "$BACKUP_SCRIPT" || true; echo "$CRON_JOB") | crontab -
    
    echo "✅ Автобэкапы настроены!"
    echo "📅 Расписание: $BACKUP_SCHEDULE"
    echo "📝 Логи: $LOG_FILE"
    echo ""
    echo "Примеры расписания cron:"
    echo "  0 2 * * *     - каждый день в 02:00"
    echo "  0 3 * * 0     - каждое воскресенье в 03:00"
    echo "  0 */6 * * *   - каждые 6 часов"
    echo "  30 1 1 * *    - 1 число каждого месяца в 01:30"
}

# Функция удаления cron задачи
uninstall_cron() {
    echo "🔧 Удаление автоматических бэкапов..."
    
    # Удаляем из crontab
    crontab -l 2>/dev/null | grep -v "$BACKUP_SCRIPT" | crontab - || true
    
    echo "✅ Автобэкапы удалены из cron!"
}

# Функция показа статуса
show_status() {
    echo "📊 Статус автоматических бэкапов"
    echo "================================"
    
    # Читаем настройки
    BACKUP_ENABLED=$(get_env_var "BACKUP_ENABLED" "$GLOBAL_ENV" "true")
    BACKUP_SCHEDULE=$(get_env_var "BACKUP_SCHEDULE" "$GLOBAL_ENV" "0 2 * * *")
    BACKUP_DIR=$(get_env_var "BACKUP_DIR" "$GLOBAL_ENV" "$PROJECT_ROOT/db_backups")
    RETENTION_DAYS=$(get_env_var "BACKUP_RETENTION_DAYS" "$GLOBAL_ENV" "14")
    LOG_FILE=$(get_env_var "BACKUP_LOG_FILE" "$GLOBAL_ENV" "$PROJECT_ROOT/ops/backup/backup.log")
    
    echo "🔧 Глобальные настройки:"
    echo "  Автобэкапы: $BACKUP_ENABLED"
    echo "  Расписание: $BACKUP_SCHEDULE"
    echo "  Хранение: $RETENTION_DAYS дней"
    echo "  Папка бэкапов: $BACKUP_DIR"
    echo "  Лог-файл: $LOG_FILE"
    echo ""
    
    # Проверяем cron
    if crontab -l 2>/dev/null | grep -q "$BACKUP_SCRIPT"; then
        echo "✅ Cron задача установлена"
        echo "📅 Текущее расписание в cron:"
        crontab -l | grep "$BACKUP_SCRIPT" | sed 's/^/  /'
    else
        echo "❌ Cron задача НЕ установлена"
    fi
    echo ""
    
    # Статистика бэкапов
    if [[ -d "$BACKUP_DIR" ]]; then
        echo "📈 Статистика бэкапов:"
        TOTAL_BACKUPS=$(find "$BACKUP_DIR" -name "*.sql*" 2>/dev/null | wc -l || echo "0")
        BACKUP_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1 || echo "0")
        echo "  Всего файлов: $TOTAL_BACKUPS"
        echo "  Размер папки: $BACKUP_SIZE"
        echo ""
        
        echo "📁 Последние бэкапы по сервисам:"
        for service_dir in "$BACKUP_DIR"/*; do
            if [[ -d "$service_dir" ]]; then
                service_name=$(basename "$service_dir")
                latest_backup=$(find "$service_dir" -name "*.sql*" 2>/dev/null | sort | tail -1 || echo "нет")
                if [[ "$latest_backup" != "нет" ]]; then
                    backup_time=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$latest_backup" 2>/dev/null || echo "неизвестно")
                    echo "  $service_name: $backup_time"
                else
                    echo "  $service_name: нет бэкапов"
                fi
            fi
        done
    else
        echo "📂 Папка бэкапов не существует: $BACKUP_DIR"
    fi
    echo ""
    
    # Персональные настройки сервисов
    echo "⚙️  Персональные настройки сервисов:"
    for ENV_FILE in "$PROJECT_ROOT"/.env.*; do
        [[ -f "$ENV_FILE" ]] || continue
        SVC_NAME="$(basename "$ENV_FILE" | sed 's/^\.env\.//')"
        
        # Проверяем персональные настройки бэкапа
        personal_settings=""
        if grep -q "BACKUP_" "$ENV_FILE" 2>/dev/null; then
            personal_settings=$(grep "BACKUP_" "$ENV_FILE" | grep -v "^#" | tr '\n' '; ')
        fi
        
        if [[ -n "$personal_settings" ]]; then
            echo "  $SVC_NAME: $personal_settings"
        else
            echo "  $SVC_NAME: использует глобальные настройки"
        fi
    done
}

# Функция тестового запуска
test_backup() {
    echo "🧪 Тестовый запуск бэкапа..."
    echo "Запускаем: bash $BACKUP_SCRIPT"
    echo ""
    
    bash "$BACKUP_SCRIPT"
}

# Функция показа логов
show_logs() {
    local lines="${1:-50}"
    LOG_FILE=$(get_env_var "BACKUP_LOG_FILE" "$GLOBAL_ENV" "$PROJECT_ROOT/ops/backup/backup.log")
    
    echo "📝 Последние $lines строк лога:"
    echo "==============================================="
    
    if [[ -f "$LOG_FILE" ]]; then
        tail -n "$lines" "$LOG_FILE"
    else
        echo "Лог-файл не найден: $LOG_FILE"
    fi
}

# Справка
show_help() {
    echo "🛠️  Управление автоматическими бэкапами PostgreSQL"
    echo ""
    echo "Использование: $0 <команда> [параметры]"
    echo ""
    echo "Команды:"
    echo "  install     - Установить автобэкапы в cron"
    echo "  uninstall   - Удалить автобэкапы из cron"
    echo "  status      - Показать статус и настройки"
    echo "  test        - Запустить тестовый бэкап"
    echo "  logs [N]    - Показать последние N строк лога (по умолчанию 50)"
    echo "  help        - Показать эту справку"
    echo ""
    echo "Примеры:"
    echo "  $0 install          # Установить автобэкапы"
    echo "  $0 status           # Посмотреть статус"
    echo "  $0 test             # Тестовый запуск"
    echo "  $0 logs 100         # Последние 100 строк лога"
    echo ""
    echo "Настройки находятся в файлах:"
    echo "  Глобальные: $GLOBAL_ENV"
    echo "  Персональные: .env.<service>"
}

# Основная логика
case "${1:-help}" in
    "install")
        install_cron
        ;;
    "uninstall")
        uninstall_cron
        ;;
    "status")
        show_status
        ;;
    "test")
        test_backup
        ;;
    "logs")
        show_logs "${2:-50}"
        ;;
    "help"|*)
        show_help
        ;;
esac