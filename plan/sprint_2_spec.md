# Sprint 2 — Frontend карты: zoom к курсору, единая легенда, честная раскраска

## Цель

Карта становится управляемой и объяснимой: колесо приближает к курсору; во всех трёх режимах
(CLUSTER / MOOD / TOPIC) — единообразная кликабельная легенда; шум не маскируется под кластер;
режим TOPIC перестаёт кодировать 23 темы 12-ю цветами. Только frontend (`web/frontend/`).

## Контекст (см. fable_report.md §1.2, §1.3)

- `MapView.tsx`, `handleWheel`: меняет только `scale`, `x/y` не трогает; `transformOrigin: "0px 0px"`
  → zoom всегда к левому верхнему углу. `event.clientX/Y` не читаются.
- `MapLegend`: `if (points.length === 0 || colorMode === "cluster") return null` — у default-режима
  нет легенды вовсе. MOOD — статичные span'ы, TOPIC — кнопки-фильтры: три разных контракта.
- `clusterColor`: `clusterPalette[Math.abs(clusterId) % clusterPalette.length]` → шум (`-1`)
  красится цветом кластера 1.
- `topicColor()` хеширует 23 темы в `topicPalette` из 12 цветов → коллизии. По исходной спеке
  проекта (task.md) topics должны работать как highlight-фильтр, а не как одновременная раскраска.
- `clusterPalette` — 10 слабо различимых muted-свотчей.
- `NotePanel` получает с бэка `day_summary`, но не отображает.
- После спринта 1 `/api/map` содержит `n_noise`.

## Scope

### In scope
1. Zoom к курсору (wheel), математика трансформа — в отдельный чистый модуль с юнит-тестами.
2. Единый компонент легенды для всех трёх режимов; фиксированная зона, единый вид, кликабельность.
3. Раскраска: шум → нейтральный серый + чип UNCLUSTERED; новая различимая clusterPalette;
   TOPIC-режим без раскраски точек по темам.
4. Единая модель выделения (highlight) для всех режимов.
5. `day_summary` в NotePanel.
6. Обновление тестов, фиксирующих старое поведение.

### Out of scope
- Изменения backend (кроме чтения нового поля `n_noise` из payload).
- Ссылки из NotePanel в Journal Reader (спринт 3; текущую мёртвую ссылку `#seasons?date=` пока не трогать).
- Двойной клик/пинч-zoom, fit-to-cluster, лассо — не делать (backlog).
- SeasonsView (спринт 5).

## Ключевые архитектурные решения

**АР-1. Чистый модуль трансформа** `web/frontend/src/map/viewTransform.ts`:
```ts
export type ViewTransform = { scale: number; x: number; y: number };
export function zoomAt(current: ViewTransform, cursorX: number, cursorY: number,
                       deltaY: number, opts: {minZoom: number; maxZoom: number; intensity: number}): ViewTransform;
export function panBy(start: ViewTransform, dx: number, dy: number): ViewTransform;
```
Формула zoomAt (cursorX/Y — координаты курсора относительно панели, из `getBoundingClientRect`):
```
k = clamp(scale * exp(-clamp(deltaY, -240, 240) * intensity), minZoom, maxZoom) / scale
newX = cursorX - (cursorX - x) * k
newY = cursorY - (cursorY - y) * k
newScale = scale * k
```
`MapView` использует только эти функции; вся математика покрывается юнит-тестами без DOM.
Существующие константы (`minZoom=0.5`, `maxZoom=6`, `zoomIntensity=0.0008`) сохранить.

**АР-2. Единый компонент легенды** `web/frontend/src/ui/Legend.tsx` (в `ui/` — в спринте 5 его
переиспользует календарь):
```ts
type LegendItem = { key: string; label: string; count: number; swatch?: string };
<Legend items={LegendItem[]} activeKey={string | null}
        onToggle={(key: string) => void} />
```
- Всегда рендерится заголовок `LEGEND` и ряд чипов: `[swatch?] LABEL count`, mono uppercase,
  2px радиус, hairline-граница.
