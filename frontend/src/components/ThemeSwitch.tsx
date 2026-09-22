/** System / light / dark, as three pressed-state buttons in the header.
 *
 * Buttons with aria-pressed rather than radios: each is a one-click action
 * that takes effect immediately, and the group reads as a toolbar control.
 */

import type { ThemeChoice } from "../lib/theme";

const OPTIONS: { value: ThemeChoice; label: string; icon: JSX.Element }[] = [
  {
    value: "system",
    label: "Match system theme",
    icon: (
      <>
        <rect x="3" y="4" width="18" height="12" rx="2" />
        <path d="M8 20h8M12 16v4" />
      </>
    ),
  },
  {
    value: "light",
    label: "Light theme",
    icon: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
      </>
    ),
  },
  {
    value: "dark",
    label: "Dark theme",
    icon: <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" />,
  },
];

export function ThemeSwitch({
  value,
  onChange,
}: {
  value: ThemeChoice;
  onChange: (choice: ThemeChoice) => void;
}) {
  return (
    <div className="theme-switch" role="group" aria-label="Theme">
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          aria-label={option.label}
          title={option.label}
          onClick={() => onChange(option.value)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            {option.icon}
          </svg>
        </button>
      ))}
    </div>
  );
}
