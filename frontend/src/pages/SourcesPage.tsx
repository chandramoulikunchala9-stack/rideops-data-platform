import type { Sources } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card, ReportSection } from "../components/Card";
import { Icon } from "../components/Icon";
import { SourceFlow } from "../components/SourceFlow";
import { StatusBadge } from "../components/StatusBadge";
import { formatCompact, formatDateTime, formatInt, formatPct, humanize, relativeTime } from "../lib/format";
import { sourceColor, sourceIcon, sourceList } from "../lib/sources";

const bytes = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} kB`);

export function SourcesPage() {
  const state = useApi<Sources>("/sources");

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Source apps</h1>
          <p>Every application that sends data to RideOps: what arrived, what passed validation, and what was quarantined.</p>
        </div>
      </header>

      <AsyncView state={state} skeletonRows={6} isEmpty={(s) => sourceList(s).length === 0} empty="No source has delivered data yet. Run python -m rideops.db all.">
        {(body) => {
          const sources = sourceList(body);
          return (
            <div className="report">
              <ReportSection
                id="apps"
                title="Applications"
                description="One tile per application, with its delivery and validation status."
                lineage={
                  <>
                    <Icon name="layers" size={14} /> <code>ops.ingestion_log</code> · <code>core.rejected_records</code>
                  </>
                }
              >
                <div className="source-tiles">
                  {sources.map((s) => (
                    <article className="card source-tile" key={s.id} aria-labelledby={`src-${s.id}`}>
                      <div className="source-tile-head">
                        <span className="source-icon" style={{ color: sourceColor(s.id) }}>
                          <Icon name={sourceIcon(s.id)} size={20} />
                        </span>
                        <StatusBadge status={s.status} />
                      </div>
                      <h3 id={`src-${s.id}`}>{s.name}</h3>
                      <p className="source-desc">{s.description}</p>
                      <dl className="source-figures">
                        <div>
                          <dt>Rows received</dt>
                          <dd>{formatCompact(s.rows_loaded)}</dd>
                        </div>
                        <div>
                          <dt>Quarantined</dt>
                          <dd>{s.rows_rejected ? formatInt(s.rows_rejected) : "None"}</dd>
                        </div>
                      </dl>
                      <p className="source-fresh">
                        <Icon name="clock" size={13} /> Last file {relativeTime(s.last_loaded_at)}
                      </p>
                    </article>
                  ))}
                </div>
              </ReportSection>

              <ReportSection
                id="flow"
                title="Where the rows go"
                description="Band width follows the rows each application delivered; red dashes are rows sent to quarantine, grey dots are duplicate deliveries merged into one row."
              >
                <Card>
                  <div className="flow-wrap">
                    <SourceFlow sources={sources} />
                  </div>
                </Card>
              </ReportSection>

              <ReportSection
                id="entities"
                title="Delivered entities"
                description="The exact numbers behind the drawing, one row per entity."
              >
                <Card>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Application</th>
                          <th scope="col">Entity</th>
                          <th scope="col" className="num">Files</th>
                          <th scope="col" className="num">Rows received</th>
                          <th scope="col" className="num">Rows in tables</th>
                          <th scope="col" className="num">Quarantined</th>
                          <th scope="col" className="num">Duplicates</th>
                          <th scope="col" className="num">Reject rate</th>
                          <th scope="col" className="num">Size</th>
                          <th scope="col">Last loaded</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sources.flatMap((s) =>
                          s.entities.map((e) => (
                            <tr key={`${s.id}-${e.entity}`}>
                              <td>
                                <span className="cell-key">
                                  <span className="swatch" style={{ background: sourceColor(s.id) }} aria-hidden="true" />
                                  {s.name}
                                </span>
                              </td>
                              <td>{humanize(e.entity)}</td>
                              <td className="num">
                                {formatInt(e.files)}
                                {e.failed_files > 0 && <span className="text-critical"> ({e.failed_files} failed)</span>}
                              </td>
                              <td className="num">{formatInt(e.rows_loaded)}</td>
                              <td className="num">{formatInt(e.rows_current)}</td>
                              <td className="num">{formatInt(e.rows_rejected)}</td>
                              <td className="num">{formatInt(Math.max(0, e.rows_loaded - e.rows_current - e.rows_rejected))}</td>
                              <td className="num">{e.rows_loaded ? formatPct(e.rows_rejected / e.rows_loaded, 2) : "–"}</td>
                              <td className="num">{bytes(e.bytes)}</td>
                              <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(e.last_loaded_at)}</td>
                            </tr>
                          )),
                        )}
                      </tbody>
                    </table>
                  </div>
                  <p className="table-note">
                    Rows received = rows in tables + quarantined + duplicates.
                    Pipeline runs can hold more rows than were delivered: the platform also records its live runs there directly.
                  </p>
                </Card>
              </ReportSection>
            </div>
          );
        }}
      </AsyncView>
    </>
  );
}
