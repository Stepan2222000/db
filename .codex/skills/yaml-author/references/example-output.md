purpose: |
  Персональная система трекинга "что я делал" — активности и события.
  Один пользователь, один Telegram-чат с ботом, один контекст трекинга.
  Один агент, одна сессия — cron работает в той же сессии.
  Переживает перезапуски и обрывы сессий.
  Семантику решает LLM, механику — плагин.

definitions:
  - id: tracker_status
    body_markdown: |
      Читает [[status.md]], возвращает структурированный объект:
      `mode`, `active[]`, `ping_pending`, `updated_at`, `last_ping_at`, `sleep_started_at`.

      При отсутствии файла — дефолты: `mode: normal`, `active: []`, `ping_pending: false`.

      [Документация: Agent Tools](https://docs.openclaw.ai/plugins/agent-tools)

  - id: tracker_update
    body_markdown: |
      Обновляет [[status.md]]. Принимает partial update — read-modify-write.
      Автоматически проставляет `updated_at` при каждом вызове.

      Примеры:
      - Новая активность: `{ active: [{ name: "...", started_at: "now" }] }`
      - Режим сна: `{ mode: "sleeping", active: [], sleep_started_at: "now" }`
      - Сброс пинга: `{ ping_pending: false }`

      Создаёт файл при первой записи, если не существует.

  - id: tracker_log
    body_markdown: |
      Дописывает строку в [[дневной файл]].
      Автоформатирование: время по таймзоне из [[конфиг плагина]].
      Создаёт файл и секцию `## Трекинг` если не существуют.

      Принимает опциональный `date` (YYYY-MM-DD) для ретроспективных записей —
      автоматически добавляет пометку `[retrospective]`.

  - id: status.md
    body_markdown: |
      Snapshot трекера: `memory/status.md`. Формат: YAML frontmatter.

      Поля:
      - `updated_at` — последнее содержательное изменение (ISO 8601)
      - `active[]` — текущие активности, каждая с `name` и `started_at`
      - `mode` — `normal` / `sleeping`
      - `ping_pending` — ждём ли ответа на пинг
      - `last_ping_at` — когда последний пинг
      - `sleep_started_at` — когда лёг спать

      Холодный старт: [[tracker_status]] возвращает дефолты,
      [[tracker_update]] создаёт файл при первой записи.

  - id: дневной файл
    body_markdown: |
      Хронология дня: `memory/YYYY-MM-DD.md`. Записи остаются навсегда.

      Формат: секция `## Трекинг` с записями `- HH:MM — Действие`.
      Пишется через [[tracker_log]], время форматируется по таймзоне.

      Примеры записей:
      - `08:15 — Проснулся (сон: вчера 23:30 — 08:15, ~8.75ч)`
      - `09:00 — Начал: кодинг парсера`
      - `12:00 — Пинг отправлен`
      - `14:00 — Закончил кодинг (5ч), начал: обед`
      - `14:00 — [retrospective] Обедал`

      Стык суток: запись в файл того дня, когда событие произошло.

  - id: правила трекинга
    body_markdown: |
      Что считается активностью:
      - Работа над задачей
      - Переключение между задачами
      - Начало или конец чего-то содержательного

      Что НЕ активность:
      - Приветствия, шутки, мемы
      - Абстрактные вопросы, любопытство

      В спорных случаях — спросить пользователя.
      Добавление — без бюрократии, когда из контекста понятно.
      Закрытие — только при ясном контексте, не механически по сдвигу темы.

  - id: ретроспективная запись
    body_markdown: |
      Запись события задним числом — когда пользователь сообщает
      о прошлом ("вчера делал X", "встал в 7 утра").

      [[tracker_log]] принимает параметр `date` (YYYY-MM-DD)
      и записывает в файл указанного дня.
      К тексту автоматически добавляется пометка `[retrospective]`,
      чтобы отличать от записей в реальном времени.

  - id: AGENTS.md блок
    body_markdown: |
      Статичные инструкции трекера в `AGENTS.md` между маркерами
      `<!-- activity-tracker-start/end -->`. Агент видит их в каждом
      сообщении — часть system prompt.

      [[openclaw tracker setup]] записывает, [[openclaw tracker remove]] удаляет.
      Значения `{pingIntervalMinutes}`, `{quietHours}`, `{ignoreTimeoutHours}`
      подставляются из [[конфиг плагина]] при setup.

      Шаблон:

      ```
      ## Activity Tracker

      Ты — трекер активностей. Правила работы:

      **Tools:** tracker_status, tracker_update, tracker_log.
      Используй их для чтения/записи состояния.
      Не работай с файлами напрямую.

      **Трекинг:** При каждом сообщении оценивай, изменилась
      ли активность. Смотри на смысл разговора, не на ключевые
      слова. Если активность изменилась — tracker_update +
      tracker_log. Не будь навязчив, не спрашивай если очевидно.
      Если контекст неоднозначный — лучше уточни. Приветствия,
      шутки, мемы — не активность.

      **Ретроспективные записи:** Если пользователь сообщает
      о прошлом ("вчера делал X", "встал в 7") — используй
      параметр date в tracker_log для записи в нужный день.

      **Пинги (cron):** Периодически приходит cron-сообщение
      "Проверь статус трекера". Вызови tracker_status, посмотри
      updated_at. Если прошло больше {pingIntervalMinutes} минут
      и не тихие часы и не sleeping и нет ping_pending — спроси
      чем занимается. Если статус свежий или недавно общался —
      ничего не делай. Пинг — короткий текст, по-дружески.

      **Сон:** "Ложусь спать" → tracker_update(mode: sleeping,
      active: [], sleep_started_at: "now") + tracker_log("Лёг
      спать"). Cron не пингует при mode: sleeping. Любое
      сообщение после → проснулся: tracker_update(mode: normal)
      + tracker_log с длительностью сна. Если передумал спать —
      просто убери mode: sleeping без записи о пробуждении.

      **Тихие часы:** {quietHours}. Не пингуй в это время.

      **Правило игнора:** Если ping_pending и прошло >
      {ignoreTimeoutHours}ч эффективного времени (вычитая тихие
      часы) с момента last_ping_at — зафиксируй игнор через
      tracker_log, сбрось ping_pending. Не придумывай
      активность — честная пустота лучше ложных фактов.
      ```

      [Документация: System Prompt](https://docs.openclaw.ai/concepts/system-prompt)

  - id: before_prompt_build
    body_markdown: |
      Хук, вызывается перед каждым ответом агента. Инжектит
      актуальный статус из [[status.md]] — но не каждый раз,
      а раз в N сообщений (`remindEveryMessages` из [[конфиг плагина]]).

      Логика:
      - `messages.length % N === 0` или первое после рестарта → инжект
      - Иначе — ничего, агент работает по памяти

      Экономия токенов возможна благодаря [[lossless-claw]]:
      агент надёжно помнит контекст, хук лишь обновляет актуальное состояние.

      Возвращает `{ appendSystemContext: "..." }`. Шаблон инжекта:

      ```
      <activity-tracker-status>
      Текущее состояние трекера:
      - mode: {mode}
      - active:
        - {activity.name} (с {activity.started_at})
      - updated_at: {updated_at} ({relative_time} назад)
      - ping_pending: {ping_pending}
      - last_ping_at: {last_ping_at}

      Работай по инструкциям трекера из AGENTS.md.
      </activity-tracker-status>
      ```

      Значения подставляются из [[status.md]] в момент вызова хука.

      [Документация: Plugin Hooks](https://docs.openclaw.ai/tools/plugin)

  - id: конфиг плагина
    body_markdown: |
      Блок в `openclaw.json` → `plugins.entries.openclaw-activity-tracker.config`.

      Поля:
      - `chatId` (обязательно) — ID direct chat в Telegram
      - `timezone` (`Europe/Samara`) — таймзона записей
      - `quietHours` (`23:00-08:00`) — [[тихие часы]]
      - `pingIntervalMinutes` (`30`) — интервал пингов
      - `ignoreTimeoutHours` (`3`) — порог фиксации игнора
      - `remindEveryMessages` (`5`) — периодичность [[before_prompt_build]]

      Установка: [[openclaw tracker setup]].
      Удаление: [[openclaw tracker remove]].
      Диагностика: [[openclaw tracker status]].

      [Документация: Configuration](https://docs.openclaw.ai/gateway/configuration-reference)

  - id: тихие часы
    body_markdown: |
      Часы, когда cron не пингует (из [[конфиг плагина]]: `quietHours`).

      Два эффекта:
      1. Блокируют отправку пингов — агент не беспокоит ночью
      2. Замораживают таймер игнора — ночные часы не считаются

      Пример заморозки:
      - Пинг в 22:30, тихие часы 23:00-08:00 (9ч)
      - 08:30 — прошло 10ч, но эффективно только 1ч
      - 10:30 — эффективно 3ч → фиксируем игнор

      Арифметику агент считает сам по правилам из [[AGENTS.md блок]].

  - id: сознательные упрощения
    body_markdown: |
      Что система сознательно НЕ делает:

      - Не хранит raw-слой (голосовые, фото) — OpenClaw обрабатывает сам, в память попадает только текстовый факт
      - Не вводит систему entities (карточки людей/проектов) — добавим позже, если заболит
      - Не делает пользовательских команд (/mode, /status) — всё через естественный язык
      - Не закладывает обработку ошибок на уровне дизайна — edge cases решаются по факту
      - Не делает бэкапы — не нужны сейчас

  - id: lossless-claw
    body_markdown: |
      Плагин `@martian-engineering/lossless-claw` — DAG-based conversation
      summarization. Заменяет стандартный compaction OpenClaw.

      Что это даёт трекеру:
      - Агент надёжно помнит контекст через историю сессии
      - [[before_prompt_build]] может инжектить статус периодически, а не каждый раз
      - [[дневной файл]] — другой формат данных, не дублирование истории чата

  - id: openclaw tracker setup
    body_markdown: |
      Устанавливает трекер за одну команду:
      - Записывает конфиг в `openclaw.json`
        (поля и дефолты — в [[конфиг плагина]])
      - Добавляет инструкции в [[AGENTS.md блок]]
      - Создаёт cron-задачу через `openclaw cron add`
        с sessionTarget в основной чат
      - Создаёт начальный [[status.md]]

      [Документация: Plugin Manifest](https://docs.openclaw.ai/plugins/manifest)
      [Документация: CLI Plugins](https://docs.openclaw.ai/cli/plugins)
      [Документация: Agent Workspace](https://docs.openclaw.ai/concepts/agent-workspace)

  - id: openclaw tracker remove
    body_markdown: |
      Разбирает то, что поставил setup:
      - Удаляет cron-задачу через `openclaw cron rm`
      - Удаляет блок трекера из AGENTS.md (между маркерами)
      - Отключает плагин

      Данные ([[status.md]], [[дневной файл]]) не удаляются —
      сознательное решение, чтобы не потерять историю.

  - id: openclaw tracker status
    body_markdown: |
      Диагностика: показывает текущее состояние трекера.
      - Конфиг плагина из `openclaw.json`
      - Содержимое [[status.md]] (mode, активности, ping_pending)
      - Статус cron-задачи (запущена или нет)

      Полезно для отладки: если пинги не приходят,
      проверяешь cron, mode, ping_pending.

modules:
  - id: activity-tracking
    purpose: Распознавать и записывать смены активности из естественного разговора.
    steps:
      - id: detect-change
        body_markdown: |
          Агент оценивает каждое сообщение по смыслу,
          не по ключевым словам.
          Решает по [[правила трекинга]].
        links:
          - to: update-and-log
            condition: активность изменилась
          - to: sleep-enter
            condition: ложится спать
          - to: acknowledge-ping
            condition: ответ на пинг, без смены

      - id: acknowledge-ping
        body_markdown: |
          Пользователь ответил на пинг, но активность не изменилась.
          - Сбросить ожидание ответа через [[tracker_update]]
          - Записать ответ через [[tracker_log]]
          - Кратко подтвердить
        links:
          - to: detect-change
            condition: ждём следующее сообщение

      - id: update-and-log
        body_markdown: |
          - Обновить активность через [[tracker_update]]
          - Записать событие через [[tracker_log]]
          - Если событие было в прошлом — [[ретроспективная запись]]
          - Ожидание ответа на пинг сбрасывается автоматически
          - Кратко подтвердить пользователю
        links:
          - to: detect-change
            condition: ждём следующее сообщение

      - id: sleep-enter
        body_markdown: |
          - Закрыть все активности через [[tracker_update]]
          - Включить режим сна
          - Записать через [[tracker_log]]
          - Cron перестаёт пинговать
          - Если агент уже спрашивал и ждёт ответа —
            ожидание снимается (уход спать — не игнор)
        links:
          - to: sleep-wake
            condition: следующее сообщение

      - id: sleep-wake
        body_markdown: |
          Первое сообщение после сна. Два варианта:

          - Проснулся — выключить режим через [[tracker_update]],
            записать длительность сна через [[tracker_log]]
          - Передумал — убрать режим без записи
        links:
          - to: detect-change
            condition: проснулся или передумал

  - id: cron-pinging
    purpose: Периодически проверять и пинговать при молчании.
    steps:
      - id: cron-trigger
        body_markdown: |
          Cron запускается каждые N минут в существующей сессии
          основного чата. Агент получает сообщение
          "Проверь статус трекера".

          Агент видит:
          - историю разговора
          - правила из [[AGENTS.md блок]]
          - актуальный статус из [[before_prompt_build]]
        links:
          - to: check-context
            condition: оценка ситуации

      - id: check-context
        body_markdown: |
          Агент оценивает по контексту разговора, нужна ли
          проверка статуса. Если недавно общался или
          знает текущее состояние — может решить сразу.
        links:
          - to: skip-ping
            condition: из контекста ясно, пинг не нужен
          - to: check-status
            condition: нужно проверить статус

      - id: check-status
        body_markdown: |
          Вызвать [[tracker_status]] и проверить условия:
          - Режим сна → не пинговать
          - [[тихие часы]] → не пинговать
          - Статус свежий → не пинговать
          - Пинг ждёт ответа → проверить таймер игнора
          - Иначе → пинговать
        links:
          - to: send-ping
            condition: пинг нужен
          - to: skip-ping
            condition: пинг не нужен
          - to: ignore-timeout
            condition: пинг ждёт ответа

      - id: send-ping
        body_markdown: |
          Текстовый пинг в чат. Лаконично, по-дружески.

          После отправки:
          - Обновить статус через [[tracker_update]]
          - Записать через [[tracker_log]]
        links:
          - to: detect-change
            condition: пользователь ответил
          - to: ignore-timeout
            condition: нет ответа, сработал таймер

      - id: skip-ping
        body_markdown: |
          Пинг не нужен. Следующая проверка — по расписанию cron.

      - id: ignore-timeout
        body_markdown: |
          Прошло достаточно эффективных часов с момента пинга —
          фиксировать игнор:
          - Записать через [[tracker_log]]
          - Сбросить ожидание через [[tracker_update]]

          [[тихие часы]] замораживают таймер.
          Активность не придумываем —
          честная пустота лучше ложных фактов.
        links:
          - to: cron-trigger
            condition: следующий cron по расписанию
