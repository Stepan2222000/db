# Implementation Notes

## Purpose

Этот файл не заменяет `docs/SPEC.flow.yaml`.

Он нужен как промежуточный список важных моментов, которые уже были проговорены в чате, но пока:
- отсутствуют в актуальном YAML
- описаны там недостаточно точно
- требуют явной фиксации до следующего редактирования `SPEC.flow.yaml`

Если в чате согласован новый важный момент, который влияет на реализацию, но его ещё нет в YAML, сначала он фиксируется здесь, а потом переносится в `docs/SPEC.flow.yaml`.

Этот файл хранит не только missing rules и runtime nuances, но и:
- ответы из чата на содержательные вопросы
- решения, которые появились из-за лакун в текущем YAML
- временный контекст до следующей синхронизации `SPEC.flow.yaml`

## Docs To Study Before Implementation

Ниже перечислены внешние документы, которые нужно читать перед реализацией соответствующих подсистем.

### Always Re-read For This Project

- Docker Compose env files:
  - https://docs.docker.com/compose/env-file/
- Docker Compose services:
  - https://docs.docker.com/reference/compose-file/services/
  - https://docs.docker.com/reference/compose-file/services/#env_file
  - https://docs.docker.com/reference/compose-file/services/#shm_size
  - https://docs.docker.com/reference/compose-file/services/#healthcheck
- Docker Compose deploy resources:
  - https://docs.docker.com/reference/compose-file/deploy/#resources
- Docker Compose CLI:
  - https://docs.docker.com/reference/cli/docker/compose/
  - https://docs.docker.com/reference/cli/docker/compose/up/
- Docker exec:
  - https://docs.docker.com/reference/cli/docker/container/exec/
- Official PostgreSQL Docker image:
  - https://hub.docker.com/_/postgres
  - https://github.com/docker-library/docs/blob/master/postgres/content.md
  - https://github.com/docker-library/postgres/blob/master/docker-entrypoint.sh

### PostgreSQL Runtime / Query / Auth

- Runtime connection settings:
  - https://www.postgresql.org/docs/current/runtime-config-connection.html
- `pg_isready`:
  - https://www.postgresql.org/docs/current/app-pg-isready.html
- `psql`:
  - https://www.postgresql.org/docs/current/app-psql.html
- `ALTER ROLE`:
  - https://www.postgresql.org/docs/current/sql-alterrole.html
- `pg_hba.conf`:
  - https://www.postgresql.org/docs/current/auth-pg-hba-conf.html
- Admin functions (`pg_database_size()` и related):
  - https://www.postgresql.org/docs/current/functions-admin.html

### Backup / Restore / Remote Stages

- `pg_dump`:
  - https://www.postgresql.org/docs/current/app-pgdump.html
- Paramiko SSH client:
  - https://docs.paramiko.org/en/stable/api/client.html
- Paramiko SFTP:
  - https://docs.paramiko.org/en/stable/api/sftp.html

### Metrics / Automation Stages

- `docker stats`:
  - https://docs.docker.com/reference/cli/docker/container/stats/
- Python `sqlite3`:
  - https://docs.python.org/3/library/sqlite3.html
- `python-crontab`:
  - https://pypi.org/project/python-crontab/
- `/proc/meminfo`:
  - https://man7.org/linux/man-pages/man5/proc_meminfo.5.html

## Chat-derived Clarifications Missing from YAML

- `docs/SPEC.flow.yaml` — главный источник правды для реализации.
- Отклонение от `docs/SPEC.flow.yaml` допустимо только в rollback-теме.
- Если в чате был дан ответ на вопрос, который меняет трактовку flow, последовательность шагов, поведение команды или acceptance criteria, этот ответ нужно зафиксировать здесь до тех пор, пока он не будет синхронизирован в YAML.
- Если реализация упирается в поведение Docker / PostgreSQL / Paramiko / Compose, нельзя опираться только на память: сначала перечитываются соответствующие docs, перечисленные выше.

## Resolved Chat Decisions Not Yet Reflected in YAML

### Spec Priority

- Для реализации главным источником правды считается `docs/SPEC.flow.yaml`.
- Отклонение от него допустимо только в rollback-теме.

### Chat Answers As Missing Spec Context

- Если по ходу реализации возник содержательный вопрос, а ответ на него был дан в чате, этот ответ считается недостающим spec-context до тех пор, пока не будет перенесён в YAML.
- Такие ответы нельзя держать только в памяти; их нужно фиксировать здесь отдельными решениями.

### Runtime-first Validation

- Если логика зависит от реального поведения Docker / PostgreSQL / Compose / Paramiko, сначала нужно по возможности проверить механику в терминале на живом runtime, а потом писать код.
- Цель этой проверки — не писать реализацию по ложному контексту.

### Post-implementation Validation

- После реализации шага нельзя ограничиваться только unit-тестами.
- Нужно прогонять рабочие сценарии и edge cases в терминале, чтобы подтвердить, что логика действительно работает в реальном окружении.

### YAML Synchronization Boundary

- Если по ходу реализации был согласован новый важный момент, но YAML ещё не обновлён, этот момент сначала хранится здесь.
- После завершения шага можно остановиться и не править YAML сразу, если отдельно согласовано, что синхронизация YAML будет позже.

## Runtime Nuances Already Verified

- Для SQL через stdin внутри контейнера нужен `docker exec -i`; без `-i` stdin до `psql` не доходит.
- В official `postgres` image loopback-подключения внутри контейнера (`127.0.0.1`, `::1`) могут проходить через `trust`, поэтому через них нельзя надёжно проверять смену пароля.
- `docker compose up -d` действительно пересоздаёт контейнеры при изменении service configuration, включая `env_file` и `command`.
- Изменение `POSTGRES_PASSWORD` в `services/.env.<service>` само по себе не меняет пароль в уже инициализированной базе; для синхронизации нужен отдельный SQL-шаг.
