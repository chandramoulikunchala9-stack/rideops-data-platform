import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Card({ title, subtitle, actions, children, className = "" }: CardProps) {
  const headingId = title ? `card-${title.replace(/\W+/g, "-").toLowerCase()}` : undefined;
  return (
    <section className={`card ${className}`} aria-labelledby={headingId}>
      {(title || actions) && (
        <div className="card-header">
          <div>
            {title && <h2 id={headingId}>{title}</h2>}
            {subtitle && <p className="subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="toolbar">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export type KpiTone = "good" | "warning" | "critical";

interface KpiProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** Tints the tile when the number itself is a state (over budget, failing, healthy). */
  tone?: KpiTone;
}

/** Stat tile: a single headline number is not a chart. */
export function KpiCard({ label, value, hint, tone }: KpiProps) {
  return (
    <div className="card kpi" data-tone={tone}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

interface ReportSectionProps {
  id: string;
  title: string;
  description?: ReactNode;
  /** Where the numbers come from: tables, layers, freshness. */
  lineage?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

/** A page is a report: each section names what it answers and where its data comes from. */
export function ReportSection({ id, title, description, lineage, actions, children }: ReportSectionProps) {
  return (
    <section className="report-section" aria-labelledby={`${id}-title`}>
      <div className="section-head">
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        <div className="toolbar">
          {lineage && <span className="lineage">{lineage}</span>}
          {actions}
        </div>
      </div>
      {children}
    </section>
  );
}
