# Sprint 3 — Journal Reader: чтение дневника целиком + deep-links

## Цель

Появляется четвёртый view `#journal` — полноценное чтение дневника: день целиком (summary + все
заметки с полным текстом), навигация prev/next, месячный индекс, deep-link на конкретную дату.
Всё, что показывает дату (точка карты, memory-карточка, панель дня в Seasons), ведёт сюда.

## Контекст (см. fable_report.md P0-1)

- Сейчас полный текст заметки виден только в NotePanel карты (клик по точке). Ни ленты, ни
  чтения дня целиком нет.
- `vault_reader.extract_note_raw_text(note_path, ts)` умеет извлекать **одну** заметку по `ts`:
  ищет заголовок регексом `^##\s+(?:[A-Z][a-z]+\s+\d{1,2}\s+)?(?P<ts>\d{2}:\d{2})(?:\s+—\s+(voice|text))?\s*$`,
  берёт тело до следующего `##`, вырезает управляемый блок `<!-- dairy:note-enrichment -->`.
- Hash-роутер (`App.tsx`, `routeFromHash`) знает только `map|seasons|memory`, параметры игнорирует.
  Ссылка `#seasons?date=...` из NotePanel — мёртвая.
- id заметок в базе: `YYYY-MM-DDTHH:MM`, дубликаты времени — суффикс `#n` (n ≥ 2), нумерация в
  порядке следования блоков в файле (сверить с генерацией id в `src/dairy_bot/services/enrichment.py`
  и повторить её точно).
- Инвариант G4 уточняется этим спринтом: `raw_text` разрешён в **двух** детальных эндпоинтах —
  `/api/notes/{id}` и новом `/api/days/{date}`; в массовых payload'ах — по-прежнему запрещён.

## Scope

### In scope
1. Backend: `GET /api/days/{date}` (день целиком, с raw-текстами) и `GET /api/days?month=YYYY-MM`
   (индекс месяца, без raw).
2. `vault_reader`: разбор всех блоков дневного файла; список существующих дат по vault.
3. Frontend: роутер с параметрами; view `#journal` (+ `#journal/YYYY-MM-DD`); reader UI.
4. Deep-links: NotePanel карты → `OPEN DAY`, MemoryView → `READ THIS DAY`, Seasons DayPanel →
   `READ THIS DAY`.
5. Тесты backend и frontend.

### Out of scope
- Редактирование (спринт 4). Reader в этом спринте read-only.
- Markdown-рендеринг (wikilinks, заголовки внутри заметок) — текст рендерится как plain
  `whitespace-pre-wrap` serif, как в NotePanel. Рендеринг markdown — backlog.
- Поиск, фильтры по темам в ленте — backlog.
- Redesign SeasonsView (спринт 5) — только добавление ссылки в существующий DayPanel.

## Ключевые архитектурные решения

**АР-1. Источник правды для Reader — vault, обогащение — украшение.** День читается из
markdown-файла (`{VAULT_DIR}/YYYY/MM/YYYY-MM-DD.md`); enrichment-данные (mood, topics, gist,
day summary) подтягиваются из БД и присоединяются к блокам, **если** найдены. Отсутствие
enrichment у заметки/дня не мешает чтению (кейс: свежие дни, которые бот ещё не обогатил).

**АР-2. `vault_reader.read_day(date)`** — новый разбор всего файла:
```python
@dataclass(frozen=True)
class DayNoteBlock:
    ts: str                # "21:55"
    kind: str | None       # "voice" | "text" | None
    heading_display: str   # исходный заголовок без "## " (например "June 16 21:55")
    raw_text: str          # тело без managed enrichment-блока, trimmed
```
Возвращает блоки в порядке следования в файле. Тем же регексом, что `extract_note_raw_text`
(вынести регекс/вырезание enrichment-блока в общие приватные функции модуля, без дублирования).
Защита от path traversal — как в существующем коде (`_is_relative_to`).

