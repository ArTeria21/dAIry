import {
  area,
  curveBasis,
  stack,
  stackOffsetWiggle,
  stackOrderInsideOut,
  type SeriesPoint,
} from "d3-shape";
import { useMemo } from "react";

import { clusterPalette } from "../design/palettes";
import { chromeTextClass } from "../design/theme";
import type { TopicBucket } from "../services/insights";
import { cx } from "../ui/classNames";
import { Legend, type LegendItem } from "../ui/Legend";

const excludedFromTop = ["reflection"]; // Default journal topic; keep it from dominating the top-8 signal.
const otherKey = "OTHER";
const otherColor = "#dedede";
const inkColor = "#181818";
const streamWidth = 720;
const streamHeight = 190;
const streamTop = 12;
const streamBottom = 160;

type StreamDatum = {
  period: string;
  weekStart: string;
  counts: Record<string, number>;
  shares: Record<string, number>;
};

export type TopicStreamSeries = {
  key: string;
  color: string;
  clickable: boolean;
  total: number;
};

export type TopicStreamModel = {
  data: StreamDatum[];
  series: TopicStreamSeries[];
};

type TopicsStreamProps = {
  buckets: TopicBucket[];
  selectedTopic: string;
  onSelectTopic: (topic: string) => void;
};

export function TopicsStream({ buckets, onSelectTopic, selectedTopic }: TopicsStreamProps) {
  const model = useMemo(() => buildTopicStreamModel(buckets), [buckets]);

  if (model.series.length === 0) {
    return (
      <section className="grid gap-3">
        <div className={cx(chromeTextClass, "text-[10px] text-slate")}>TOPICS OVER TIME</div>
        <p className={cx(chromeTextClass, "text-[11px] text-slate")}>NO TOPIC SIGNAL</p>
      </section>
    );
  }

  const legendItems: LegendItem[] = model.series.map((series) => ({
    key: series.key,
    label: series.key.toUpperCase(),
    count: series.total,
    disabled: !series.clickable,
    swatch: series.color,
    swatchTestId: `topic-swatch-${series.key}`,
  }));

  return (
    <section className="grid gap-3">
      <div className={cx(chromeTextClass, "text-[10px] text-slate")}>TOPICS OVER TIME</div>
      <div className={cx(chromeTextClass, "text-[10px] text-slate")}>SHARE OF WEEKLY NOTES</div>
      {buckets.length < 3 ? (
        <p className={cx(chromeTextClass, "py-8 text-[11px] text-slate")}>NOT ENOUGH DATA</p>
      ) : (
        <StreamSvg model={model} onSelectTopic={onSelectTopic} selectedTopic={selectedTopic} />
      )}
      <Legend
        activeKey={selectedTopic || null}
        ariaLabel="TOPIC LEGEND"
        items={legendItems}
        onToggle={(key) => onSelectTopic(key)}
      />
    </section>
  );
}

function StreamSvg({
  model,
  onSelectTopic,
  selectedTopic,
}: {
  model: TopicStreamModel;
  selectedTopic: string;
  onSelectTopic: (topic: string) => void;
}) {
  const keys = model.series.map((series) => series.key);
  const stacked = stack<StreamDatum>()
    .keys(keys)
    .value((datum, key) => datum.shares[key] ?? 0)
    .offset(stackOffsetWiggle)
    .order(stackOrderInsideOut)(model.data);
  const extent = stackExtent(stacked);
  const path = area<SeriesPoint<StreamDatum>>()
    .x((_point, index) => xForIndex(index, model.data.length))
    .y0((point) => yScale(point[0], extent))
    .y1((point) => yScale(point[1], extent))
    .curve(curveBasis);

  return (
    <svg aria-label="TOPIC STREAM GRAPH" className="w-full" role="img" viewBox={`0 0 ${streamWidth} ${streamHeight}`}>
      {monthTicks(model.data).map((tick) => (
        <text
          className={chromeTextClass}
          fill="#858483"
          fontSize="9"
          key={`${tick.label}-${tick.x}`}
          x={tick.x}
          y="182"
        >
          {tick.label}
        </text>
      ))}
      {stacked.map((layer) => {
        const series = model.series.find((item) => item.key === layer.key);
        if (!series) {
          return null;
        }
        const selected = selectedTopic === series.key;
        const dimmed = selectedTopic !== "" && !selected;
        const pathData = path(layer) ?? "";

        return (
          <path
            aria-label={seriesTitle(series.key, model)}
            d={pathData}
            data-testid={`topic-stream-layer-${series.key}`}
            fill={series.color}
            fillOpacity={dimmed ? 0.28 : 0.82}
            key={series.key}
            onClick={() => {
              if (series.clickable) {
                onSelectTopic(series.key);
              }
            }}
            role={series.clickable ? "button" : undefined}
            stroke={selected ? inkColor : "transparent"}
            strokeWidth={selected ? 1.5 : 0}
            tabIndex={series.clickable ? 0 : undefined}
          >
            <title>{seriesTitle(series.key, model)}</title>
          </path>
        );
      })}
    </svg>
  );
}

