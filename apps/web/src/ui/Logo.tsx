// Stratum's mark: three layers stacked and offset, as a poster is stacked (a poster is
// data: layers, not pixels). One colour (currentColor), so it is black on light, white
// on dark, and never competes with the client's poster.

export function LogoMark({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden
      focusable="false"
      className="logo-mark"
    >
      <rect x="3" y="3.5" width="18" height="4.75" rx="1.6" />
      <rect x="6" y="9.6" width="15" height="4.75" rx="1.6" opacity="0.62" />
      <rect x="3" y="15.75" width="12" height="4.75" rx="1.6" opacity="0.34" />
    </svg>
  );
}

/** The mark and the name, for the sign-in page and the posts list. */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <span className="logo" aria-label="Stratum">
      <LogoMark size={size} />
      <span className="logo-word" aria-hidden>
        Stratum
      </span>
    </span>
  );
}
