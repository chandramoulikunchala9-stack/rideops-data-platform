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

interface KpiProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}

/** Stat tile: a single headline number is not a chart. */
export function KpiCard({ label, value, hint }: KpiProps) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}
