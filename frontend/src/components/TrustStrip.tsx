import { Link } from "react-router-dom";

import type { DataQuality, SourceApp, Sources } from "../api/types";
import { useApi } from "../api/useApi";
import { formatInt, relativeTime } from "../lib/format";
import { sourceColor, sourceList } from "../lib/sources";
import { Icon } from "./Icon";

interface TrustStripProps {
  /** The source applications whose data this page reports on. */
  apps: SourceApp["id"][];
}

/**
 * A report's provenance in one line: which applications feed it, how fresh their data is,
 * how its checks came out and how many rows were kept out. Sources -> Checks before Results.
 */
export function TrustStrip({ apps }: TrustStripProps) {
  const sources = useApi<Sources>("/sources");
  const quality = useApi<DataQuality>("/data-quality");

  const feeding = sourceList(sources.data).filter((s) => apps.includes(s.id));
  const lastFile = feeding.map((s) => s.last_loaded_at).filter(Boolean).sort().at(-1) ?? null;
  const quarantined = feeding.reduce((n, s) => n + s.rows_rejected, 0);
  const counts = quality.data?.status_counts;

  return (
    <section className="trust-strip" aria-label="Where this report's data comes from">
      <div className="trust-item">
        <span className="trust-label">Built from</span>
        <span className="trust-value">
          {feeding.length
            ? feeding.map((s) => (
                <span key={s.id} className="cell-key">
                  <span className="swatch" style={{ background: sourceColor(s.id) }} aria-hidden="true" />
                  {s.name}
                </span>
              ))
            : "–"}
        </span>
      </div>
      <div className="trust-item">
        <span className="trust-label">Last file</span>
        <span className="trust-value">{relativeTime(lastFile)}</span>
      </div>
      <div className="trust-item">
        <span className="trust-label">Checks</span>
        <span className="trust-value">
          {counts ? (
            <>
              <span className="type-count-item" data-tone="good">
                <Icon name="check" size={13} /> {counts.PASS} pass
              </span>
              <span className="type-count-item" data-tone="warning">
                <Icon name="warning" size={13} /> {counts.WARN} warn
              </span>
              <span className="type-count-item" data-tone={counts.FAIL ? "critical" : undefined}>
                <Icon name="x" size={13} /> {counts.FAIL} fail
              </span>
            </>
          ) : (
            "–"
          )}
        </span>
      </div>
      <div className="trust-item">
        <span className="trust-label">Quarantined</span>
        <span className="trust-value">{formatInt(quarantined)} rows</span>
      </div>
      <div className="trust-links">
        <Link className="section-link" to="/sources">
          Source apps <Icon name="arrowRight" size={14} />
        </Link>
        <Link className="section-link" to="/data-quality">
          Data quality <Icon name="arrowRight" size={14} />
        </Link>
      </div>
    </section>
  );
}
