export type ViewTransform = {
  scale: number;
  x: number;
  y: number;
};

type ZoomOptions = {
  minZoom: number;
  maxZoom: number;
  intensity: number;
};

export function zoomAt(
  current: ViewTransform,
  cursorX: number,
  cursorY: number,
  deltaY: number,
  opts: ZoomOptions,
): ViewTransform {
  const nextScale = clamp(
    current.scale * Math.exp(-clamp(deltaY, -240, 240) * opts.intensity),
    opts.minZoom,
    opts.maxZoom,
  );
  const k = nextScale / current.scale;

  return {
    scale: nextScale,
    x: cursorX - (cursorX - current.x) * k,
    y: cursorY - (cursorY - current.y) * k,
  };
}

export function panBy(start: ViewTransform, dx: number, dy: number): ViewTransform {
  return {
    ...start,
    x: start.x + dx,
    y: start.y + dy,
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
