# dAIry

`dAIry` — приватный Telegram-бот для быстрого захвата дневниковых заметок в Obsidian/vault Git-репозиторий.

Текущая версия — это **capture-first** слой. Бот не ведёт опросы, не задаёт Deep Questions и не пишет метрики в Google Sheets. Он принимает текст или голос, сохраняет запись в Markdown, может обогатить заметку LLM-тегами, обновляет TOC и синхронизирует vault через Git.

По умолчанию каждая запись попадает в заметку сегодняшнего дня. Если нужно дописать прошлый день постфактум, можно один раз выбрать целевую дату командой `/yesterday` или `/day dd-mm-yyyy`. Такой выбор действует только на следующее текстовое сообщение или подтверждённую голосовую заметку, после чего бот снова пишет в сегодняшний день.

## Возможности

- Текстовые сообщения сразу сохраняются в дневную Markdown-заметку.
- Голосовые сообщения конвертируются через `ffmpeg`, транскрибируются через OpenRouter-модель `VOICE_MODEL_NAME` и показываются на предпросмотре перед сохранением.
- `/yesterday` сохраняет следующую запись во вчерашний день.
- `/day dd-mm-yyyy` сохраняет следующую запись в указанную дневную заметку.
- `/back` отменяет выбранную `/yesterday` или `/day` дату до того, как она будет использована.
- Файл дня создаётся только при первой реальной записи. Если записей не было, файла за этот день нет.
- В дневной заметке сохраняются ссылки на ближайший предыдущий и следующий существующий день с записями.
- Git sync включается через `GIT_ENABLED=true`: бот подтягивает remote перед записью, затем коммитит и пушит изменения.
- TOC слой включается через `TOC_ENABLED=true`: бот поддерживает `table_of_contents.md` и `.toc_index.json`, включая изменения, добавленные в прошлые дни.
- Enrichment слой включается через `ENRICHMENT_ENABLED=true`: новые записи через бота, включая post-factum записи через `/day` и `/yesterday`, получают note-level mood/topics сразу после сохранения. Ручные правки старых дней обрабатываются тихим watchdog-проходом вместе с TOC.
- Reviews включаются через `REVIEWS_ENABLED=true`: бот создаёт компактные недельные и месячные обзоры, присылает короткую версию в Telegram, а полный текст публикует в web-интерфейсе.
- Доступ ограничен одним Telegram-пользователем через `ALLOWED_USER_ID`.

## Формат заметок

Новая дневная заметка создаётся в `JOURNAL_DIR/YYYY/MM/YYYY-MM-DD.md`.

```markdown
---
date: 2026-06-16
type: daily
---
# 2026-06-16
[[2026-06-15|Prev day]] · [[2026-06-18|Next day]]

## 09:42

Текст записи
```

Когда включён note-level enrichment, у новых записей через бота под текстом появляется компактная Dataview-строка:

```markdown
## 14:32 — text

Сегодня было ужасное занятие по немецкому, преподавательница опять меня перебивала.
mood:: anger · topics:: learning, identity
```

Для ручных правок старых дней эта строка добавляется тихим watchdog-проходом раз в `TOC_SCAN_INTERVAL_MINUTES`.

Если предыдущего или следующего существующего дня нет, соответствующая ссылка пропускается. Старые заметки не мигрируются массово: бот просто перестаёт создавать старые поля метрик и служебные блоки.

Если запись добавлена в тот же календарный день, что и сама заметка, заголовок блока содержит только время:

```markdown
## 21:55
```

Если запись добавлена постфактум в другой день, заголовок показывает дату фактического добавления и время:

```markdown
## June 16 21:55
```

## Команды

| Команда | Что делает |
| --- | --- |
| `/start` | Выбрать язык ответов бота |
| `/today` | Показать сегодняшние записи, если файл уже существует |
| `/yesterday` | Сохранить следующее текстовое сообщение или подтверждённую голосовую заметку во вчерашний день |
| `/day dd-mm-yyyy` | Сохранить следующую запись в указанную дневную заметку, например `/day 13-06-2026` |
| `/back` | Отменить выбранную дату из `/yesterday` или `/day` |
| `/enrich` | Тихо пересчитать day-level enrichment для сегодняшней заметки |

