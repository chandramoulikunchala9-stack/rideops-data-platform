import { formatInt } from "../../lib/format";

/**
 * One value in a query or preview grid. Numbers are right-aligned tabular figures, nulls are
 * muted, objects (JSON, arrays) stay in mono; text is set in the UI face.
 */
export function DataCell({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <td><span className="muted">null</span></td>;
  if (typeof value === "number") {
    return <td className="num">{Number.isInteger(value) ? formatInt(value) : value.toLocaleString("en-GB", { maximumFractionDigits: 6 })}</td>;
  }
  if (typeof value === "object") {
    const text = JSON.stringify(value);
    return <td className="mono" title={text}>{text}</td>;
  }
  const text = String(value);
  return <td title={text.length > 40 ? text : undefined}>{text}</td>;
}
