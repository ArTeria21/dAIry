import type { CSSProperties, ReactNode } from "react";
import type { PointerEvent } from "react";
import { useEffect, useRef, useState } from "react";

import {
  clusterPalette,
  moodPalette,
  topicColor,
  topicMutedColor,
  type Mood,
} from "../design/palettes";
import { chromeTextClass, readingTextClass } from "../design/theme";
import {
  fetchMap,
  fetchNoteDetails,
  type MapCluster,
  type MapPayload,
  type MapPoint,
  type NoteDetails,
} from "../services/map";
import { Tag } from "../ui/primitives";
import { cx } from "../ui/classNames";

const schematicBlue = "#0d6ea5";
const mapSurfaceHeightClass = "h-[min(62vh,560px)] min-h-[440px]";
const notePanelFrameClass = "max-h-[min(62vh,560px)] overflow-y-auto rounded-[2px] border border-hairline bg-cream-paper";

type ColorMode = "cluster" | "mood" | "topic";

type PointStyle = CSSProperties & {
  "--point-color": string;
};

type LegendStyle = CSSProperties & {
  "--legend-color": string;
};

type LegendItem = {
  id: string;
  label: string;
  color: string;
  count: number;
  testId: string;
};

type ViewTransform = {
  scale: number;
  x: number;
  y: number;
};

type DragState = {
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startX: number;
  startY: number;
};

const initialViewTransform: ViewTransform = { scale: 1, x: 0, y: 0 };
const coordinatePadding = 0.05;
const minZoom = 0.5;
const maxZoom = 6;
const zoomIntensity = 0.0008;
const baseGridSize = 96;
const moodOrder: Mood[] = ["joy", "calm", "sadness", "anger", "fear", "neutral", "mixed"];

