# dAIry deploy update

Краткая инструкция для обновления production-деплоя на сервере без потери базы.

## Что важно сохранить

- Основная SQLite-база enrichment: `data/enrichment.sqlite3`.
- Production-настройки: `.env`.
- Docker volume `layer3-analysis-cache` хранит cache веб-аналитики. Его можно пересоздать, но при обычном обновлении удалять не нужно.

Не запускайте при обычном обновлении:

```bash
docker compose down -v
docker volume prune
```

Эти команды могут удалить Docker volumes. Для обновления достаточно `docker compose up -d --build`.

## 1. Перейти в проект

```bash
cd /home/artem/dAIry
```

## 2. Подтянуть код

Если изменения уже подтянуты с GitHub, этот шаг можно пропустить.

```bash
git status --short
git pull --ff-only
git log --oneline -5
```

Если `git status --short` показывает локальные изменения, сначала разберитесь с ними и не перезаписывайте их вслепую.

## 3. Сделать бэкап базы

`dairy-bot` пишет в SQLite, поэтому перед копированием базы его лучше остановить. Backend и frontend можно не останавливать.

```bash
TS=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/home/artem/backups/dairy-deploy-$TS"

mkdir -p "$BACKUP_DIR"
docker compose stop dairy-bot
cp -a data "$BACKUP_DIR/data"
cp -a .env "$BACKUP_DIR/.env"

find "$BACKUP_DIR" -maxdepth 2 -type f
```

В выводе должен быть файл:

```text
.../data/enrichment.sqlite3
```

## 4. Пересобрать и поднять новую версию

```bash
docker compose up -d --build --remove-orphans
```

Эта команда пересобирает образы и пересоздаёт контейнеры, но не удаляет `./data` и Docker volumes.

## 5. Проверить сервисы

```bash
docker compose ps
docker compose logs --tail=80 dairy-bot layer3-backend layer3-frontend
```

Ожидаемо:

- `dairy-bot`, `layer3-backend`, `layer3-frontend` в статусе `Up`;
- backend пишет `Application startup complete`;
- bot пишет `Start polling`;
- в логах нет traceback или restart loop.

## 6. Проверить HTTP

Локально на сервере:

```bash
curl -I http://127.0.0.1:18080/
curl -i http://127.0.0.1:18080/api/auth/me
```

Ожидаемо:

- `/` возвращает `200 OK`;
- `/api/auth/me` без cookie возвращает `401 Unauthorized` и JSON `{"detail":"Authentication required"}`.

Если Caddy настроен как сейчас, внешний URL:

```bash
curl -I https://diary.ndaysbefore.com/
curl -i https://diary.ndaysbefore.com/api/auth/me
```

Ожидания такие же: `200 OK` для страницы и `401 Unauthorized` для API без сессии.

## Быстрый откат базы

Используйте только если после обновления стало понятно, что база повреждена или её нужно вернуть к состоянию до деплоя.

```bash
cd /home/artem/dAIry
docker compose stop dairy-bot layer3-backend

mv data "data.broken.$(date +%Y%m%d-%H%M%S)"
cp -a /home/artem/backups/dairy-deploy-YYYYMMDD-HHMMSS/data ./data

docker compose up -d --build
docker compose ps
```

Замените `YYYYMMDD-HHMMSS` на timestamp нужного бэкапа.

## Минимальный чек-лист

1. `cd /home/artem/dAIry`
2. `git pull --ff-only`
3. `TS=$(date +%Y%m%d-%H%M%S); BACKUP_DIR="/home/artem/backups/dairy-deploy-$TS"; mkdir -p "$BACKUP_DIR"`
4. `docker compose stop dairy-bot`
5. `cp -a data "$BACKUP_DIR/data" && cp -a .env "$BACKUP_DIR/.env"`
6. `docker compose up -d --build --remove-orphans`
7. `docker compose ps`
8. `curl -I https://diary.ndaysbefore.com/`
