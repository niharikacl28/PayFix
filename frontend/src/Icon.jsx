// Compact, self-contained inline-SVG icon set. Stroke-based to match the
// premium fintech aesthetic; all icons inherit currentColor.

export function Icon({ name, size = 16, strokeWidth = 1.6, ...rest }) {
  const props = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...rest,
  };

  switch (name) {
    case "dashboard":
      return (
        <svg {...props}>
          <rect x="3" y="3" width="7" height="9" rx="2" />
          <rect x="14" y="3" width="7" height="5" rx="2" />
          <rect x="14" y="12" width="7" height="9" rx="2" />
          <rect x="3" y="16" width="7" height="5" rx="2" />
        </svg>
      );
    case "queue":
      return (
        <svg {...props}>
          <path d="M4 6h16M4 12h16M4 18h10" />
        </svg>
      );
    case "ai":
      return (
        <svg {...props}>
          <path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6z" />
          <path d="M18 14l.7 1.9L21 17l-2.3 1.1L18 20l-.7-1.9L15 17l2.3-1.1z" />
        </svg>
      );
    case "shield":
      return (
        <svg {...props}>
          <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
          <path d="M9 12l2 2 4-4" />
        </svg>
      );
    case "trend":
      return (
        <svg {...props}>
          <path d="M3 17l6-6 4 4 8-8" />
          <path d="M14 7h7v7" />
        </svg>
      );
    case "check":
      return (
        <svg {...props}>
          <path d="M5 12l5 5 9-11" />
        </svg>
      );
    case "x":
      return (
        <svg {...props}>
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      );
    case "alert":
      return (
        <svg {...props}>
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
          <path d="M10.3 3.86l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3.14l-8-14a2 2 0 0 0-3.4 0z" />
        </svg>
      );
    case "detect":
      return (
        <svg {...props}>
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </svg>
      );
    case "diagnose":
      return (
        <svg {...props}>
          <path d="M12 3v3" />
          <path d="M9 6h6" />
          <rect x="5" y="9" width="14" height="11" rx="2" />
          <path d="M9 14h6" />
        </svg>
      );
    case "simulate":
      return (
        <svg {...props}>
          <path d="M3 12c4-6 14-6 18 0" />
          <path d="M3 12c4 6 14 6 18 0" />
          <circle cx="12" cy="12" r="2" />
        </svg>
      );
    case "optimize":
      return (
        <svg {...props}>
          <path d="M12 2v4" />
          <path d="M12 18v4" />
          <path d="M4.93 4.93l2.83 2.83" />
          <path d="M16.24 16.24l2.83 2.83" />
          <path d="M2 12h4" />
          <path d="M18 12h4" />
          <path d="M4.93 19.07l2.83-2.83" />
          <path d="M16.24 7.76l2.83-2.83" />
          <circle cx="12" cy="12" r="4" />
        </svg>
      );
    case "guardrails":
      return (
        <svg {...props}>
          <path d="M3 4h18" />
          <path d="M5 8v12" />
          <path d="M19 8v12" />
          <path d="M9 8v6" />
          <path d="M15 8v6" />
        </svg>
      );
    case "execute":
      return (
        <svg {...props}>
          <path d="M5 3l14 9-14 9z" />
        </svg>
      );
    case "audit":
      return (
        <svg {...props}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M9 14l2 2 4-4" />
        </svg>
      );
    case "arrow":
      return (
        <svg {...props}>
          <path d="M12 5v14" />
          <path d="M5 12l7 7 7-7" />
        </svg>
      );
    case "money":
      return (
        <svg {...props}>
          <rect x="2" y="6" width="20" height="12" rx="2" />
          <circle cx="12" cy="12" r="3" />
          <path d="M6 10h.01M18 14h.01" />
        </svg>
      );
    case "warning":
      return (
        <svg {...props}>
          <path d="M10.3 3.86l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3.14l-8-14a2 2 0 0 0-3.4 0z" />
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
        </svg>
      );
    case "human":
      return (
        <svg {...props}>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21a8 8 0 0 1 16 0" />
        </svg>
      );
    case "stop":
      return (
        <svg {...props}>
          <rect x="6" y="6" width="12" height="12" rx="1" />
        </svg>
      );
    case "block":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="9" />
          <path d="M6 6l12 12" />
        </svg>
      );
    case "back":
      return (
        <svg {...props}>
          <path d="M15 6l-6 6 6 6" />
        </svg>
      );
    default:
      return null;
  }
}