export function MapView() {
  const [payload, setPayload] = useState<MapPayload | null>(null);
  const [colorMode, setColorMode] = useState<ColorMode>("cluster");
  const [selectedTopic, setSelectedTopic] = useState<string>("");
  const [hoveredPoint, setHoveredPoint] = useState<MapPoint | null>(null);
  const [selectedPointId, setSelectedPointId] = useState<number | null>(null);
  const [note, setNote] = useState<NoteDetails | null>(null);
  const [noteUnavailable, setNoteUnavailable] = useState(false);
  const [viewTransform, setViewTransform] = useState<ViewTransform>(initialViewTransform);
  const [dragState, setDragState] = useState<DragState | null>(null);
  const mapPanelRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let active = true;
    fetchMap()
      .then((nextPayload) => {
        if (active) {
          setPayload(normalizeMapPayload(nextPayload));
        }
      })
      .catch(() => {
        if (active) {
          setPayload(emptyMapPayload());
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const mapPanel = mapPanelRef.current;
    if (!mapPanel || !payload) {
      return;
    }

    function handleWheel(event: WheelEvent) {
      if (event.cancelable) {
        event.preventDefault();
      }
      event.stopPropagation();

      const zoomFactor = Math.exp(-clamp(event.deltaY, -240, 240) * zoomIntensity);
      setViewTransform((current) => ({
        ...current,
        scale: clamp(Number((current.scale * zoomFactor).toFixed(4)), minZoom, maxZoom),
      }));
    }

    mapPanel.addEventListener("wheel", handleWheel, { passive: false });
    return () => mapPanel.removeEventListener("wheel", handleWheel);
  }, [payload]);

  async function selectPoint(point: MapPoint) {
    setSelectedPointId(point.id);
    setNote(null);
    setNoteUnavailable(false);
    try {
      setNote(await fetchNoteDetails(point.id));
    } catch {
      setNoteUnavailable(true);
    }
  }

  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    const target = event.target;
    if (event.button !== 0 || (target instanceof HTMLElement && target.closest("button"))) {
      return;
    }

    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragState({
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: viewTransform.x,
      startY: viewTransform.y,
    });
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!dragState || dragState.pointerId !== event.pointerId || event.buttons !== 1) {
      return;
    }

    setViewTransform((current) => ({
      ...current,
      x: dragState.startX + event.clientX - dragState.startClientX,
      y: dragState.startY + event.clientY - dragState.startClientY,
    }));
  }

  function handlePointerUp(event: PointerEvent<HTMLDivElement>) {
    if (dragState?.pointerId === event.pointerId) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      setDragState(null);
    }
  }

  if (!payload) {
    return <p className={chromeTextClass}>LOADING MAP</p>;
  }

  return (
    <div className="grid gap-5">
      <MapControls
        colorMode={colorMode}
        onColorModeChange={(mode) => {
          setColorMode(mode);
          if (mode !== "topic") {
            setSelectedTopic("");
          }
        }}
        onResetView={() => setViewTransform(initialViewTransform)}
      />

      <MapLegend
        colorMode={colorMode}
        onTopicSelect={setSelectedTopic}
        points={payload.points}
        selectedTopic={selectedTopic}
      />

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <section
          aria-label="JOURNAL EMBEDDING MAP"
          className={cx(
            mapSurfaceHeightClass,
            "relative cursor-grab touch-none overflow-hidden overscroll-contain rounded-[2px] border border-hairline bg-cream-paper",
          )}
          data-testid="map-panel"
          ref={mapPanelRef}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          {payload.points.length === 0 ? (
            <div className={cx(mapSurfaceHeightClass, "grid place-items-center")}>
              <p className={cx(chromeTextClass, "text-[11px] text-slate")}>NO NOTES TO MAP</p>
            </div>
          ) : (
            <>
              <GridLayer viewTransform={viewTransform} />
              <div
                className="absolute inset-0 origin-center"
                data-testid="map-viewport"
                style={{
                  transform: `translate(${viewTransform.x}px, ${viewTransform.y}px) scale(${viewTransform.scale})`,
                  transformOrigin: "0px 0px",
                }}
              >
                <ClusterLayer clusters={payload.clusters} points={payload.points} />
                {payload.points.map((point) => (
                  <PointButton
                    colorMode={colorMode}
                    key={point.id}
                    onHover={setHoveredPoint}
                    onSelect={selectPoint}
                    point={point}
                    selected={selectedPointId === point.id}
                    selectedTopic={selectedTopic}
                  />
                ))}
              </div>
            </>
          )}

          {hoveredPoint ? (
            <div
              className={cx(
                readingTextClass,
                "absolute left-4 top-4 max-w-[260px] rounded-[2px] border border-hairline bg-cream-paper px-3 py-2 text-sm shadow-subtle",
              )}
              role="tooltip"
            >
              {hoveredPoint.gist}
            </div>
          ) : null}
        </section>

        <NotePanel note={note} noteUnavailable={noteUnavailable} selectedPointId={selectedPointId} />
      </div>
    </div>
  );
}

function MapControls({
  colorMode,
  onColorModeChange,
  onResetView,
}: {
  colorMode: ColorMode;
  onColorModeChange: (mode: ColorMode) => void;
  onResetView: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={cx(chromeTextClass, "mr-2 text-[10px] text-slate")}>COLOR BY</span>
      {(["cluster", "mood", "topic"] as const).map((mode) => (
        <button
          aria-pressed={colorMode === mode}
          className={cx(
            chromeTextClass,
            "rounded-[2px] border border-hairline px-3 py-2 text-[10px]",
            colorMode === mode ? "border-schematic-blue text-ink-black" : "text-slate",
          )}
          key={mode}
          onClick={() => onColorModeChange(mode)}
          type="button"
        >
          {mode.toUpperCase()}
        </button>
      ))}
      <button
        className={cx(chromeTextClass, "ml-auto rounded-[2px] border border-hairline px-3 py-2 text-[10px] text-slate")}
        onClick={onResetView}
        type="button"
      >
        RESET VIEW
      </button>
    </div>
  );
}

