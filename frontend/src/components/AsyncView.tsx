import type { ReactNode } from "react";

import type { ApiState } from "../api/useApi";

interface AsyncViewProps<T> {
  state: ApiState<T>;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  skeletonRows?: number;
  children: (data: T) => ReactNode;
}

/** One consistent treatment of the four states: loading, error, empty, success. */
export function AsyncView<T>({ state, isEmpty, empty, skeletonRows = 3, children }: AsyncViewProps<T>) {
  if (state.loading && state.data === null) {
    return (
      <div role="status" aria-live="polite" aria-label="Loading">
        {Array.from({ length: skeletonRows }, (_, i) => (
          <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />
        ))}
      </div>
    );
  }
  if (state.error) {
    return (
      <div className="state error" role="alert">
        <strong>Could not load data.</strong> {state.error.message}{" "}
        <button type="button" className="btn small" onClick={state.reload}>
          Retry
        </button>
      </div>
    );
  }
  if (state.data === null) return null;
  if (isEmpty?.(state.data)) {
    return <div className="state">{empty ?? "No data for the selected filters."}</div>;
  }
  return <>{children(state.data)}</>;
}
