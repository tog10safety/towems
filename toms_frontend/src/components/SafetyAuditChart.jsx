/**
 * Small, dependency-free charts for the Unit Safety Officer's MRM audit.
 *
 * Every chart type shows the same series: completed MRMs per week, month,
 * quarter or year. Bar/line use axes; pie/donut summarise the same buckets as
 * shares. Everything is inline SVG so it inherits the theme colours and prints
 * without a charting library.
 */

const PALETTE = [
  "#4f9cf9",
  "#3fb950",
  "#d29922",
  "#f85149",
  "#a371f7",
  "#39c5cf",
  "#db6d28",
  "#8b98a5",
];

const WIDTH = 720;
const HEIGHT = 300;
const MARGIN = { top: 18, right: 18, bottom: 66, left: 46 };

function polar(cx, cy, radius, angleDegrees) {
  const radians = ((angleDegrees - 90) * Math.PI) / 180;
  return [cx + radius * Math.cos(radians), cy + radius * Math.sin(radians)];
}

function arcPath(cx, cy, radius, start, end) {
  const [x1, y1] = polar(cx, cy, radius, start);
  const [x2, y2] = polar(cx, cy, radius, end);
  const large = end - start > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${large} 1 ${x2} ${y2} Z`;
}

function donutPath(cx, cy, radius, inner, start, end) {
  const [x1, y1] = polar(cx, cy, radius, start);
  const [x2, y2] = polar(cx, cy, radius, end);
  const [x3, y3] = polar(cx, cy, inner, end);
  const [x4, y4] = polar(cx, cy, inner, start);
  const large = end - start > 180 ? 1 : 0;
  return [
    `M ${x1} ${y1}`,
    `A ${radius} ${radius} 0 ${large} 1 ${x2} ${y2}`,
    `L ${x3} ${y3}`,
    `A ${inner} ${inner} 0 ${large} 0 ${x4} ${y4}`,
    "Z",
  ].join(" ");
}

function EmptyChart({ message }) {
  return <div className="state state-empty safety-chart-empty">{message}</div>;
}

function TimeChart({ type, buckets }) {
  if (!buckets.length) {
    return <EmptyChart message="No completed assessments in the covered period." />;
  }

  const plotWidth = WIDTH - MARGIN.left - MARGIN.right;
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const max = Math.max(1, ...buckets.map((row) => row.completed));
  const step = plotWidth / buckets.length;
  const x = (index) => MARGIN.left + step * (index + 0.5);
  const y = (value) => MARGIN.top + plotHeight - (value / max) * plotHeight;

  const tickCount = 4;
  const ticks = Array.from({ length: tickCount + 1 }, (_, index) =>
    Math.round((max / tickCount) * index)
  ).filter((value, index, all) => all.indexOf(value) === index);

  const labelEvery = Math.max(1, Math.ceil(buckets.length / 9));
  const rotateLabels = buckets.length > 7;
  const barWidth = Math.min(36, step * 0.62);

  return (
    <div className="safety-chart-wrap">
      <svg
        className="safety-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={
          type === "line"
            ? "Line chart of completed MRMs per period"
            : "Bar chart of completed MRMs per period"
        }
      >
        {ticks.map((value) => (
          <g key={`tick-${value}`}>
            <line
              className="safety-chart-grid"
              x1={MARGIN.left}
              x2={WIDTH - MARGIN.right}
              y1={y(value)}
              y2={y(value)}
            />
            <text
              className="safety-chart-axis"
              x={MARGIN.left - 10}
              y={y(value) + 4}
              textAnchor="end"
            >
              {value}
            </text>
          </g>
        ))}

        <text
          className="safety-chart-axis"
          x={MARGIN.left - 10}
          y={MARGIN.top - 4}
          textAnchor="end"
        >
          MRMs
        </text>

        {type === "line" ? (
          <>
            <polyline
              className="safety-chart-line"
              points={buckets
                .map((row, index) => `${x(index)},${y(row.completed)}`)
                .join(" ")}
            />
            {buckets.map((row, index) => (
              <circle
                key={row.key}
                className="safety-chart-point"
                cx={x(index)}
                cy={y(row.completed)}
                r={4}
              >
                <title>
                  {row.label}: {row.completed} completed
                </title>
              </circle>
            ))}
          </>
        ) : (
          buckets.map((row, index) => (
            <rect
              key={row.key}
              className="safety-chart-bar"
              x={x(index) - barWidth / 2}
              y={y(row.completed)}
              width={barWidth}
              height={Math.max(0, y(0) - y(row.completed))}
              rx={3}
            >
              <title>
                {row.label}: {row.completed} completed
              </title>
            </rect>
          ))
        )}

        <line
          className="safety-chart-axis-line"
          x1={MARGIN.left}
          x2={WIDTH - MARGIN.right}
          y1={y(0)}
          y2={y(0)}
        />

        {buckets.map((row, index) => {
          const show = index % labelEvery === 0 || index === buckets.length - 1;
          if (!show) return null;
          const labelY = MARGIN.top + plotHeight + 20;
          return (
            <text
              key={`label-${row.key}`}
              className="safety-chart-axis"
              x={x(index)}
              y={labelY}
              textAnchor={rotateLabels ? "end" : "middle"}
              transform={
                rotateLabels ? `rotate(-35 ${x(index)} ${labelY})` : undefined
              }
            >
              {row.label}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

/** Keep the legend readable: the largest periods plus one combined slice. */
function shareRows(buckets) {
  const rows = buckets
    .filter((row) => row.completed > 0)
    .map((row) => ({ key: row.key, label: row.label, value: row.completed }));
  if (rows.length <= 7) return rows;

  const ranked = [...rows].sort((a, b) => b.value - a.value);
  const keep = new Set(ranked.slice(0, 6).map((row) => row.key));
  const other = rows
    .filter((row) => !keep.has(row.key))
    .reduce((sum, row) => sum + row.value, 0);

  return [
    ...rows.filter((row) => keep.has(row.key)),
    { key: "other", label: "Other periods", value: other },
  ];
}

function ShareChart({ type, buckets }) {
  const rows = shareRows(buckets);
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  if (!total) {
    return <EmptyChart message="No completed assessments in the covered period." />;
  }

  const cx = 250;
  const cy = 150;
  const radius = 105;
  const inner = type === "donut" ? 58 : 0;
  let angle = 0;

  return (
    <div className="safety-chart-wrap">
      <svg
        className="safety-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`${type === "donut" ? "Donut" : "Pie"} chart of completed MRMs per period`}
      >
        {rows.length === 1 ? (
          <>
            <circle cx={cx} cy={cy} r={radius} fill={PALETTE[0]}>
              <title>
                {rows[0].label}: {rows[0].value} completed
              </title>
            </circle>
            {type === "donut" && (
              <circle cx={cx} cy={cy} r={inner} fill="var(--bg-elevated)" />
            )}
          </>
        ) : (
          rows.map((row, index) => {
            const start = angle;
            const end = angle + (row.value / total) * 360;
            angle = end;
            return (
              <path
                key={row.key}
                d={
                  type === "donut"
                    ? donutPath(cx, cy, radius, inner, start, end)
                    : arcPath(cx, cy, radius, start, end)
                }
                fill={PALETTE[index % PALETTE.length]}
                stroke="var(--bg-elevated)"
                strokeWidth={2}
              >
                <title>
                  {row.label}: {row.value} completed (
                  {Math.round((row.value / total) * 100)}%)
                </title>
              </path>
            );
          })
        )}

        {type === "donut" && (
          <>
            <text className="safety-chart-total" x={cx} y={cy + 2} textAnchor="middle">
              {total}
            </text>
            <text
              className="safety-chart-axis"
              x={cx}
              y={cy + 22}
              textAnchor="middle"
            >
              completed
            </text>
          </>
        )}

        <g transform="translate(410 42)">
          {rows.map((row, index) => (
            <g key={row.key} transform={`translate(0 ${index * 29})`}>
              <rect
                width={12}
                height={12}
                rx={2}
                fill={PALETTE[index % PALETTE.length]}
              />
              <text className="safety-chart-legend" x={20} y={11}>
                {row.label}
              </text>
              <text
                className="safety-chart-legend-value"
                x={190}
                y={11}
                textAnchor="end"
              >
                {row.value} ({Math.round((row.value / total) * 100)}%)
              </text>
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
}

export default function SafetyAuditChart({ type = "bar", buckets = [] }) {
  if (type === "pie" || type === "donut") {
    return <ShareChart type={type} buckets={buckets} />;
  }
  return <TimeChart type={type} buckets={buckets} />;
}
