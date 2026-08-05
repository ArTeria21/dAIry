import type { CSSProperties } from "react";
import type { PointerEvent } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import {
  clusterColor,
  moodPalette,
  noiseColor,
  topicDimmedColor,
  topicPointColor,
  topicPointHighlightColor,
  topicMutedColor,
  type Mood,
} from "../design/palettes";
import { chromeTextClass, readingTextClass } from "../design/theme";
import { NoteEditor } from "../journal/NoteEditor";
import { panBy, zoomAt, type ViewTransform } from "./viewTransform";
import {
  fetchMap,
  fetchNoteDetails,
  SemanticIndexBuildingError,
  type MapCluster,
  type MapPayload,
  type MapPoint,
  type NoteDetails,
} from "../services/map";
import { Legend, type LegendItem } from "../ui/Legend";
import { Tag } from "../ui/primitives";
import { cx } from "../ui/classNames";

const schematicBlue = "#0d6ea5";
const mapSurfaceHeightClass = "h-[min(62vh,560px)] min-h-[440px]";
const notePanelFrameClass =
  "max-h-[min(62vh,560px)] overflow-y-auto overflow-x-hidden rounded-[2px] border border-hairline bg-cream-paper";

type ColorMode = "cluster" | "mood" | "topic";

type PointStyle = CSSProperties & {
  "--point-color": string;
};

type Highlight = { mode: ColorMode; key: string };

type LoadNoteOptions = {
  keepCurrent?: boolean;
};

type DragState = {
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startScale: number;
  startX: number;
  startY: number;
};

type SurfaceSize = {
  width: number;
  height: number;
};

const initialViewTransform: ViewTransform = { scale: 1, x: 0, y: 0 };
const coordinatePadding = 0.05;
const minZoom = 0.5;
const maxZoom = 6;
const zoomIntensity = 0.0008;
const baseGridSize = 96;
const labelEstimateWidthPx = 7;
const labelEstimateHeightPx = 14;
const labelEdgePaddingPx = 8;
const moodOrder: Mood[] = ["joy", "calm", "sadness", "anger", "fear", "neutral", "mixed"];
const allTopicsLegendKey = "__all_topics__";
const unclusteredLegendKey = "__unclustered__";

