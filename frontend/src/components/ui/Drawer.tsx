/** A panel that slides in from the right edge, beside the page.
 *
 * Non-modal on purpose: the page behind stays usable, so you can open
 * another contender, sort the runs table or reply to a job without closing
 * this first. On wide screens the page makes room for it (see
 * navigation.css); on narrow ones it covers the page.
 *
 * Content is kept on screen for the length of the exit animation, so
 * closing slides out instead of vanishing. Focus moves into the panel when
 * it opens and goes back where it came from when it closes.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

/** Matches the slide-out animation in motion.css. */
export const EXIT_MS = 200;

function exitDelay(): number {
  // Animation is off globally under reduced motion (base.css), so there is
  // nothing to wait for. jsdom has no matchMedia at all.
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : EXIT_MS;
  } catch {
    return EXIT_MS;
  }
}

export function Drawer({
  open,
  label,
  onClose,
  children,
}: {
  open: boolean;
  /** Accessible name for the panel. */
  label: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const [leaving, setLeaving] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  // While sliding out the caller may already have nothing to show, so the
  // last open content is what slides away.
  const [lastChildren, setLastChildren] = useState<ReactNode>(children);
  const panel = useRef<HTMLElement>(null);

  // Adjusting state to a prop change during render, as React recommends
  // over an effect: it settles before anything is painted.
  if (open !== wasOpen) {
    setWasOpen(open);
    setLeaving(!open);
  }

  if (open && children !== lastChildren) setLastChildren(children);

  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => setLeaving(false), exitDelay());

    return () => window.clearTimeout(timer);
  }, [leaving]);

  // Focus in on open, back out on close.
  useEffect(() => {
    if (!open) return;
    const returnTo = document.activeElement;

    panel.current?.focus();

    return () => {
      if (returnTo instanceof HTMLElement && returnTo.isConnected) returnTo.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !e.defaultPrevented) onClose();
    }

    document.addEventListener("keydown", handleKey);

    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  if (!open && !leaving) return null;

  return (
    <aside
      ref={panel}
      role="dialog"
      aria-modal="false"
      aria-label={label}
      tabIndex={-1}
      className={open ? "drawer" : "drawer drawer--leaving"}
    >
      {open ? children : lastChildren}
    </aside>
  );
}
