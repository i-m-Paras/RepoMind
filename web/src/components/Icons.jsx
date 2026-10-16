// Small inline icon set (stroke icons, 24px grid) — no icon library needed.
const PATHS = {
  chat: "M4 5h16v11H8l-4 4z",
  files: "M4 4h6l2 2h8v13H4z",
  index: "M4 6h16M4 12h16M4 18h10",
  plans: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01",
  skills: "M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6-4.5-4.2 6.1-.7z",
  plus: "M12 5v14M5 12h14",
  x: "M6 6l12 12M18 6L6 18",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  send: "M4 12l16-8-6 16-2-7z",
  stop: "M7 7h10v10H7z",
  alert: "M12 8v5M12 16.5v.01M10.3 4l-8 14a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  tool: "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z",
  chevron: "M9 6l6 6-6 6",
  folder: "M3 6h6l2 2h10v11H3z",
  file: "M6 3h8l4 4v14H6zM14 3v4h4",
  upload: "M12 16V4M7 9l5-5 5 5M4 16v4h16v-4",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16",
  save: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  search: "M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zM20 20l-4-4",
  menu: "M4 7h16M4 12h16M4 17h16",
  play: "M7 5l12 7-12 7z",
  git: "M6 3v12M18 9a3 3 0 1 0 0-.01M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 12c0 4-6 3-12 6",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  sparkle: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
};

export function Icon({ name, size = 16, className = "" }) {
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] || ""} />
    </svg>
  );
}