export function MapView() {
  const [payload, setPayload] = useState<MapPayload | null>(null);
  const [semanticIndexBuilding, setSemanticIndexBuilding] = useState(false);
  const [colorMode, setColorMode] = useState<ColorMode>("cluster");
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [hoveredPoint, setHoveredPoint] = useState<MapPoint | null>(null);
  const [selectedPointId, setSelectedPointId] = useState<string | null>(null);
  const [hiddenPointIds, setHiddenPointIds] = useState<Set<string>>(() => new Set());
  const [notePanelStatus, setNotePanelStatus] = useState("");
  const [note, setNote] = useState<NoteDetails | null>(null);
  const [noteReloading, setNoteReloading] = useState(false);
  const [noteUnavailable, setNoteUnavailable] = useState(false);
  const [viewTransform, setViewTransform] = useState<ViewTransform>(initialViewTransform);
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [mapSurfaceSize, setMapSurfaceSize] = useState<SurfaceSize>({ width: 0, height: 0 });
  const mapPanelRef = useRef<HTMLElement | null>(null);
  const noteLoadGenerationRef = useRef(0);

  useEffect(() => {
    let active = true;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    async function loadMap() {
      try {
        const nextPayload = await fetchMap();
        if (active) {
          setPayload(normalizeMapPayload(nextPayload));
          setSemanticIndexBuilding(false);
        }
      } catch (error) {
        if (!active) {
          return;
        }
        if (error instanceof SemanticIndexBuildingError) {
          setSemanticIndexBuilding(true);
          retryTimer = setTimeout(() => {
            retryTimer = null;
            void loadMap();
          }, 10_000);
        } else {
          setPayload(emptyMapPayload());
          setSemanticIndexBuilding(false);
        }
      }
    }

    void loadMap();
    return () => {
      active = false;
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
      }
    };
  }, []);

  useEffect(() => {
    const mapPanel = mapPanelRef.current;
    if (!mapPanel || !payload) {
      return;
    }
    const panel = mapPanel;

    function handleWheel(event: WheelEvent) {
      if (event.cancelable) {
        event.preventDefault();
      }
      event.stopPropagation();

      const rect = panel.getBoundingClientRect();
      const cursorX = event.clientX - rect.left;
      const cursorY = event.clientY - rect.top;

      setViewTransform((current) =>
        zoomAt(current, cursorX, cursorY, event.deltaY, {
          minZoom,
          maxZoom,
          intensity: zoomIntensity,
        }),
      );
    }

    panel.addEventListener("wheel", handleWheel, { passive: false });
    return () => panel.removeEventListener("wheel", handleWheel);
  }, [payload]);

  useLayoutEffect(() => {
    const mapPanel = mapPanelRef.current;
    if (!mapPanel || !payload) {
      return;
    }
    const panel = mapPanel;

    function measureMapSurface() {
      const rect = panel.getBoundingClientRect();
      const width = panel.clientWidth || rect.width;
      const height = panel.clientHeight || rect.height;
      if (width <= 0 || height <= 0) {
        return;
      }
      setMapSurfaceSize((current) =>
        current.width === width && current.height === height
          ? current
          : { width, height },
      );
    }

    measureMapSurface();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measureMapSurface);
    observer?.observe(panel);
    window.addEventListener("resize", measureMapSurface);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measureMapSurface);
    };
  }, [payload]);

  async function selectPoint(point: MapPoint) {
    setNotePanelStatus("");
    setSelectedPointId(point.id);
    await loadNote(point.id);
  }

  async function reloadSelectedNote() {
    if (selectedPointId !== null) {
      const reloadedNote = await loadNote(selectedPointId, { keepCurrent: true });
      return reloadedNote
        ? {
            rawText: reloadedNote.raw_text,
            rawTextSha256: reloadedNote.raw_text_sha256,
          }
        : null;
    }
    return null;
  }

  async function loadNote(id: string, options: LoadNoteOptions = {}): Promise<NoteDetails | null> {
    const generation = noteLoadGenerationRef.current + 1;
    noteLoadGenerationRef.current = generation;
    const keepCurrent = options.keepCurrent === true;
    if (keepCurrent) {
      setNoteReloading(true);
    } else {
      setNoteReloading(false);
      setNote(null);
    }
    setNoteUnavailable(false);
    try {
      const nextNote = await fetchNoteDetails(id);
      if (noteLoadGenerationRef.current === generation) {
        setNote(nextNote);
      }
      return nextNote;
    } catch {
      if (noteLoadGenerationRef.current === generation) {
        setNoteUnavailable(true);
      }
      if (keepCurrent) {
        throw new Error("NOTE RELOAD FAILED");
      }
      return null;
    } finally {
      if (noteLoadGenerationRef.current === generation) {
        setNoteReloading(false);
      }
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
      startScale: viewTransform.scale,
      startX: viewTransform.x,
      startY: viewTransform.y,
    });
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!dragState || dragState.pointerId !== event.pointerId || event.buttons !== 1) {
      return;
    }

    setViewTransform(() =>
      panBy(
        { scale: dragState.startScale, x: dragState.startX, y: dragState.startY },
        event.clientX - dragState.startClientX,
        event.clientY - dragState.startClientY,
      ),
    );
  }

  function handlePointerUp(event: PointerEvent<HTMLDivElement>) {
    if (dragState?.pointerId === event.pointerId) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      setDragState(null);
    }
  }

  if (semanticIndexBuilding) {
    return <p className={chromeTextClass}>SEMANTIC INDEX IS BEING BUILT</p>;
  }
  if (!payload) {
    return <p className={chromeTextClass}>LOADING MAP</p>;
  }
  const visiblePoints = payload.points.filter((point) => !hiddenPointIds.has(point.id));
  const highlightedCluster =
    colorMode === "cluster" && highlight?.mode === "cluster" && highlight.key !== unclusteredLegendKey
      ? (payload.clusters.find((cluster) => String(cluster.id) === highlight.key) ?? null)
      : null;

  return (
    <div className="grid gap-5">
      <MapControls
        colorMode={colorMode}
        onColorModeChange={(mode) => {
          setColorMode(mode);
          setHighlight(null);
        }}
        onResetView={() => setViewTransform(initialViewTransform)}
      />

      <div
        className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]"
        data-testid="map-layout"
      >
        <section
          aria-label="JOURNAL EMBEDDING MAP"
          className={cx(
            mapSurfaceHeightClass,
            "relative cursor-grab touch-none overflow-hidden overscroll-contain rounded-[2px] border border-hairline bg-cream-paper lg:col-start-1 lg:row-start-1",
          )}
          data-testid="map-panel"
          ref={mapPanelRef}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          {visiblePoints.length === 0 ? (
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
                {visiblePoints.map((point) => (
                  <PointButton
                    colorMode={colorMode}
                    highlight={highlight}
                    key={point.id}
                    onHover={setHoveredPoint}
                    onSelect={selectPoint}
                    point={point}
                    scale={viewTransform.scale}
                    selected={selectedPointId === point.id}
                  />
                ))}
              </div>
              {colorMode === "cluster" ? (
                <ClusterLayer
                  clusters={payload.clusters}
                  highlight={highlight}
                  points={visiblePoints}
                  surfaceSize={mapSurfaceSize}
                  viewTransform={viewTransform}
                />
              ) : null}
            </>
          )}

          <div className="pointer-events-none absolute left-4 top-4 grid max-w-[280px] gap-2">
            {hoveredPoint ? (
              <div
                className={cx(
                  readingTextClass,
                  "rounded-[2px] border border-hairline bg-cream-paper px-3 py-2 text-sm shadow-subtle",
                )}
                role="tooltip"
              >
                {hoveredPoint.gist}
              </div>
            ) : null}
            {highlightedCluster ? (
              <div
                className="grid gap-2 rounded-[2px] border border-hairline bg-cream-paper px-3 py-2 shadow-subtle"
                data-testid="cluster-summary"
              >
                <span className={cx(chromeTextClass, "text-[10px] text-ink-black")}>
                  {highlightedCluster.label.toUpperCase()} · {highlightedCluster.size} NOTES
                </span>
                {highlightedCluster.description ? (
                  <p className={cx(readingTextClass, "text-sm leading-6 text-slate")}>
                    {highlightedCluster.description}
                  </p>
                ) : null}
                {highlightedCluster.dominant_topics.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {highlightedCluster.dominant_topics.map((topic) => (
                      <Tag key={topic}>{topic.replace(/_/g, " ").toUpperCase()}</Tag>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>

        {visiblePoints.length > 0 ? (
          <div className="min-w-0 lg:col-span-2 lg:row-start-2" data-testid="map-legend-region">
            <MapLegend
              clusters={payload.clusters}
              colorMode={colorMode}
              highlight={highlight}
              nNoise={payload.n_noise}
              onHighlightToggle={toggleHighlight}
              points={visiblePoints}
            />
          </div>
        ) : null}

        <div className="min-w-0 lg:col-start-2 lg:row-start-1">
          <NotePanel
            note={note}
            noteUnavailable={noteUnavailable}
            noteReloading={noteReloading}
            noteStatus={notePanelStatus}
            onDeleted={(status) => {
              if (selectedPointId !== null) {
                setHiddenPointIds((current) => new Set([...current, selectedPointId]));
              }
              setSelectedPointId(null);
              setNote(null);
              setNoteUnavailable(false);
              setNotePanelStatus(status);
            }}
            onReload={reloadSelectedNote}
            selectedPointId={selectedPointId}
          />
        </div>
      </div>
    </div>
  );

  function toggleHighlight(mode: ColorMode, key: string) {
    if (mode === "topic" && key === allTopicsLegendKey) {
      setHighlight(null);
      return;
    }

    setHighlight((current) => (current?.mode === mode && current.key === key ? null : { mode, key }));
  }
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
  clusters,
  colorMode,
  highlight,
  nNoise,
  onHighlightToggle,
  points,
}: {
  clusters: MapCluster[];
  colorMode: ColorMode;
  highlight: Highlight | null;
  nNoise: number;
  onHighlightToggle: (mode: ColorMode, key: string) => void;
  points: MapPoint[];
}) {
  if (points.length === 0) {
    return null;
  }

  const items = legendItemsForMode(colorMode, points, clusters, nNoise);
  const activeKey =
    colorMode === "topic" && highlight?.mode !== "topic"
      ? allTopicsLegendKey
      : highlight?.mode === colorMode
        ? highlight.key
        : null;

  return (
    <Legend
      activeKey={activeKey}
      ariaLabel={`${colorMode.toUpperCase()} LEGEND`}
      gridColumns
      items={items}
      onToggle={(key) => onHighlightToggle(colorMode, key)}
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
  highlight,
  points,
  surfaceSize,
  viewTransform,
}: {
  clusters: MapCluster[];
  highlight: Highlight | null;
  points: MapPoint[];
  surfaceSize: SurfaceSize;
  viewTransform: ViewTransform;
}) {
  const activeClusterKey = highlight?.mode === "cluster" ? highlight.key : null;
  const labels = clusterLabels(clusters, points, activeClusterKey, viewTransform, surfaceSize);

  return (
    <svg aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
      {labels.map((label) => {
        const dimmed = activeClusterKey !== null && activeClusterKey !== String(label.cluster.id);
        return (
          <g key={label.cluster.id} opacity={dimmed ? 0.48 : 1}>
            <text
              className={cx(chromeTextClass, "font-plexmono")}
              data-testid={`cluster-label-${label.cluster.id}`}
              fill={dimmed ? "#858483" : schematicBlue}
              fontSize={11 * label.fontScale}
              paintOrder="stroke"
              stroke="#f5f4f1"
              strokeLinejoin="round"
              strokeWidth={3 * label.fontScale}
              style={{ textShadow: "0 0 3px #f5f4f1" }}
              textAnchor="middle"
              x={label.x}
              y={label.y}
            >
              {label.cluster.label.toUpperCase()}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

type ClusterLabel = {
  cluster: MapCluster;
  x: number;
  y: number;
  fontScale: number;
  bbox: LabelBox;
};

type LabelBox = {
  left: number;
  right: number;
  top: number;
  bottom: number;
};

function clusterLabels(
  clusters: MapCluster[],
  points: MapPoint[],
  activeClusterKey: string | null,
  viewTransform: ViewTransform,
  surfaceSize: SurfaceSize,
): ClusterLabel[] {
  const candidates = clusters
    .map((cluster) => clusterLabelCandidate(cluster, points, viewTransform, surfaceSize))
    .filter((label): label is ClusterLabel => label !== null)
    .sort((left, right) => {
      const leftActive = activeClusterKey === String(left.cluster.id) ? 1 : 0;
      const rightActive = activeClusterKey === String(right.cluster.id) ? 1 : 0;
      return rightActive - leftActive || right.cluster.size - left.cluster.size || left.cluster.id - right.cluster.id;
    });

  const placed: ClusterLabel[] = [];
  for (const candidate of candidates) {
    const active = activeClusterKey === String(candidate.cluster.id);
    if (active || placed.every((label) => !boxesIntersect(label.bbox, candidate.bbox))) {
      placed.push(candidate);
    }
  }
  return placed;
}

function clusterLabelCandidate(
  cluster: MapCluster,
  points: MapPoint[],
  viewTransform: ViewTransform,
  surfaceSize: SurfaceSize,
): ClusterLabel | null {
  const clusterPoints = points.filter((point) => point.cluster_id === cluster.id);
  if (clusterPoints.length === 0) {
    return null;
  }
  const rawX = median(clusterPoints.map((point) => point.x)) * 100;
  // Anchor the label above the topmost point so it does not sit on the dots.
  const rawY = Math.min(...clusterPoints.map((point) => (1 - point.y) * 100));
  const position = clampedClusterLabelPosition(
    rawX,
    rawY,
    cluster.label,
    viewTransform,
    surfaceSize,
  );
  if (!position) {
    return null;
  }

  return {
    cluster,
    x: position.x,
    y: position.y,
    fontScale: position.fontScale,
    bbox: {
      left: position.x - position.renderedWidth / 2,
      right: position.x + position.renderedWidth / 2,
      top: position.y - position.renderedHeight,
      bottom: position.y,
    },
  };
}

function clampedClusterLabelPosition(
  rawX: number,
  rawY: number,
  label: string,
  viewTransform: ViewTransform,
  surfaceSize: SurfaceSize,
): {
  x: number;
  y: number;
  fontScale: number;
  renderedWidth: number;
  renderedHeight: number;
} | null {
  if (surfaceSize.width <= 0 || surfaceSize.height <= 0) {
    return null;
  }

  const scale = Math.max(viewTransform.scale, 0.1);
  const estimatedWidth = Math.max(labelEstimateWidthPx, label.length * labelEstimateWidthPx);
  const availableWidth = Math.max(1, surfaceSize.width - labelEdgePaddingPx * 2);
  const fontScale = Math.min(1, availableWidth / estimatedWidth);
  const renderedWidth = estimatedWidth * fontScale;
  const renderedHeight = labelEstimateHeightPx * fontScale;
  const rawScreenX = (rawX / 100) * surfaceSize.width * scale + viewTransform.x;
  const rawScreenBaselineY =
    (rawY / 100) * surfaceSize.height * scale + viewTransform.y - labelEstimateHeightPx;
  const minScreenX = labelEdgePaddingPx + renderedWidth / 2;
  const maxScreenX = surfaceSize.width - labelEdgePaddingPx - renderedWidth / 2;
  const minScreenBaselineY = labelEdgePaddingPx + renderedHeight;
  const maxScreenBaselineY = surfaceSize.height - labelEdgePaddingPx;
  const screenX = clamp(rawScreenX, minScreenX, maxScreenX);
  const screenBaselineY = clamp(rawScreenBaselineY, minScreenBaselineY, maxScreenBaselineY);

  return {
    x: screenX,
    y: screenBaselineY,
    fontScale,
    renderedWidth,
    renderedHeight,
  };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function boxesIntersect(left: LabelBox, right: LabelBox): boolean {
  return left.left < right.right && left.right > right.left && left.top < right.bottom && left.bottom > right.top;
}

function PointButton({
  colorMode,
  highlight,
  onHover,
  onSelect,
  point,
  scale,
  selected,
}: {
  colorMode: ColorMode;
  highlight: Highlight | null;
  onHover: (point: MapPoint | null) => void;
  onSelect: (point: MapPoint) => void;
  point: MapPoint;
  scale: number;
  selected: boolean;
}) {
  const color = pointColor(point, colorMode, highlight);
  const style: PointStyle = {
    "--point-color": color,
    backgroundColor: "var(--point-color)",
    left: `${point.x * 100}%`,
    top: `${(1 - point.y) * 100}%`,
    // Counter the viewport zoom so points keep a constant on-screen size.
    transform: `translate(-50%, -50%) scale(${1 / Math.max(scale, 0.1)})`,
  };

  return (
    <button
      aria-label={`NOTE ${point.id}`}
      className={cx(
        "absolute rounded-[2px] border bg-[var(--point-color)] outline transition-[height,width,outline-color,box-shadow]",
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
  noteStatus,
  noteUnavailable,
  noteReloading,
  onDeleted,
  onReload,
  selectedPointId,
}: {
  note: NoteDetails | null;
  noteStatus: string;
  noteUnavailable: boolean;
  noteReloading: boolean;
  onDeleted: (status: string) => void;
  onReload: () => Promise<{ rawText: string; rawTextSha256: string } | null>;
  selectedPointId: string | null;
}) {
  if (selectedPointId === null) {
    return (
      <aside className={cx(notePanelFrameClass, "p-5")}>
        <p className={cx(chromeTextClass, "text-[10px] text-slate")}>{noteStatus || "SELECT A NOTE"}</p>
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

  const daySummary = note.day_summary?.trim();

  return (
    <aside
      aria-busy={noteReloading}
      aria-label={`NOTE ${note.id} DETAILS`}
      className={cx(notePanelFrameClass, "grid content-start gap-4 p-5")}
      role="complementary"
    >
      <div className="flex items-center justify-between gap-3">
        <Tag>{note.mood.toUpperCase()}</Tag>
        <a className={cx(chromeTextClass, "text-[10px] text-schematic-blue")} href={`#journal/${note.date}`}>
          {note.date} · OPEN DAY
        </a>
      </div>
      <p className={cx(readingTextClass, "whitespace-pre-wrap break-words text-[15px] leading-7")}>{note.raw_text}</p>
      <NoteEditor
        noteId={String(note.id)}
        onDeleted={onDeleted}
        onReload={onReload}
        rawText={note.raw_text}
        rawTextSha256={note.raw_text_sha256}
      />
      {daySummary ? (
        <div className="grid gap-2">
          <span className={cx(chromeTextClass, "text-[10px] text-slate")}>DAY SUMMARY</span>
          <p className={cx(readingTextClass, "text-sm leading-6 text-slate")}>{daySummary}</p>
        </div>
      ) : null}
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

function pointColor(point: MapPoint, colorMode: ColorMode, highlight: Highlight | null): string {
  if (colorMode === "mood") {
    const color = moodPalette[point.mood];
    const activeMood = activeHighlight(highlight, "mood");
    return !activeMood || activeMood.key === point.mood ? color : topicMutedColor;
  }

  if (colorMode === "topic") {
    const activeTopic = activeHighlight(highlight, "topic");
    return !activeTopic ? topicPointColor : point.topics.includes(activeTopic.key) ? topicPointHighlightColor : topicDimmedColor;
  }

  const activeCluster = activeHighlight(highlight, "cluster");
  if (point.cluster_id === -1) {
    return !activeCluster || activeCluster.key === unclusteredLegendKey ? noiseColor : topicMutedColor;
  }

  const color = clusterColor(point.cluster_id);
  return !activeCluster || clusterHighlightMatches(point, activeCluster.key) ? color : topicMutedColor;
}

function activeHighlight(highlight: Highlight | null, mode: ColorMode): Highlight | null {
  return highlight?.mode === mode ? highlight : null;
}

function clusterHighlightMatches(point: MapPoint, key: string): boolean {
  if (key === unclusteredLegendKey) {
    return point.cluster_id === -1;
  }

  return String(point.cluster_id) === key;
}

function legendItemsForMode(
  colorMode: ColorMode,
  points: MapPoint[],
  clusters: MapCluster[],
  nNoise: number,
): LegendItem[] {
  if (colorMode === "cluster") {
    return clusterLegendItems(clusters, nNoise);
  }

  if (colorMode === "mood") {
    return moodLegendItems(points);
  }

  return topicLegendItems(points);
}

function clusterLegendItems(clusters: MapCluster[], nNoise: number): LegendItem[] {
  const items = clusters.map((cluster) => ({
    key: String(cluster.id),
    label: formatLegendLabel(cluster.label),
    count: cluster.size,
    swatch: clusterColor(cluster.id),
    swatchTestId: legendTestId("cluster", String(cluster.id)),
  }));

  if (nNoise > 0) {
    items.push({
      key: unclusteredLegendKey,
      label: "UNCLUSTERED",
      count: nNoise,
      swatch: noiseColor,
      swatchTestId: legendTestId("cluster", "unclustered"),
    });
  }

  return items;
}

function moodLegendItems(points: MapPoint[]): LegendItem[] {
  const counts = new Map<Mood, number>();
  points.forEach((point) => {
    counts.set(point.mood, (counts.get(point.mood) ?? 0) + 1);
  });

  return moodOrder
    .filter((mood) => counts.has(mood))
    .map((mood) => ({
      key: mood,
      label: formatLegendLabel(mood),
      count: counts.get(mood) ?? 0,
      swatch: moodPalette[mood],
      swatchTestId: legendTestId("mood", mood),
    }));
}

function topicLegendItems(points: MapPoint[]): LegendItem[] {
  const counts = new Map<string, number>();
  points.forEach((point) => {
    new Set(point.topics.filter(Boolean)).forEach((topic) => {
      counts.set(topic, (counts.get(topic) ?? 0) + 1);
    });
  });

  const items = [...counts.entries()]
    .sort(([topicA, countA], [topicB, countB]) => countB - countA || topicA.localeCompare(topicB))
    .map(([topic, count]) => ({
      key: topic,
      label: formatLegendLabel(topic),
      count,
    }));

  return [{ key: allTopicsLegendKey, label: "ALL TOPICS" }, ...items];
}

function formatLegendLabel(value: string): string {
  return value.replace(/_/g, " ").toUpperCase();
}

function legendTestId(kind: "cluster" | "mood", value: string): string {
  return `legend-swatch-${kind}-${value.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}`;
}

function median(values: number[]): number {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) {
    return sorted[middle];
  }
  return (sorted[middle - 1] + sorted[middle]) / 2;
}

function emptyMapPayload(): MapPayload {
  return {
    signature: "empty",
    computed_at: "",
    n_noise: 0,
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
    n_noise: Number.isFinite(payload.n_noise) ? Math.max(0, payload.n_noise) : 0,
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

function formatPx(value: number): string {
  return `${Number(value.toFixed(2))}px`;
}
