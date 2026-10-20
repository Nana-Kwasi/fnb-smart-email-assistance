// Small, dependency-free SVG charts. Series colours come from the validated --series-* tokens;
// every chart with more than one series has a legend, and values are shown on hover.
import { useId, useMemo, useRef, useState, type ReactNode } from "react";
import { formatDay } from "@/lib/format";

/** A single-series trend line for a KPI card. Decorative: the card states the number. */
export function Sparkline({ values, color = "var(--series-1)", width = 110, height = 44 }: {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  const id = useId();
  if (values.length < 2) return null;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const pts = values.map((v, i) => [
    (i / (values.length - 1)) * width,
    height - 4 - ((v - min) / span) * (height - 8),
  ]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden className="kpi-spark">
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.18" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${width},${height} L0,${height} Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="4" fill={color} stroke="#fff" strokeWidth="2" />
    </svg>
  );
}

export interface Series {
  key: string;
  label: string;
  color: string;
}

/** Lines over days, with a crosshair and a tooltip listing every series for the hovered day. */
export function TrendChart({ data, series, height = 240 }: {
  data: ({ date: string } & Record<string, number | string>)[];
  series: Series[];
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const width = 760;
  const pad = { top: 12, right: 16, bottom: 26, left: 34 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  const { max, ticks } = useMemo(() => {
    const raw = Math.max(1, ...data.flatMap((d) => series.map((s) => Number(d[s.key]) || 0)));
    const step = Math.max(1, Math.ceil(raw / 4 / (raw > 50 ? 10 : 1)) * (raw > 50 ? 10 : 1));
    const top = step * Math.ceil(raw / step);
    return { max: top, ticks: Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step) };
  }, [data, series]);

  const x = (i: number) => pad.left + (data.length < 2 ? innerW / 2 : (i / (data.length - 1)) * innerW);
  const y = (v: number) => pad.top + innerH - (v / max) * innerH;
  const labelEvery = Math.ceil(data.length / 7);

  function onMove(e: React.MouseEvent<SVGRectElement>) {
    const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const i = Math.round(((px - pad.left) / innerW) * (data.length - 1));
    setHover(Math.min(data.length - 1, Math.max(0, i)));
  }

  const hovered = hover !== null ? data[hover] : null;
  const tipLeft = hover !== null && box.current ? (x(hover) / width) * box.current.clientWidth : 0;
  const tipTop = hover !== null && box.current
    ? (Math.min(...series.map((s) => y(Number(data[hover][s.key]) || 0))) / height) * box.current.clientHeight
    : 0;

  return (
    <div className="chart" ref={box}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Emails per day">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
            <text x={pad.left - 8} y={y(t) + 4} textAnchor="end" className="chart-axis">
              {t.toLocaleString()}
            </text>
          </g>
        ))}
        {data.map((d, i) =>
          i % labelEvery === 0 || i === data.length - 1 ? (
            <text key={d.date} x={x(i)} y={height - 6} textAnchor="middle" className="chart-axis">
              {data.length > 10 ? new Date(`${d.date}T12:00`).getDate() : formatDay(d.date)}
            </text>
          ) : null,
        )}
        {series.map((s) => {
          const path = data
            .map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(Number(d[s.key]) || 0).toFixed(1)}`)
            .join(" ");
          return (
            <g key={s.key}>
              <path d={`${path} L${x(data.length - 1)},${y(0)} L${x(0)},${y(0)} Z`} fill={s.color} opacity="0.07" />
              <path d={path} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
            </g>
          );
        })}
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} stroke="var(--ink-faint)" strokeWidth="1" />
            {series.map((s) => (
              <circle key={s.key} cx={x(hover)} cy={y(Number(data[hover][s.key]) || 0)} r="4.5" fill={s.color} stroke="#fff" strokeWidth="2" />
            ))}
          </g>
        )}
        <rect
          x={pad.left}
          y={pad.top}
          width={innerW}
          height={innerH}
          fill="transparent"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        />
      </svg>
      {hovered && (
        <div className="chart-tooltip" style={{ left: tipLeft, top: tipTop }}>
          <div className="tt-date">
            {new Date(`${hovered.date}T12:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}
          </div>
          {series.map((s) => (
            <div className="tt-row" key={s.key}>
              <span className="swatch" style={{ background: s.color }} />
              {s.label}
              <strong>{Number(hovered[s.key]) || 0}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Legend({ series }: { series: Series[] }) {
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.key}>
          <i className="swatch" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

/** Horizontal bars, one hue, labels and values in text colours. */
export function BarList({ items, color = "var(--series-1)", onSelect, empty }: {
  items: { key: string; label: string; value: number }[];
  color?: string;
  onSelect?: (key: string) => void;
  empty?: ReactNode;
}) {
  if (items.length === 0) return <>{empty}</>;
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="bar-list">
      {items.map((item) => {
        const content = (
          <>
            <span className="bar-name" title={item.label}>{item.label}</span>
            <span className="bar-value">{item.value}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(item.value / max) * 100}%`, background: color, display: "block" }} />
            </span>
          </>
        );
        return onSelect ? (
          <button key={item.key} className="bar-item" onClick={() => onSelect(item.key)}>
            {content}
          </button>
        ) : (
          <div key={item.key} className="bar-item">
            {content}
          </div>
        );
      })}
    </div>
  );
}