Любое обычное текстовое сообщение считается новой записью. Голосовое сообщение сначала проходит транскрибацию и подтверждение.

Если `ENRICHMENT_ENABLED=true`, бот отвечает одним статусным сообщением и редактирует его по мере прогресса:

```text
✅ Note written to file
✅ LLM processed note. Mood: calm (0.77), topics: reflection, productivity
✅ Synced with git
```

Статусное сообщение относится только к note-level enrichment. Day-level enrichment работает тихо, как TOC: по команде `/enrich` или в watchdog-проходе.

`/yesterday` и `/day` не переключают бота навсегда. Они ставят одноразовую цель: следующая запись попадёт в выбранную дневную заметку, а запись после неё снова попадёт в сегодняшний день. Если целевой заметки ещё нет, бот создаст её с обычным frontmatter, заголовком дня и навигацией.

В сообщениях бота даты показываются в формате `dd-mm-yyyy`. Формат `yyyy-mm-dd` используется только внутри vault: в именах файлов, frontmatter, заголовках дневных заметок и Obsidian-ссылках.

## Конфигурация

Пример `.env`:

```bash
# Telegram
BOT_TOKEN=123456:telegram-bot-token
ALLOWED_USER_ID=123456789

# OpenRouter
OPENROUTER_API_KEY=sk-or-xxx
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
VOICE_MODEL_NAME=mistralai/voxtral-small-24b-2507

# Vault paths
JOURNAL_DIR=/data
HOST_JOURNAL_DIR=/absolute/path/to/your/obsidian/git/repo

# Git over SSH
GIT_ENABLED=true
SSH_KEY_PATH=/absolute/path/to/private/ssh/key

# Runtime
TIMEZONE=Europe/Vienna
LANGUAGE=EN

# TOC
TOC_ENABLED=true
TOC_FILENAME=table_of_contents.md
TOC_MODEL_NAME=openai/gpt-4.1-mini
TOC_SCAN_INTERVAL_MINUTES=10
TOC_MAX_TAGS=5

# Enrichment
ENRICHMENT_ENABLED=true
ENRICHMENT_MODEL_NAME=openai/gpt-4.1-mini
EMBEDDING_MODEL_NAME=intfloat/multilingual-e5-large
ENRICHMENT_DB_PATH=data/enrichment.sqlite3
EMBEDDINGS_DB_PATH=data/embeddings.sqlite3

# Weekly/monthly reviews
REVIEWS_ENABLED=false
REVIEWS_DB_PATH=data/reviews.sqlite3
REVIEW_ASSETS_DIR=data/review_images
REVIEW_MODEL_NAME=openai/gpt-5.6-terra
REVIEW_IMAGE_MODEL_NAME=openai/gpt-image-2
REVIEW_IMAGE_FALLBACK_MODEL_NAME=recraft/recraft-v4.1-pro
REVIEW_MAX_SEARCH_CALLS=6
# Optional; omit to disable external web search.
# PARALLEL_API_KEY=your-parallel-api-key
REVIEW_WEEKLY_SEND_TIME=09:00
REVIEW_MONTHLY_SEND_TIME=10:00
WEB_PUBLIC_BASE_URL=https://journal.example.com

# Layer 3 web analytics
LAYER3_FRONTEND_BIND=127.0.0.1
LAYER3_FRONTEND_PORT=18080
WEB_USERNAME=artem
WEB_PASSWORD_ARGON2='$argon2id$v=19$m=65536,t=3,p=4$replace-with-generated-hash'
WEB_SESSION_SECRET=replace-with-long-random-secret
WEB_COOKIE_SECURE=true
WEB_LOGIN_RATE_LIMIT_ATTEMPTS=5
WEB_LOGIN_RATE_LIMIT_WINDOW_SECONDS=60

# Optional web-to-bot note editing. Empty keeps editing disabled.
# Generate with: openssl rand -hex 32
EDIT_API_TOKEN=
```

`JOURNAL_DIR` — путь внутри приложения. Для Docker оставляйте `/data`.

