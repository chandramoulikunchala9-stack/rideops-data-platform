import { Icon } from "../../components/Icon";
import { STATE_META } from "../status";
import type { ConnState } from "../types";

export function StateBadge({ state }: { state: ConnState }) {
  const meta = STATE_META[state] ?? STATE_META.unknown;
  return (
    <span className="badge" data-tone={meta.tone} data-state={state}>
      <Icon name={meta.icon} size={13} />
      {meta.label}
    </span>
  );
}
