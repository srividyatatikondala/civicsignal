// Inline stroke icons (24x24, currentColor) — no icon package needed.
const PATHS = {
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  alert: <><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  conflict: <><path d="M7 4v16M17 4v16" /><path d="m4 8 3-4 3 4M14 16l3 4 3-4" /></>,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></>,
  landmark: <><path d="M3 21h18M4 10h16M12 3 3 8h18z" /><path d="M6 10v8M10 10v8M14 10v8M18 10v8" /></>,
  target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>,
  map: <><path d="m9 4-6 2v14l6-2 6 2 6-2V4l-6 2z" /><path d="M9 4v14M15 6v14" /></>,
  route: <><circle cx="6" cy="19" r="2" /><circle cx="18" cy="5" r="2" /><path d="M8 19h8a3 3 0 0 0 0-6H8a3 3 0 0 1 0-6h8" /></>,
  network: <><circle cx="12" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="19" r="2" /><path d="M12 7v5M12 12l-6 5M12 12l6 5" /></>,
  check: <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
  external: <><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>,
  chevron: <path d="m9 6 6 6-6 6" />,
  layers: <><path d="m12 3 9 5-9 5-9-5z" /><path d="m3 13 9 5 9-5" /></>,
  file: <><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z" /><path d="M14 3v5h5M8 13h8M8 17h5" /></>,
  sparkle: <><path d="M12 3v4M12 17v4M3 12h4M17 12h4" /><path d="m6 6 2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" /></>,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
  book: <><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M4 21V5M8 7h7" /></>,
  quote: <><path d="M7 7h4v6a4 4 0 0 1-4 4" /><path d="M14 7h4v6a4 4 0 0 1-4 4" /></>,
};

export default function Icon({ name, size = 18, className = "", title }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden={title ? undefined : "true"} role={title ? "img" : undefined}>
      {title && <title>{title}</title>}
      {PATHS[name]}
    </svg>
  );
}

export function Logo({ size = 40 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" className="logo-mark">
      <defs>
        <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5b8cff" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="44" height="44" rx="12" fill="url(#lg)" />
      <circle cx="21" cy="21" r="9" fill="none" stroke="#fff" strokeWidth="3" />
      <path d="m28 28 8 8" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" />
      <path d="M16 21h2.5l1.5-3.5 2.5 7 1.5-3.5H26" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Hero illustration: search results flowing into an evidence map, with signal rings.
export function HeroArt() {
  return (
    <svg viewBox="0 0 420 300" className="hero-art" role="img" aria-label="Search results analysed into an evidence map">
      <defs>
        <linearGradient id="hg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5b8cff" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
        <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#5b8cff" stopOpacity="0.35" />
          <stop offset="1" stopColor="#5b8cff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="270" cy="150" r="140" fill="url(#glow)" />
      {[46, 78, 110].map((r) => (
        <circle key={r} cx="270" cy="150" r={r} fill="none" stroke="#5b8cff" strokeOpacity="0.25" strokeDasharray="3 6" />
      ))}
      {/* result cards */}
      {[0, 1, 2, 3].map((i) => (
        <path key={`p${i}`} d={`M${162 + i * 6} ${62 + i * 56} C 200 ${62 + i * 56}, 205 150, 232 150`}
              fill="none" stroke="url(#hg)" strokeWidth="1.6" strokeOpacity="0.7" />
      ))}
      {[0, 1, 2, 3].map((i) => (
        <g key={i} transform={`translate(${12 + i * 6}, ${40 + i * 56})`}>
          <rect width="150" height="44" rx="8" className="hero-card" />
          <rect x="12" y="10" width="70" height="6" rx="3" fill="#5b8cff" opacity="0.9" />
          <rect x="12" y="22" width="118" height="4" rx="2" className="hero-line" />
          <rect x="12" y="30" width="90" height="4" rx="2" className="hero-line" />
          <circle cx="136" cy="13" r="5" fill={["#34d399", "#f59e0b", "#f87171", "#34d399"][i]} />
        </g>
      ))}
      {/* evidence map core */}
      <g transform="translate(232 112)">
        <rect width="76" height="76" rx="18" fill="url(#hg)" />
        <circle cx="34" cy="34" r="14" fill="none" stroke="#fff" strokeWidth="4" />
        <path d="m44 44 12 12" stroke="#fff" strokeWidth="5" strokeLinecap="round" />
      </g>
      {/* output nodes */}
      {[[372, 70, "#34d399"], [392, 150, "#f59e0b"], [372, 230, "#f87171"]].map(([x, y, c]) => (
        <g key={y}>
          <path d={`M308 150 Q 340 ${y} ${x - 12} ${y}`} fill="none" stroke={c} strokeWidth="1.6" strokeOpacity="0.8" />
          <circle cx={x} cy={y} r="12" fill={c} fillOpacity="0.18" stroke={c} />
          <circle cx={x} cy={y} r="4" fill={c} />
        </g>
      ))}
    </svg>
  );
}
