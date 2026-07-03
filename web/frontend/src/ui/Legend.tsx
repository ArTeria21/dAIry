import type { CSSProperties } from "react";

import { chromeTextClass } from "../design/theme";
import { cx } from "./classNames";

export type LegendItem = {
  key: string;
  label: string;
  count?: number;
  disabled?: boolean;
  swatch?: string;
  swatchTestId?: string;
};

type LegendProps = {
  items: LegendItem[];
  activeKey: string | null;
  ariaLabel: string;
  onToggle: (key: string) => void;
};

type SwatchStyle = CSSProperties & {
  "--legend-color": string;
};

export function Legend({ activeKey, ariaLabel, items, onToggle }: LegendProps) {
  return (
    <div aria-label={ariaLabel} className="grid h-[84px] content-start gap-2" role="group">
      <span className={cx(chromeTextClass, "text-[10px] text-slate")}>LEGEND</span>
      <div className="flex max-h-[68px] flex-wrap gap-2 overflow-y-auto pr-1">
        {items.map((item) => {
          const active = activeKey === item.key;

          return (
            <button
              aria-label={item.label}
              aria-pressed={active}
              className={cx(
                chromeTextClass,
                "inline-flex h-8 max-w-full items-center gap-2 rounded-[2px] border bg-cream-paper px-2 text-[10px] disabled:opacity-55",
                active ? "border-signal-orange text-ink-black" : "border-hairline text-slate",
              )}
              disabled={item.disabled}
              key={item.key}
              onClick={() => {
                if (!item.disabled) {
                  onToggle(item.key);
                }
              }}
              type="button"
            >
              {item.swatch ? <LegendSwatch color={item.swatch} testId={item.swatchTestId} /> : null}
              <span>{item.label}</span>
              {item.count === undefined ? null : (
                <span aria-hidden="true" className="text-slate">
                  {item.count}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function LegendSwatch({ color, testId }: { color: string; testId?: string }) {
  const style: SwatchStyle = {
    "--legend-color": color,
    backgroundColor: "var(--legend-color)",
  };

  return (
    <span
      aria-hidden="true"
      className="h-3 w-3 shrink-0 rounded-[2px] border border-hairline"
      data-testid={testId}
      style={style}
    />
  );
}