function MapLegend({
  colorMode,
  onTopicSelect,
  points,
  selectedTopic,
}: {
  colorMode: ColorMode;
  onTopicSelect: (topic: string) => void;
  points: MapPoint[];
  selectedTopic: string;
}) {
  if (points.length === 0 || colorMode === "cluster") {
    return null;
  }

  if (colorMode === "mood") {
    const items = moodLegendItems(points);

    return (
      <LegendFrame label="MOOD COLOR LEGEND">
        {items.map((item) => (
          <span
            className={cx(
              chromeTextClass,
              "inline-flex h-8 items-center gap-2 rounded-[2px] border border-hairline bg-cream-paper px-2 text-[10px] text-ink-black",
            )}
            key={item.id}
          >
            <LegendSwatch item={item} />
            <span>{item.label}</span>
            <span className="text-slate">{item.count}</span>
          </span>
        ))}
      </LegendFrame>
    );
  }

  const topicItems = topicLegendItems(points);
  if (topicItems.length === 0) {
    return null;
  }

  return (
    <LegendFrame label="TOPIC COLOR LEGEND">
      <button
        aria-pressed={selectedTopic === ""}
        className={cx(
          chromeTextClass,
          "inline-flex h-8 items-center rounded-[2px] border px-2 text-[10px]",
          selectedTopic === "" ? "border-schematic-blue text-ink-black" : "border-hairline text-slate",
        )}
        onClick={() => onTopicSelect("")}
        type="button"
      >
        ALL TOPICS
      </button>
      {topicItems.map((item) => (
        <button
          aria-label={item.label}
          aria-pressed={selectedTopic === item.id}
          className={cx(
            chromeTextClass,
            "inline-flex h-8 max-w-full items-center gap-2 rounded-[2px] border bg-cream-paper px-2 text-[10px]",
            selectedTopic === item.id ? "border-schematic-blue text-ink-black" : "border-hairline text-slate",
          )}
          key={item.id}
          onClick={() => onTopicSelect(item.id)}
          type="button"
        >
          <LegendSwatch item={item} />
          <span>{item.label}</span>
          <span aria-hidden="true" className="text-slate">
            {item.count}
          </span>
        </button>
      ))}
    </LegendFrame>
  );
}