**АР-3. Сопоставление блоков с БД по id.** Для каждого блока строится кандидат-id
`f"{date}T{ts}"`, для повторяющихся `ts` в пределах дня — суффиксы по порядку следования
(`...#2`, `...#3`). Точную схему суффиксов **сверить с генерацией id в боте**
(`src/dairy_bot/services/enrichment.py`) и повторить 1:1; расхождение = стоп и вопрос.
Если id не найден в БД — блок отдаётся без enrichment-полей.

**АР-4. `vault_reader.list_day_dates()`** — сортированный список дат по файлам vault
(глоб `[0-9][0-9][0-9][0-9]/[0-9][0-9]/????-??-??.md`, валидация имени). Prev/next вычисляются
по этому списку (соседние **существующие** дни, как в навигации самих заметок). Сканирование
~сотен файлов на запрос допустимо; кэширование — backlog.

**АР-5. Контракты API:**
```
GET /api/days/{date}                # date = YYYY-MM-DD, иначе 422
→ {
  date, prev_date: str|null, next_date: str|null,
  day: {mood, mood_confidence, summary, key_topics, weekday, is_weekend, season, facts} | null,
  notes: [{id, ts, kind, heading_display, raw_text,
           mood: str|null, topics: str[], gist: str|null}]
}                                   # 404 если файла нет
GET /api/days?month=YYYY-MM         # 422 при неверном формате
→ { days: [{date, note_count, mood: str|null}] }   # только существующие файлы; БЕЗ raw, БЕЗ summary
```
Оба — под auth (G3). `note_count` — число блоков в файле.

**АР-6. Роутер с параметром.** `routeFromHash` возвращает `{ key: RouteKey; param?: string }`;
формат `#journal/2026-06-08`. Неверный параметр (не дата) → как `#journal` без параметра.
`#journal` без даты → последняя существующая дата (первый запрос: `/api/days?month=` текущего
месяца, при пустом — предыдущего; проще: новый параметр не вводить — фронт запрашивает
`/api/days/{today}`; при 404 берёт `prev_date`… но 404 не несёт prev). Решение: `#journal` без
параметра → фронт вызывает `GET /api/days/latest` — **добавить** этот спец-маршрут на backend
(отдаёт день последней существующей даты, 404 только при полностью пустом vault).
Существующие маршруты `map|seasons|memory` работают как раньше.

**АР-7. Reader UI** (`web/frontend/src/journal/JournalView.tsx` + подкомпоненты):
- Заголовок дня: serif-дата крупно, mood-`Tag`, weekday mono; day summary serif-абзацем (если есть).
- Кнопки `← PREV DAY` / `NEXT DAY →` (ghost, mono), disabled при null.
- Блоки заметок: mono-заголовок `21:55 · VOICE` (kind — если известен), serif-текст
  `whitespace-pre-wrap`, под текстом чипы mood/topics (если есть).
- Месячный мини-индекс: mono-список дат месяца с точкой-mood (данные из `/api/days?month=`),
  клик → `#journal/{date}`; переключатель месяца ← →.
- Deep-links: `NotePanel` — ссылка `OPEN DAY →` на `#journal/{note.date}` (заменяет мёртвую
  `#seasons?date=`); `MemoryView` — кнопка `READ THIS DAY`; Seasons `DayPanel` — ссылка
  `READ THIS DAY`.
- Nav-панель приложения получает пункт `JOURNAL` (первым, перед MAP).

## Инварианты спринта

- S3-1. Vault по-прежнему read-only из layer3 (только чтение файлов).
- S3-2. `raw_text` — только в `/api/days/{date}`, `/api/days/latest` и `/api/notes/{id}`.
  Месячный индекс не содержит ни raw, ни summary, ни путей файлов.
- S3-3. Ошибки чтения не раскрывают пути файловой системы в теле ответа (как существующий
  паттерн `NoteRawTextNotFound`).
- S3-4. Никаких изменений в БД-слое бота; enrichment join — только чтение.
- S3-5. Reader работает при полностью отсутствующем enrichment (vault-only день).

## Edge cases и ожидаемое поведение