export function buildTopicStreamModel(buckets: TopicBucket[]): TopicStreamModel {
  const totals = new Map<string, number>();
  buckets.forEach((bucket) => {
    Object.entries(bucket.counts).forEach(([topic, count]) => {
      totals.set(topic, (totals.get(topic) ?? 0) + count);
    });
  });

  const topTopics = [...totals.entries()]
    .filter(([topic]) => !excludedFromTop.includes(topic))
    .sort(([topicA, countA], [topicB, countB]) => countB - countA || topicA.localeCompare(topicB))
    .slice(0, 8)
    .map(([topic]) => topic);
  const topTopicSet = new Set(topTopics);
  const otherTotal = [...totals.entries()]
    .filter(([topic]) => !topTopicSet.has(topic))
    .reduce((sum, [, count]) => sum + count, 0);
  const series: TopicStreamSeries[] = topTopics.map((topic, index) => ({
    key: topic,
    color: clusterPalette[index % clusterPalette.length],
    clickable: true,
    total: totals.get(topic) ?? 0,
  }));
  if (otherTotal > 0) {
    series.push({
      key: otherKey,
      color: otherColor,
      clickable: false,
      total: otherTotal,
    });
  }

  return {
    data: buckets.map((bucket) => {
      const counts: Record<string, number> = {};
      const shares: Record<string, number> = {};
      topTopics.forEach((topic) => {
        counts[topic] = bucket.counts[topic] ?? 0;
      });
      if (otherTotal > 0) {
        counts[otherKey] = Object.entries(bucket.counts)
          .filter(([topic]) => !topTopicSet.has(topic))
          .reduce((sum, [, count]) => sum + count, 0);
      }
      series.forEach((item) => {
        shares[item.key] = bucket.total <= 0 ? 0 : (counts[item.key] ?? 0) / bucket.total;
      });
      return {
        period: bucket.period,
        weekStart: weekStartFromPeriod(bucket.period),
        counts,
        shares,
      };
    }),
    series,
  };
}

function stackExtent(stacked: ReturnType<ReturnType<typeof stack<StreamDatum>>>): [number, number] {
  let min = 0;
  let max = 0;
  stacked.forEach((layer) => {
    layer.forEach((point) => {
      min = Math.min(min, point[0], point[1]);
      max = Math.max(max, point[0], point[1]);
    });
  });
  return min === max ? [min - 1, max + 1] : [min, max];
}

function xForIndex(index: number, length: number): number {
  if (length <= 1) {
    return 0;
  }
  return (index / (length - 1)) * streamWidth;
}

function yScale(value: number, [min, max]: [number, number]): number {
  return streamBottom - ((value - min) / (max - min)) * (streamBottom - streamTop);
}

function monthTicks(data: StreamDatum[]): { label: string; x: number }[] {
  let previous = "";
  return data.flatMap((datum, index) => {
    const month = datum.weekStart.slice(5, 7);
    if (month === previous) {
      return [];
    }
    previous = month;
    return [{ label: monthLabel(month), x: xForIndex(index, data.length) }];
  });
}

function seriesTitle(topic: string, model: TopicStreamModel): string {
  const best = model.data.reduce(
    (current, datum) => {
      const count = datum.counts[topic] ?? 0;
      return count > current.count ? { count, datum } : current;
    },
    { count: 0, datum: model.data[0] },
  );
  const share = best.datum?.shares[topic] ?? 0;
  return `${topic.toUpperCase()} · WEEK OF ${best.datum?.weekStart ?? ""} · ${best.count} NOTES (${Math.round(share * 100)}%)`;
}

function weekStartFromPeriod(period: string): string {
  const match = /^(\d{4})-W(\d{2})$/.exec(period);
  if (!match) {
    return period;
  }
  const year = Number(match[1]);
  const week = Number(match[2]);
  const januaryFourth = new Date(Date.UTC(year, 0, 4));
  const mondayOffset = (januaryFourth.getUTCDay() + 6) % 7;
  const firstMonday = new Date(januaryFourth);
  firstMonday.setUTCDate(januaryFourth.getUTCDate() - mondayOffset);
  firstMonday.setUTCDate(firstMonday.getUTCDate() + (week - 1) * 7);
  return firstMonday.toISOString().slice(0, 10);
}

function monthLabel(month: string): string {
  return ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"][
    Number(month) - 1
  ];
}