`HOST_JOURNAL_DIR` — абсолютный путь на хосте к Obsidian/vault Git-репозиторию. `docker-compose.yml` монтирует его в контейнер как `/data`.

`SSH_KEY_PATH` — абсолютный путь к **файлу** приватного ключа. Это не должна быть директория. Например:

```bash
SSH_KEY_PATH=/Users/artem/.ssh/id_ed25519
```

`LANGUAGE` задаёт язык генеративных LLM-полей: `EN` или `RU`. Настройка влияет на prose-поля вроде note-level `gist`/`mood_evidence`, day-level `summary`/evidence и TOC `summary`; enum-значения `mood`, `topics`, `key_topics` и TOC tags остаются английскими.

`TOC_MODEL_NAME` используется только для TOC enrichment: модель делает короткое summary и выбирает теги для `table_of_contents.md`.

`ENRICHMENT_MODEL_NAME` используется для note-level и day-level structured output. `EMBEDDING_MODEL_NAME` используется для единого semantic index всех diary entries; оставляйте здесь embedding-модель, например `intfloat/multilingual-e5-large` (1024-мерные embedding'и), а не chat-модель или экспериментальный slug. Metadata enrichment хранится в `data/enrichment.sqlite3`, общие vectors карты и reviews — в `data/embeddings.sqlite3`; локальные review DB и изображения также хранятся в `data/` и не коммитятся.

Если нужен временный capture-only режим без дополнительных LLM/embedding вызовов, поставьте:

```bash
ENRICHMENT_ENABLED=false
```

Если хотите проверить запись без Git remote, временно поставьте:

```bash
GIT_ENABLED=false
```

## Layer 3 web analytics

Layer 3 adds two containers:

- `layer3-backend` — FastAPI API, auth, read-only access to `data/enrichment.sqlite3`, `data/embeddings.sqlite3` and the vault, plus its own writable `layer3-analysis-cache` Docker volume.
- `layer3-frontend` — nginx serving the Vite SPA and proxying `/api/*` to the backend on the private Docker network.

The backend mounts bot outputs read-only, including the reviews DB and generated posters:

```yaml
./data:/bot-data:ro
${HOST_JOURNAL_DIR}:/vault:ro
```

It overrides paths inside the container:

```bash
ENRICHMENT_DB_PATH=/bot-data/enrichment.sqlite3
EMBEDDINGS_DB_PATH=/bot-data/embeddings.sqlite3
REVIEWS_DB_PATH=/bot-data/reviews.sqlite3
REVIEW_ASSETS_DIR=/bot-data/review_images
VAULT_DIR=/vault
ANALYSIS_CACHE_PATH=/app/cache/analysis_cache.sqlite3
```

Before deployment, set web credentials in `.env`. Generate an argon2 hash with:

```bash
uv run python -c "from argon2 import PasswordHasher; import getpass; print(PasswordHasher().hash(getpass.getpass('WEB_PASSWORD: ')))"
```

Put the hash in `.env` in single quotes, because argon2 hashes contain `$` characters:

```bash
WEB_PASSWORD_ARGON2='$argon2id$v=19$m=65536,t=3,p=4$...'
```

Generate a session secret with:

```bash
openssl rand -hex 32
```

The compose file binds the frontend to localhost by default:

```bash
LAYER3_FRONTEND_BIND=127.0.0.1
LAYER3_FRONTEND_PORT=18080
```

Point the existing HTTPS reverse proxy at `http://127.0.0.1:18080`. Keep `WEB_COOKIE_SECURE=true` in production so the auth cookie is `Secure` behind TLS. For local HTTP-only testing, temporarily set `WEB_COOKIE_SECURE=false`.

### Editing from the web

Web note edits are opt-in. Set the same `EDIT_API_TOKEN` for `dairy-bot` and `layer3-backend`; generate it with `openssl rand -hex 32`. If it is empty, the bot does not start the internal edit API and saves from the frontend return `EDITING DISABLED`.

The backend keeps the vault mount read-only and never edits diary files directly. It proxies `PUT /api/notes/{id}` to the bot over the private compose network at `http://dairy-bot:8081`; that port is not published on the host.

Edits use `raw_text_sha256` for optimistic conflict detection. A 409 reloads the fresh note/day and keeps the user's unsaved draft visible; successful edits are reprocessed later by the enrichment watchdog.

## Запуск через Docker

1. Создайте `.env` из `.env.example` или примера выше.
2. Убедитесь, что `HOST_JOURNAL_DIR` существует и является Git-репозиторием.
3. Убедитесь, что `SSH_KEY_PATH` указывает на реальный приватный ключ.
4. Запустите сервис:

```bash
docker compose down
docker compose up -d --build
docker compose logs -f dairy-bot
```

Для запуска Layer 3 вместе с ботом:

```bash
docker compose up -d --build dairy-bot layer3-backend layer3-frontend
docker compose logs -f layer3-backend layer3-frontend
```

Compose монтирует `SSH_KEY_PATH` в контейнер, копирует ключ во временную директорию, выставляет права `0600` и запускает бот через `/app/.venv/bin/python`. Это важно: системный `python` внутри контейнера не содержит зависимости проекта.

SQLite caches bot-owned слоёв монтируются из локальной папки проекта `./data` в контейнерный путь `/app/data`. При стандартных путях metadata будет видна как `data/enrichment.sqlite3`, а общий semantic index — как `data/embeddings.sqlite3`.

Layer 3 читает оба файла как `/bot-data/enrichment.sqlite3` и `/bot-data/embeddings.sqlite3` в режиме read-only и не пишет в bot DB. Проекция, кластеры и labels сохраняются только в Docker volume `layer3-analysis-cache`.

Для Git over SSH также нужен `~/.ssh/known_hosts`; он монтируется автоматически.

## Локальный запуск

Для локального запуска поставьте `JOURNAL_DIR` сразу на путь к vault на вашей машине, а не `/data`.

```bash
uv sync
uv run python src/bot.py
```

## TOC слой

Если `TOC_ENABLED=true`, бот поддерживает:

- `table_of_contents.md` — читаемое оглавление vault;
- `.toc_index.json` — технический cache состояния индекса.

TOC обновляется:

- сразу после сохранения новой записи;
- сразу после постфактум-добавления записи в выбранный прошлый день;
- периодически раз в `TOC_SCAN_INTERVAL_MINUTES`, чтобы поймать ручные правки старых заметок.

Для каждой содержательной заметки модель возвращает строгий JSON:

```json
{
  "summary": "A concise English summary.",
  "tags": ["reflection", "work"]
}
```

Теги выбираются из фиксированной таксономии в `src/dairy_bot/services/toc_service.py`.

## Enrichment слой

Enrichment состоит из двух уровней.

**Note-level** запускается сразу для text/voice записей через бота, включая post-factum сохранение в выбранный прошлый день. Модель возвращает `gist`, `mood_evidence`, `mood`, `mood_confidence` и `topics`; в markdown записываются только `mood` и `topics`, а evidence и gist уходят в enrichment SQLite cache. Passage embeddings независимо индексируются в общей semantic DB и не дублируются в enrichment.

**Day-level** пересчитывает весь день целиком и обновляет YAML frontmatter: `mood`, `mood_confidence`, `key_topics`, sparse facts (`sport`, `reading`, `purchases`, `eating_outside`, `deep_focus`, `sleep_quality`), `weekday`, `is_weekend`, `season` и `summary`. Evidence для sparse facts хранится в SQLite cache.

Day-level запускается тихо:

- по команде `/enrich` для сегодняшнего дня;
- watchdog-проходом раз в `TOC_SCAN_INTERVAL_MINUTES`, если в не сегодняшнем daily note появились новые записи или изменились существующие.

Watchdog сначала выполняет enrichment, затем TOC, чтобы `table_of_contents.md` индексировал уже обогащённый markdown. Изменения отслеживаются по hash текста записей (а не всего файла), поэтому перезапись frontmatter или summary не считается изменением и повторное обогащение не запускается.

## Недельные и месячные reviews

При `REVIEWS_ENABLED=true` бот создаёт обзор закрывшейся недели по воскресеньям в `REVIEW_WEEKLY_SEND_TIME` и обзор закрывшегося месяца первого числа в `REVIEW_MONTHLY_SEND_TIME`. Оба времени интерпретируются в `TIMEZONE`; если первое число приходится на воскресенье, weekly и monthly jobs запускаются независимо в своё время. При первом запуске непустые прошлые периоды заполняются ретроспективно, но старые обзоры не рассылаются в Telegram.

Полная версия доступна в web-интерфейсе; Telegram получает короткий текст, один сгенерированный постер (если генерация изображения удалась) и кнопку на `WEB_PUBLIC_BASE_URL`. Укажите здесь внешний HTTPS-адрес, доступный с телефона, до включения scheduled-рассылки. Если обе image-модели недоступны, текстовый обзор остаётся готовым и показывается без постера.

`REVIEW_MODEL_NAME` отвечает за анализ (по умолчанию `openai/gpt-5.6-terra`),
`REVIEW_IMAGE_MODEL_NAME` и `REVIEW_IMAGE_FALLBACK_MODEL_NAME` — за постер. Planner
сначала ищет связи с прошлыми дневниковыми записями через `search_diary` и
сопоставляет их с предыдущими reviews. Другие Markdown-файлы vault не индексируются.
Внешний поиск включается только при заданном
`PARALLEL_API_KEY` и ограничен `REVIEW_MAX_SEARCH_CALLS` (максимум 6 на обзор).
Для Parallel модель формирует self-contained objective и 2–3 взаимодополняющих
англоязычных keyword-запроса; имена и идентифицирующие детали обобщает сама LLM.
Web-эссе получает мягкий ориентир 250–300 слов, но длина не валидируется и не
исправляется кодом или critic-проходом.

Смысл и форма LLM-ответов контролируются только prompts и отдельным LLM-critic/revise проходом. Код не применяет regex-блоклисты, masking, semantic post-validation, обрезание или переписывание текста и visual brief. Детерминированными остаются только технические границы: JSON Schema, временная изоляция исторических периодов, безопасность путей, HTML escaping и проверка формата ответа внешнего API.

То же правило действует на остальных LLM-boundaries: TOC и day enrichment получают полный raw Markdown, cluster labels и transcription возвращаются без whitespace/length cleanup, а malformed structured output не «лечится» локальными эвристиками. Markdown segmentation и managed markers остаются частью формата хранения, а не content-фильтрацией модели.

Бот единолично пишет `EMBEDDINGS_DB_PATH`, `REVIEWS_DB_PATH` и `REVIEW_ASSETS_DIR`. В Docker это `/app/data/embeddings.sqlite3`, `/app/data/reviews.sqlite3` и `/app/data/review_images`; backend видит те же данные как `/bot-data/...` через read-only mount. Пока первый полный semantic pass не опубликован атомарно, карта показывает статус построения и повторяет запрос раз в 10 секунд.

## Проверки

```bash
uv lock
uv run python -m compileall src main.py
uv run pytest
git diff --check
```

## Частые ошибки

### `ModuleNotFoundError: No module named 'aiogram'`

Пересоберите контейнер после обновления `docker-compose.yml`:

```bash
docker compose down
docker compose up -d --build
```

Актуальный compose запускает `/app/.venv/bin/python src/bot.py`, чтобы использовать зависимости, установленные через `uv sync`.

### `UNPROTECTED PRIVATE KEY FILE`

Проверьте, что `SSH_KEY_PATH` указывает на файл приватного ключа:

```bash
ls -la "$SSH_KEY_PATH"
```

Это должен быть файл вроде `id_ed25519`, а не директория. Контейнер сам копирует ключ и ставит права `0600`.

### Бот видит неожиданные файлы в `/data`

Проверьте итоговую конфигурацию compose:

```bash
docker compose config
```

В секции `volumes` должно быть видно, что `HOST_JOURNAL_DIR` смонтирован в `/data`.

## Что удалено

Из текущей версии убраны:

- утренние и вечерние опросы;
- Deep Questions;
- Google Sheets export;
- погодный слой;
- `APScheduler` и отдельный scheduler-сервис.

Если эти идеи снова понадобятся, их лучше вернуть как отдельные явно включаемые модули, а не как часть базового capture layer.
