/**
 * Small hand-built SVG charts (no charting library: keeps the bundle light for slow
 * connections). One series per chart, one axis, hover tooltip on every column,
 * values labelled selectively, and a table view for each chart.
 */
import { useMemo, useRef, useState, type ReactNode } from "react";

export function StatTile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value num">{value}</div>
      {sub !== undefined && <div className="sub">{sub}</div>}
    </div>
  );
}

function niceMax(v: number): { max: number; step: number } {
  if (v <= 0) return { max: 4, step: 1 };
  const raw = v / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  return { max: Math.ceil(v / step) * step, step };
}

const fmt = (n: number) => (Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 1 }));

export interface ColumnDatum {
  key: string;
  label: string; // axis label
  tip: string; // tooltip heading (e.g. full date)
  value: number;
}

export function ColumnChart({ title, unit, data, height = 180 }: { title: string; unit: string; data: ColumnDatum[]; height?: number }) {
  const [hover, setHover] = useState<{ d: ColumnDatum; x: number; y: number } | null>(null);
  const [asTable, setAsTable] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const W = 640;
  const pad = { l: 40, r: 8, t: 18, b: 24 };
  const { max, step } = useMemo(() => niceMax(Math.max(0, ...data.map((d) => d.value))), [data]);
  const innerW = W - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const slot = innerW / Math.max(1, data.length);
  const barW = Math.max(2, Math.min(28, slot - 2)); // 2px surface gap between columns
  const y = (v: number) => pad.t + innerH - (v / max) * innerH;
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const maxIdx = data.reduce((best, d, i) => (d.value > (data[best]?.value ?? -1) ? i : best), 0);
  const every = Math.ceil(data.length / 10); // thin out axis labels on long ranges

  return (
    <div className="card" ref={wrap}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h2>{title}</h2>
        <button className="secondary small" style={{ padding: "2px 8px" }} onClick={() => setAsTable((t) => !t)}>
          {asTable ? "Chart" : "Table"}
        </button>
      </div>
      {asTable ? (
        <table className="data">
          <thead><tr><th>Day</th><th className="right">{unit}</th></tr></thead>
          <tbody>{data.map((d) => <tr key={d.key}><td>{d.tip}</td><td className="right num">{fmt(d.value)}</td></tr>)}</tbody>
        </table>
      ) : (
        <svg viewBox={`0 0 ${W} ${height}`} width="100%" role="img" aria-label={`${title}, ${unit} per day`} onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
              <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--text-2)">{fmt(t)}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const cx = pad.l + slot * i + slot / 2;
            const h = Math.max(0, (d.value / max) * innerH);
            const top = pad.t + innerH - h;
            const r = Math.min(4, barW / 2, h);
            return (
              <g key={d.key}>
                {h > 0 && (
                  <path
                    d={`M${cx - barW / 2},${pad.t + innerH} V${top + r} Q${cx - barW / 2},${top} ${cx - barW / 2 + r},${top} H${cx + barW / 2 - r} Q${cx + barW / 2},${top} ${cx + barW / 2},${top + r} V${pad.t + innerH} Z`}
                    fill="var(--series-1)"
                    opacity={hover && hover.d.key !== d.key ? 0.55 : 1}
                  />
                )}
                {(i === maxIdx || i === data.length - 1) && d.value > 0 && (
                  <text x={cx} y={top - 4} textAnchor="middle" fontSize={11} fill="var(--text)">{fmt(d.value)}</text>
                )}
                {i % every === 0 && (
                  <text x={cx} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--text-2)">{d.label}</text>
                )}
                {/* hit target: the whole column slot, taller than the mark */}
                <rect
                  x={cx - slot / 2} y={pad.t} width={slot} height={innerH} fill="transparent"
                  onMouseMove={(e) => setHover({ d, x: e.clientX, y: e.clientY })}
                />
              </g>
            );
          })}
          <line x1={pad.l} x2={W - pad.r} y1={pad.t + innerH} y2={pad.t + innerH} stroke="var(--border)" />
        </svg>
      )}
      {hover && (
        <div className="tooltip" style={{ left: hover.x + 12, top: hover.y - 10 }}>
          <div style={{ fontWeight: 700 }}>{hover.d.tip}</div>
          <div>{fmt(hover.d.value)} {unit}</div>
        </div>
      )}
    </div>
  );
}
