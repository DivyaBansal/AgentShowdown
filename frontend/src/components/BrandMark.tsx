/** The agentshowdown mark: two gladii crossed, one per contender.
 *
 * Drawn in the first two chart series colours, so the logo and the charts
 * share one identity in both themes. The second sword carries a stroke in the
 * header's surface colour, painted *behind* its fill: it shows only where it
 * overlaps the first sword, cutting a clean gap at the cross, and never thins
 * the second sword itself.
 *
 * Decorative: the wordmark beside it already names the app, so the SVG is
 * hidden from assistive tech rather than exposed as a second role="img".
 */

/** One upright gladius on a 24-unit grid: blade, guard, grip, pommel. */
function Gladius() {
  return (
    <>
      <path d="M12 2.2 13.4 4.8V15h-2.8V4.8Z" />
      <rect x="8.4" y="15" width="7.2" height="1.9" rx="0.95" />
      <rect x="11" y="16.9" width="2" height="3.4" />
      <circle cx="12" cy="21.3" r="1.5" />
    </>
  );
}

export function BrandMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      className="brand__mark"
      width={size}
      height={size}
      // Cropped to the crossed swords, which span roughly 3..21 once rotated.
      viewBox="3 3 18 18"
      aria-hidden="true"
      focusable="false"
    >
      <g transform="rotate(-45 12 12)" fill="var(--series-1)">
        <Gladius />
      </g>
      <g
        transform="rotate(45 12 12)"
        fill="var(--series-2)"
        stroke="var(--surface)"
        strokeWidth="1.4"
        paintOrder="stroke"
      >
        <Gladius />
      </g>
    </svg>
  );
}