- Каждый чип — `<button>` с `aria-pressed`; клик по активному чипу снимает выделение.
- Активный чип — акцент Signal Orange (единственный orange-элемент view, инвариант G9).
- Зона легенды имеет стабильную минимальную высоту, чтобы переключение режимов не дёргало layout.

**АР-3. Наполнение легенды по режимам:**
- **CLUSTER**: по одному чипу на кластер из `clusters` payload (`label` LLM-лейбл uppercase,
  `count = size`, swatch = цвет кластера), плюс последний чип `UNCLUSTERED` (`count = n_noise`,
  swatch = серый шума) — только если `n_noise > 0`.
- **MOOD**: как сейчас по составу (moodOrder + counts), но теперь чипы кликабельны.
- **TOPIC**: чипы `ALL TOPICS` + темы по убыванию count, **без swatch** (точки по темам не
  красятся). Существующая кнопка "ALL TOPICS" эквивалентна сбросу выделения — оставить как чип
  без count, активный когда выделения нет.

**АР-4. Единая модель выделения.** Вместо только-`selectedTopic` — одно состояние
`highlight: { mode: ColorMode; key: string } | null`. Смена `colorMode` сбрасывает highlight.
Правила раскраски точек (`pointColor`):

| Режим | Без выделения | С выделением |
|---|---|---|
| CLUSTER | цвет кластера; `-1` → `noiseColor` | точки выбранного кластера — своим цветом; остальные → `topicMutedColor` |
| MOOD | цвет mood | выбранный mood — своим цветом; остальные → `topicMutedColor` |
| TOPIC | все точки → graphite `#434343` | точки с выбранной темой → ink-black `#181818`; остальные → `topicMutedColor` |

**АР-5. Палитры** (`design/palettes.ts`):
- `noiseColor = "#cbcbcb"` (Concrete) — новый экспорт; в `clusterColor` спецобработка `-1` **до**
  модульной арифметики.
- `clusterPalette` заменить на 8 различимых muted-цветов (различимы по hue при низкой насыщенности,
  в семействе design.md; без вивид-радуги). Ориентир (можно скорректировать по контрасту на cream):
  `#4e79a7` steel blue, `#8a9a5b` moss, `#b07aa1` mauve, `#c2843c` ochre, `#5f9e9c` teal-grey,
  `#a05d56` clay, `#7b6f9e` dusty violet, `#6b7b8c` slate blue.
- `topicColor()` больше не используется для точек; экспорт удалить, если не останется потребителей
  (проверить SeasonsView — если использует, оставить с комментарием deprecated до спринта 5).
- Mood-палитра не меняется (G10).

**АР-6. Кластерные халлы** (`ClusterLayer`) остаются как есть (после спринта 1 они снова начнут
появляться). При highlight кластера его халл — как сейчас, остальные халлы приглушаются opacity.

## Инварианты спринта

- S2-1. Пан (drag) и RESET VIEW работают как раньше: 1:1 к пикселям, reset → `{scale:1,x:0,y:0}`.
- S2-2. Точка мира под курсором при wheel-zoom неподвижна (математически, юнит-тест).
- S2-3. `wheel` остаётся non-passive, `preventDefault`/`stopPropagation` сохраняются (страница не
  скроллится над картой) — существующие тесты.
- S2-4. Легенда присутствует во всех трёх режимах при `points.length > 0`; при `points.length === 0`
  не рендерится (как сейчас).
- S2-5. Orange — только на одном активном чипе (и существующих активных nav-элементах); точки
  никогда не красятся в orange в этом спринте.
- S2-6. Раскраска mood-точек идентична moodPalette календаря (G10) — не трогать mood-цвета.
- S2-7. Дизайн-токены — только из styles.css/theme.ts + palettes.ts (G13: правки в обоих файлах).

## Edge cases и ожидаемое поведение