| # | Кейс | Ожидаемое поведение |
|---|---|---|
| E1 | `GET /api/days/2026-02-30` (невалидная дата) | 422 |
| E2 | Дата валидна, файла нет | 404, без раскрытия путей |
| E3 | День есть в vault, отсутствует в таблице `days` | `day: null`, заметки отдаются; UI показывает день без summary/mood |
| E4 | Блок есть в файле, id не найден в `notes` БД | Заметка без mood/topics/gist; UI не рисует чипы |
| E5 | Два блока с одинаковым `ts` | Второй матчится на id `...#2`; порядок в ответе = порядок в файле |
| E6 | Постфактум-заголовок `## June 16 21:55` | `ts="21:55"`, `heading_display="June 16 21:55"`, UI показывает исходный заголовок |
| E7 | Файл с 2025-структурой (вольные `###`-подзаголовки внутри блока) | Подзаголовки — часть raw_text, рендерятся как текст |
| E8 | Первый/последний день дневника | `prev_date`/`next_date` = null, кнопка disabled |
| E9 | Дни-пропуски между датами | prev/next перескакивают пропуски (соседние существующие файлы) |
| E10 | `#journal/not-a-date` | Ведёт себя как `#journal` (latest) |
| E11 | Пустой vault | `/api/days/latest` → 404; UI: состояние `NO ENTRIES YET` |
| E12 | Файл дня без единого `## HH:MM`-блока | `notes: []`, день открывается (summary, если есть) |
| E13 | `month=2026-13` | 422 |

## Критерии приёмки

- **AC-1.** Backend-тесты: временный синтетический vault (tmp_path) с ≥ 3 днями, включая E5, E6,
  E12; `/api/days/{date}` возвращает блоки в порядке файла с корректным join enrichment.
- **AC-2.** Тест: месячный индекс не содержит подстрок raw-текста и `note_path` (аналог
  существующего no-leak теста).
- **AC-3.** Все новые эндпоинты возвращают 401 без сессии.
- **AC-4.** Frontend-тесты: рендер дня (заголовок, summary, N блоков, чипы), prev/next навигация
  меняет hash, `#journal/{date}` открывает нужную дату, E10/E11.
- **AC-5.** Тесты ссылок: из NotePanel, MemoryView и Seasons DayPanel переход ведёт на
  `#journal/{правильная дата}` (мёртвая ссылка `#seasons?date=` устранена).
- **AC-6.** Все команды проверки зелёные; существующие тесты роутинга обновлены под новый
  формат маршрута.

## Чек-лист выполнения

- [x] 1. Прочитан worklog, спека; изучена генерация note-id в `src/dairy_bot/services/enrichment.py` (только чтение) и `vault_reader.py`.
- [x] 2. `vault_reader.read_day()` + рефакторинг общих функций с `extract_note_raw_text` (без изменения его поведения) + тесты (E5–E7, E12).
- [x] 3. `vault_reader.list_day_dates()` + prev/next + тесты (E8, E9).
- [x] 4. Join блоков с enrichment по id-схеме бота + тесты (E3–E5).
- [x] 5. Эндпоинты `/api/days/{date}`, `/api/days/latest`, `/api/days?month=` + auth + тесты (AC-1–AC-3, E1, E2, E11, E13).
- [x] 6. Роутер с параметрами + тесты (E10).
- [x] 7. `JournalView`: день, блоки, prev/next, месячный индекс + тесты (AC-4).
- [x] 8. Deep-links из NotePanel / MemoryView / Seasons DayPanel + пункт JOURNAL в nav + тесты (AC-5).
- [x] 9. Все команды проверки зелёные.
- [x] 10. Worklog дополнен; один коммит `sprint-3: ...` в `web-refactoring`.

## Definition of Done

- Все AC выполнены, чек-лист `[x]`, backend + frontend тесты и `tsc -b` зелёные.
- Инвариант S3-2 покрыт тестом (no-leak в индексе).
- Reader спроектирован в языке design.md: serif для текста заметок и summary, mono для дат,
  заголовков блоков, кнопок; 2px радиусы; cream-канва (G7–G9).
- Worklog содержит запись, включая любые находки о несоответствии id-схемы.

## Команды проверки

```bash
cd web/backend && uv run pytest
cd web/frontend && npx vitest run && npx tsc -b
```
