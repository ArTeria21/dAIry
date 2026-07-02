# worklog.md — коллективная память агентов

> Каждый агент **читает этот файл первым** и **дописывает запись в конце спринта** (или при
> прерывании работы). Цель — чтобы следующие агенты не повторяли ошибок и не переоткрывали
> устройство проекта. Пиши плотно и по делу: не «что я делал», а «что важно знать».

## Формат записи

```markdown
## [дата] Sprint N — <статус: done | partial | blocked>
**Агент:** <модель/инструмент>
**Сделано:** 1–3 строки.
**Отклонения от спеки:** что и почему (или «нет»).
**Грабли/наблюдения:** самое ценное — неожиданности в коде, хрупкие места, неочевидные связи.
**Backlog-наблюдения:** идеи вне scope, замеченные по пути.
```

Записи добавляются только в конец файла. Чужие записи не редактировать.

---

## [2026-07-02] Sprint 0 — done (подготовка плана, вручную)
**Агент:** Claude (Fable 5), сессия review + планирования.
**Сделано:** review проекта (`fable_report.md`), спеки спринтов 1–5, этот worklog.
**Отклонения от спеки:** —
**Грабли/наблюдения (стартовый набор фактов о проекте):**
- Тесты: бот — `uv run pytest` в корне; web backend — `cd web/backend && uv run pytest`; frontend — `cd web/frontend && npx vitest run`. Docker для тестов не нужен.
- `web/frontend/src/design/theme.ts` дублирует токены из `styles.css`; тест `design-tokens.test.ts` проверяет синхронизацию. Правки токенов — всегда в оба файла.
- `map.test.tsx` (~600 строк) жёстко фиксирует текущее поведение карты, включая баги (zoom меняет только scale; легенда в cluster-режиме отсутствует). Спринты 1–2 намеренно ломают часть этих ассертов — тесты обновлять, а не подгонять код под старые тесты.
- `umap`/`hdbscan` импортируются лениво внутри методов `UmapProjector.project`/`HdbscanClusterer.cluster` — юнит-тесты backend используют фейковые projector/clusterer через DI в `AnalysisService`, реальные библиотеки в тестах не гоняются (медленно). Сохранять этот паттерн.
- Эмбеддинг-модель в `.env` — `intfloat/multilingual-e5-large`, 1024 dim (README упоминает `text-embedding-3-small` — README устарел). e5 требует префиксов `query:`/`passage:` — корпус сейчас без префиксов; решено НЕ трогать до отдельного решения (см. sprint_1_spec, out of scope).
- `data/enrichment.sqlite3` в репо — от **другого** дневника (образец структуры). `diary/` — настоящий дневник пользователя, read-only, для контекста. Ни то ни другое не использовать в автотестах и не коммитить.
- Роутинг фронта — самодельный hash-роутер в `App.tsx` (`routeFromHash`), query-параметры сейчас игнорируются.
- Формат id заметок в базе: `YYYY-MM-DDTHH:MM`, при дубликатах времени — суффикс `#n`. Логика генерации — в `src/dairy_bot/services/enrichment.py` (бот).
- Заголовки заметок в markdown: `## HH:MM`, `## HH:MM — voice|text`, постфактум — `## June 16 21:55`. Единый регекс — `NOTE_HEADING_RE` в `src/dairy_bot/services/enrichment.py`, у layer3 своя копия в `web/backend/dairy_web/vault_reader.py`.
- Управляемый enrichment-блок в markdown помечен маркером `<!-- dairy:note-enrichment -->` + строка `mood:: … · topics:: …`; `vault_reader.extract_note_raw_text` его вырезает из raw_text.
**Backlog-наблюдения:** `d3` объявлен в package.json, но не используется (понадобится в спринте 5); NotePanel получает `day_summary` и `note_path` с бэка, но не показывает (day_summary — в спринте 2).

## [2026-07-02] Sprint 1 — done
**Агент:** GPT-5 Codex.
**Сделано:** Backend-карта переведена на UMAP-reducer(10D, cosine) → HDBSCAN с фиксированными параметрами; добавлен `n_noise` в snapshot/cache/API. Подпись кэша теперь учитывает `note_entry_state.content_hash`, а OpenRouter-лейблер получил timeout и fallback на статические labels.
**Отклонения от спеки:** нет.
**Грабли/наблюдения:** Новый формат подписи (`state=...` вместо id-only при наличии content_hash) ожидаемо вызовет один полный recompute map-cache после деплоя. `uv sync` под repo `.python-version=3.13` падал на древнем transitive `llvmlite==0.36.0`; добавлен явный `numba>=0.62` и ограничение backend `requires-python = ">=3.12,<3.14"`, после чего lock перешёл на `numba==0.66.0`/`llvmlite==0.48.0`. Старый `analysis_cache.sqlite3` без `metadata.n_noise` пересоздаётся целиком.
**Backlog-наблюдения:** В тестах остаётся `StarletteDeprecationWarning` про `fastapi.testclient`/`httpx`; не относится к спринту, но позже стоит обновить тестовый клиент по рекомендации Starlette/FastAPI.