function LegendFrame({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div aria-label={label} className="grid gap-2" role="group">
      <span className={cx(chromeTextClass, "text-[10px] text-slate")}>LEGEND</span>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

function LegendSwatch({ item }: { item: LegendItem }) {
  const style: LegendStyle = {
    "--legend-color": item.color,
    backgroundColor: "var(--legend-color)",
  };

  return (
    <span
      aria-hidden="true"
      className="h-3 w-3 shrink-0 rounded-[2px] border border-hairline"
      data-testid={item.testId}
      style={style}
    />
  );
}

function GridLayer({ viewTransform }: { viewTransform: ViewTransform }) {
  const gridSize = formatPx(baseGridSize * viewTransform.scale);
  const gridPosition = `${formatPx(viewTransform.x)} ${formatPx(viewTransform.y)}`;

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0"
      data-testid="map-grid"
      style={{
        backgroundImage:
          "linear-gradient(to right, #e5e5e5 1px, transparent 1px), linear-gradient(to bottom, #e5e5e5 1px, transparent 1px)",
        backgroundPosition: gridPosition,
        backgroundSize: `${gridSize} ${gridSize}`,
      }}
    />
  );
}

function ClusterLayer({
  clusters,
  points,
}: {
  clusters: MapCluster[];
  points: MapPoint[];
}) {
  return (
    <svg aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full">
      {clusters.map((cluster) => {
        const clusterPoints = points.filter((point) => point.cluster_id === cluster.id);
        const box = clusterBox(clusterPoints);
        return (
          <g key={cluster.id}>
            <rect
              data-testid={`cluster-hull-${cluster.id}`}
              fill="none"
              height={`${box.height}%`}
              rx="2"
              stroke={schematicBlue}
              strokeWidth="1"
              width={`${box.width}%`}
              x={`${box.x}%`}
              y={`${box.y}%`}
            />
            <text
              className={cx(chromeTextClass, "font-plexmono")}
              fill={schematicBlue}
              fontSize="11"
              x={`${box.x}%`}
              y={`${Math.max(4, box.y - 2)}%`}
            >
              {cluster.label.toUpperCase()}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function PointButton({
  colorMode,
  onHover,
  onSelect,
  point,
  selected,
  selectedTopic,
}: {
  colorMode: ColorMode;
  onHover: (point: MapPoint | null) => void;
  onSelect: (point: MapPoint) => void;
  point: MapPoint;
  selected: boolean;
  selectedTopic: string;
}) {
  const color = pointColor(point, colorMode, selectedTopic);
  const style: PointStyle = {
    "--point-color": color,
    backgroundColor: "var(--point-color)",
    left: `${point.x * 100}%`,
    top: `${(1 - point.y) * 100}%`,
  };

  return (
    <button
      aria-label={`NOTE ${point.id}`}
      className={cx(
        "absolute -translate-x-1/2 -translate-y-1/2 rounded-[2px] border bg-[var(--point-color)] outline transition-[height,width,outline-color,box-shadow]",
        selected
          ? "h-4 w-4 border-pure-white outline-2 outline-ink-black shadow-sm"
          : "h-3 w-3 border-cream-paper outline-1 outline-hairline",
      )}
      onClick={() => onSelect(point)}
      onMouseEnter={() => onHover(point)}
      onMouseLeave={() => onHover(null)}
      onPointerDown={(event) => event.stopPropagation()}
      style={style}
      type="button"
    />
  );
}

function NotePanel({
  note,
  noteUnavailable,
  selectedPointId,
}: {
  note: NoteDetails | null;
  noteUnavailable: boolean;
  selectedPointId: number | null;
}) {
  if (selectedPointId === null) {
    return (
      <aside className={cx(notePanelFrameClass, "p-5")}>
        <p className={cx(chromeTextClass, "text-[10px] text-slate")}>SELECT A NOTE</p>
      </aside>
    );
  }

  if (noteUnavailable) {
    return (
      <aside
        aria-label={`NOTE ${selectedPointId} DETAILS`}
        className={cx(notePanelFrameClass, "p-5")}
        role="complementary"
      >
        <p className={cx(chromeTextClass, "text-[10px] text-slate")}>NOTE UNAVAILABLE</p>
      </aside>
    );
  }

  if (!note) {
    return (
      <aside
        aria-label={`NOTE ${selectedPointId} DETAILS`}
        className={cx(notePanelFrameClass, "p-5")}
        role="complementary"
      >
        <p className={cx(chromeTextClass, "text-[10px] text-slate")}>LOADING NOTE</p>
      </aside>
    );
  }

  return (
    <aside
      aria-label={`NOTE ${note.id} DETAILS`}
      className={cx(notePanelFrameClass, "grid content-start gap-4 p-5")}
      role="complementary"
    >
      <div className="flex items-center justify-between gap-3">
        <Tag>{note.mood.toUpperCase()}</Tag>
        <a className={cx(chromeTextClass, "text-[10px] text-schematic-blue")} href={`#seasons?date=${note.date}`}>
          {note.date}
        </a>
      </div>
      <p className={cx(readingTextClass, "whitespace-pre-wrap text-[15px] leading-7")}>{note.raw_text}</p>
      <div className="grid gap-2">
        <span className={cx(chromeTextClass, "text-[10px] text-slate")}>MOOD EVIDENCE</span>
        <p className={cx(readingTextClass, "text-sm leading-6 text-slate")}>{note.mood_evidence}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {note.topics.map((topic) => (
          <Tag key={topic}>{topic.toUpperCase()}</Tag>
        ))}
      </div>
      <div className={cx(chromeTextClass, "text-[10px] text-slate")}>
        CONFIDENCE <span className="text-ink-black">{note.mood_confidence.toFixed(2)}</span>
      </div>
    </aside>
  );
}

function pointColor(point: MapPoint, colorMode: ColorMode, selectedTopic: string): string {
  if (colorMode === "mood") {
    return moodPalette[point.mood];
  }
  if (colorMode === "topic") {
    if (selectedTopic) {
      return point.topics.includes(selectedTopic) ? topicColor(selectedTopic) : topicMutedColor;
    }
    return point.topics[0] ? topicColor(point.topics[0]) : topicMutedColor;
  }
  return clusterColor(point.cluster_id);
}

function clusterColor(clusterId: number): string {
  return clusterPalette[Math.abs(clusterId) % clusterPalette.length];
}

function moodLegendItems(points: MapPoint[]): LegendItem[] {
  const counts = new Map<Mood, number>();
  points.forEach((point) => {
    counts.set(point.mood, (counts.get(point.mood) ?? 0) + 1);
  });

  return moodOrder
    .filter((mood) => counts.has(mood))
    .map((mood) => ({
      id: mood,
      label: formatLegendLabel(mood),
      color: moodPalette[mood],
      count: counts.get(mood) ?? 0,
      testId: legendTestId("mood", mood),
    }));
}

function topicLegendItems(points: MapPoint[]): LegendItem[] {
  const counts = new Map<string, number>();
  points.forEach((point) => {
    new Set(point.topics.filter(Boolean)).forEach((topic) => {
      counts.set(topic, (counts.get(topic) ?? 0) + 1);
    });
  });

  return [...counts.entries()]
    .sort(([topicA, countA], [topicB, countB]) => countB - countA || topicA.localeCompare(topicB))
    .map(([topic, count]) => ({
      id: topic,
      label: formatLegendLabel(topic),
      color: topicColor(topic),
      count,
      testId: legendTestId("topic", topic),
    }));
}

function formatLegendLabel(value: string): string {
  return value.replace(/_/g, " ").toUpperCase();
}

function legendTestId(kind: "mood" | "topic", value: string): string {
  return `legend-swatch-${kind}-${value.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}`;
}

function clusterBox(points: MapPoint[]) {
  if (points.length === 0) {
    return { x: 8, y: 8, width: 16, height: 16 };
  }

  const xs = points.map((point) => point.x * 100);
  const ys = points.map((point) => (1 - point.y) * 100);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = 7;

  return {
    x: Math.max(2, minX - pad),
    y: Math.max(6, minY - pad),
    width: Math.max(14, maxX - minX + pad * 2),
    height: Math.max(14, maxY - minY + pad * 2),
  };
}

function emptyMapPayload(): MapPayload {
  return {
    signature: "empty",
    computed_at: "",
    points: [],
    clusters: [],
  };
}

function normalizeMapPayload(payload: MapPayload): MapPayload {
  if (!Array.isArray(payload.points) || !Array.isArray(payload.clusters)) {
    return emptyMapPayload();
  }

  const finitePoints = payload.points.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));

  return {
    ...payload,
    points: fitPointsToUnitViewport(finitePoints),
  };
}

function fitPointsToUnitViewport(points: MapPoint[]): MapPoint[] {
  if (points.length === 0) {
    return [];
  }

  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const xSpan = maxX - minX;
  const ySpan = maxY - minY;

  return points.map((point) => ({
    ...point,
    x: normalizeCoordinate(point.x, minX, xSpan),
    y: normalizeCoordinate(point.y, minY, ySpan),
  }));
}

function normalizeCoordinate(value: number, min: number, span: number): number {
  if (span === 0) {
    return 0.5;
  }

  return coordinatePadding + ((value - min) / span) * (1 - coordinatePadding * 2);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function formatPx(value: number): string {
  return `${Number(value.toFixed(2))}px`;
}
