# Implementation Notes

## Purpose

Этот файл не заменяет `docs/SPEC.flow.yaml`.

Он нужен как промежуточный список важных моментов, которые уже были проговорены в чате, но пока:
- отсутствуют в актуальном YAML
- описаны там недостаточно точно
- требуют явной фиксации до следующего редактирования `SPEC.flow.yaml`

Если в чате согласован новый важный момент, который влияет на реализацию, но его ещё нет в YAML, сначала он фиксируется здесь, а потом переносится в `docs/SPEC.flow.yaml`.

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

## Runtime Nuances Already Verified

- Для SQL через stdin внутри контейнера нужен `docker exec -i`; без `-i` stdin до `psql` не доходит.
- В official `postgres` image loopback-подключения внутри контейнера (`127.0.0.1`, `::1`) могут проходить через `trust`, поэтому через них нельзя надёжно проверять смену пароля.
- `docker compose up -d` действительно пересоздаёт контейнеры при изменении service configuration, включая `env_file` и `command`.
- Изменение `POSTGRES_PASSWORD` в `services/.env.<service>` само по себе не меняет пароль в уже инициализированной базе; для синхронизации нужен отдельный SQL-шаг.