## [2026-07-02] Sprint 2 — done
**Агент:** GPT-5 Codex.
**Сделано:** Frontend карты переведён на чистый `viewTransform` (`zoomAt`/`panBy`) с zoom-to-cursor; добавлен единый `ui/Legend` и общий `highlight` для CLUSTER/MOOD/TOPIC. Шум теперь серый `noiseColor`, TOPIC-режим стал highlight-фильтром без 23-цветной раскраски, `n_noise` нормализуется с default 0, `day_summary` показывается в NotePanel.
**Отклонения от спеки:** ручная проверка через Vite+backend не запускалась; проверка выполнена автоматическими gate-командами из спеки.
**Грабли/наблюдения:** Тесты карты намеренно переписаны под новое поведение: cluster-legend кликается и приглушает остальные кластеры, mood-legend кликается, TOPIC использует graphite/ink/muted вместо `topicColor`, wheel теперь проверяет x/y по формуле, old-cache payload без `n_noise` не падает. `npm install` проходит, но выводит 1 low-severity audit item и предупреждение allow-scripts по `esbuild`/`fsevents`; на sprint-2 gates это не влияет.
**Backlog-наблюдения:** Для будущей ручной QA карты удобно поднять минимальный authenticated mock/backend fixture, чтобы не зависеть от реального дневника и не трогать `diary/`.

## [2026-07-02] Sprint 3 — done
**Агент:** GPT-5 Codex.
**Сделано:** Добавлены `read_day`/`list_day_dates`, API `/api/days`, `/api/days/latest`, `/api/days/{date}` с auth, prev/next и join enrichment по id. Frontend получил route `#journal[/YYYY-MM-DD]`, `JournalView` с day reader/month index и deep-links из Map/Memory/Seasons.
**Отклонения от спеки:** нет.
**Грабли/наблюдения:** Схема id в боте подтверждена: base `YYYY-MM-DDTHH:MM`, дубликаты в порядке файла получают `#2`, `#3`. `extract_note_raw_text` теперь использует общий парсер блоков; тест зафиксировал, что первый duplicate timestamp по-прежнему возвращает первый блок. Month index не содержит raw/summary/path; `raw_text` остаётся только в detail/day endpoints.
**Backlog-наблюдения:** Journal month index сейчас сканирует vault и перечитывает файлы на запрос, как разрешено спекой; если vault сильно вырастет, нужен cache/manifest для counts и date list.

## [2026-07-02] Sprint 4 — done
**Агент:** GPT-5 Codex.
**Сделано:** Добавлен internal edit API бота на aiohttp с токеном, optimistic hash replacement, `journal_lock` + `git_sync`; layer3 получил `raw_text_sha256` в detail/day payloads и `PUT /api/notes/{id}` как proxy в бота. Frontend получил общий `NoteEditor` в Map NotePanel и Journal reader с обработкой 409/502; compose/env/README описывают opt-in editing без публикации порта 8081.
**Отклонения от спеки:** нет.
**Грабли/наблюдения:** Схема id для edit lookup совпала с Sprint 3: `YYYY-MM-DDTHH:MM` и `#n` для дублей, поэтому replace-функция ищет конкретный heading по `note_id`. Git flow edit API: `prepare_for_write` → read/replace/write → `commit_and_push`; `GitPushError` считается локальным успехом и только логируется. Root `uv sync` чистит backend-only packages (`umap`/`hdbscan`), поэтому root pytest теперь явно ограничен `testpaths = ["tests"]`; backend tests остаются отдельным gate из `web/backend`. Существующий `.gitignore` игнорирует любой каталог `journal/`, из-за чего frontend journal-компоненты нужно было добавить через `git add -f`.
**Backlog-наблюдения:** Web save сейчас сообщает только общие статусы; если понадобится UX точнее, backend уже пробрасывает HTTP-классы ошибок, и текст можно уточнить без изменения bot API. Стоит сузить ignore-rule `journal/`, чтобы будущие файлы под `web/frontend/src/journal/` не требовали force-add.
