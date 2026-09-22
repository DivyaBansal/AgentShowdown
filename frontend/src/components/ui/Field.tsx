/** A labelled form control with an optional hint.
 *
 * The caller passes the same `id` to the control, which keeps the control
 * itself fully in the caller's hands (input, select, textarea, datalist)
 * while the label/hint layout lives in one place.
 */

import type { ReactNode } from "react";

export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}
