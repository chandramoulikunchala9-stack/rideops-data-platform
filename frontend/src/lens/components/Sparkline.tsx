/** Tiny inline latency trend. Gaps (failed checks) break the line instead of dropping to zero. */
export function Sparkline({ values, width = 96, height = 24, label }: {
  values: (number | null)[];
  width?: number;
  height?: number;
  label?: string;
}) {
  const nums = values.filter((v): v is number => v != null);
  if (nums.length < 2) return null; // a single point is not a trend
  const max = Math.max(...nums);
  const min = Math.min(...nums);
  const span = max - min || 1;
  const step = width / Math.max(1, values.length - 1);
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v == null) {
      pen = false;
      return;
    }
    const x = (i * step).toFixed(1);
    const y = (height - 2 - ((v - min) / span) * (height - 4)).toFixed(1);
    d += `${pen ? "L" : "M"}${x} ${y} `;
    pen = true;
  });
  return (
    <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`}
         role="img" aria-label={label ?? `Latency trend, last ${nums.at(-1)} ms`}>
      <path d={d.trim()} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}
