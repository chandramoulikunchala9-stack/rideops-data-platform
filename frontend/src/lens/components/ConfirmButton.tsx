import { useEffect, useRef, useState } from "react";

interface ConfirmButtonProps {
  /** The resting button, e.g. "Delete". */
  label: string;
  /** What the second click does, naming the object: "Delete shop-prod". */
  confirmLabel: string;
  /** One sentence on the consequence, shown beside the confirm button. */
  consequence?: string;
  onConfirm: () => void;
  small?: boolean;
  disabled?: boolean;
  /** Not destructive (dismiss, hide): the resting button stays neutral; only the confirm is toned. */
  quiet?: boolean;
}

/**
 * A destructive action in two steps, inline: the first click asks, the second acts.
 * Escape or Cancel steps back. No modal: the question appears where the hand already is.
 */
export function ConfirmButton({ label, confirmLabel, consequence, onConfirm, small, disabled, quiet }: ConfirmButtonProps) {
  const [asking, setAsking] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const size = small ? " small" : "";

  useEffect(() => {
    if (asking) confirmRef.current?.focus();
  }, [asking]);

  if (!asking) {
    return (
      <button type="button" className={`btn${quiet ? "" : " danger"}${size}`} disabled={disabled} onClick={() => setAsking(true)}>
        {label}
      </button>
    );
  }
  return (
    <span className="confirm-inline" role="group" aria-label={confirmLabel}
          onKeyDown={(e) => { if (e.key === "Escape") setAsking(false); }}>
      {consequence && <span className="confirm-text">{consequence}</span>}
      <button ref={confirmRef} type="button" className={`btn${quiet ? " primary" : " danger solid"}${size}`}
              onClick={() => { setAsking(false); onConfirm(); }}>
        {confirmLabel}
      </button>
      <button type="button" className={`btn${size}`} onClick={() => setAsking(false)}>Cancel</button>
    </span>
  );
}