| # | Кейс | Ожидаемое поведение |
|---|---|---|
| E1 | Wheel при `scale = maxZoom` (6) | scale не растёт, x/y не меняются (k=1 — проверить, что нет дрейфа) |
| E2 | Wheel при `scale = minZoom` (0.5) | аналогично, без дрейфа |
| E3 | `deltaY` за пределами ±240 | клампится (текущее поведение), zoomAt монотонен |
| E4 | Все точки — шум (`clusters=[]`, `n_noise=N`) | CLUSTER-режим: все точки серые, легенда = один чип UNCLUSTERED N |
| E5 | `n_noise = 0` | Чип UNCLUSTERED не рендерится |
| E6 | Кластеров больше 8 (палитра короче) | Циклическая палитра (mod) — допустимо; шум по-прежнему серый |
| E7 | Смена режима при активном highlight | highlight сбрасывается, layout легенды не прыгает |
| E8 | Тема выбрана, у точки несколько тем, среди них выбранная | Точка считается matching (includes) |
| E9 | Заметка без `day_summary` (null/пустая строка) | Блок DAY SUMMARY в NotePanel не рендерится |
| E10 | Payload без поля `n_noise` (старый кэш бэка) | Трактуется как 0, UI не падает (normalizeMapPayload задаёт default) |

## Критерии приёмки

- **AC-1.** Юнит-тесты `viewTransform.test.ts`: (а) инвариант курсора — для произвольных transform
  и точки курсора мировая точка под курсором до и после `zoomAt` совпадает (в пределах ε);
  (б) кламп scale; (в) отсутствие дрейфа на границах (E1/E2).
- **AC-2.** Компонентный тест: wheel с `clientX/clientY` над серединой панели меняет и scale, и
  translate согласно формуле (обновить существующие ассерты, которые проверяли «x/y не изменились»).
- **AC-3.** В CLUSTER-режиме рендерится легенда с лейблами кластеров и counts; клик по чипу
  приглушает точки других кластеров; повторный клик возвращает.
- **AC-4.** В MOOD-режиме чипы кликабельны и фильтруют; в TOPIC-режиме точки не имеют
  per-topic цветов (все graphite до выбора), выбор темы даёт ink/muted-разделение.
- **AC-5.** Точки с `cluster_id = -1` — цвета `noiseColor` во всех случаях; чип UNCLUSTERED
  показывает `n_noise`.
- **AC-6.** `day_summary` отображается в NotePanel (mono-заголовок `DAY SUMMARY` + serif-текст),
  скрыт при его отсутствии.
- **AC-7.** `npx vitest run` и `npx tsc -b` зелёные; `design-tokens.test.ts` зелёный.

## Чек-лист выполнения

- [x] 1. Прочитан worklog, спека, `MapView.tsx`, `palettes.ts`, `map.test.tsx`.
- [x] 2. `viewTransform.ts` + юнит-тесты (AC-1, E1–E3).
- [x] 3. `MapView` переведён на `zoomAt`/`panBy`; wheel читает координаты курсора относительно панели; компонентные тесты обновлены (AC-2, S2-1, S2-3).
- [x] 4. `noiseColor` + фикс `clusterColor` для `-1`; новая `clusterPalette` (8 цветов); тесты AC-5, E4–E6.
- [x] 5. `ui/Legend.tsx` + замена `MapLegend` на единый контракт во всех режимах (AC-3, S2-4, E7).
- [x] 6. Единый `highlight`-стейт + правила pointColor по таблице АР-4 (AC-3, AC-4, E8).
- [x] 7. TOPIC-режим: убрана per-topic раскраска точек; `topicColor` удалён или помечен deprecated.
- [x] 8. `n_noise` в `services/map.ts` (тип + normalize с default 0, E10).
- [x] 9. `day_summary` в NotePanel (AC-6, E9).
- [x] 10. Все команды проверки зелёные; обновлённые тесты отражают новое поведение осознанно (перечислить в worklog).
- [x] 11. Worklog дополнен; один коммит `sprint-2: ...` в `web-refactoring`.

## Definition of Done

- Все AC выполнены, чек-лист `[x]`, тесты и типы зелёные.
- Ручная проверка глазами (если окружение позволяет поднять vite + backend с образцовой базой):
  zoom к курсору, переключение трёх режимов без прыжков layout, клики по легенде. Если поднять
  окружение нельзя — зафиксировать в worklog, что проверка была только тестовой.
- Никаких новых цветов вне `palettes.ts`; G7–G10 соблюдены.

## Команды проверки

```bash
cd web/frontend && npm install && npx vitest run
cd web/frontend && npx tsc -b
```
