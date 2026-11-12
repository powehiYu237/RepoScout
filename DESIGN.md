# Design

## Visual Theme

A night-shift intelligence desk. Graphite surfaces, moss-green signal light,
amber warnings, and a warm-white ink. Dark is the default; a light theme is a
first-class alternative for daytime and printing. The surface stays near-neutral;
the brand lives in the green signal color and precise typography.

## Color Palette (OKLCH)

### Dark (default)

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `oklch(0.135 0.006 140)` | app background |
| `--surface` | `oklch(0.175 0.008 140)` | panels, table header |
| `--surface-2` | `oklch(0.215 0.010 140)` | hover, raised rows |
| `--line` | `oklch(0.30 0.012 140)` | hairline borders |
| `--ink` | `oklch(0.96 0.008 140)` | primary text |
| `--muted` | `oklch(0.74 0.014 140)` | secondary text (≥4.5:1) |
| `--faint` | `oklch(0.60 0.012 140)` | tertiary labels |
| `--primary` | `oklch(0.72 0.145 140)` | primary actions, scores |
| `--primary-soft` | `oklch(0.72 0.145 140 / 0.14)` | selected rows, gauges |
| `--accent` | `oklch(0.80 0.140 75)` | warnings, window estimate |
| `--danger` | `oklch(0.68 0.180 25)` | GPL, errors |
| `--info` | `oklch(0.76 0.090 230)` | neutral data accents |

### Light

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `oklch(0.985 0.002 140)` | app background |
| `--surface` | `oklch(1 0 0)` | panels |
| `--surface-2` | `oklch(0.955 0.006 140)` | hover |
| `--line` | `oklch(0.89 0.008 140)` | borders |
| `--ink` | `oklch(0.22 0.015 140)` | primary text |
| `--muted` | `oklch(0.42 0.015 140)` | secondary text |
| `--faint` | `oklch(0.52 0.012 140)` | tertiary labels |
| `--primary` | `oklch(0.46 0.115 140)` | primary actions |
| `--primary-soft` | `oklch(0.46 0.115 140 / 0.10)` | selected rows |
| `--accent` | `oklch(0.58 0.130 75)` | warnings |
| `--danger` | `oklch(0.52 0.180 25)` | GPL, errors |

License tokens (both themes): green `--primary`, yellow `--accent`, red
`--danger`, gray `--faint`, unknown `--muted`. Always pair with text.

## Typography

- UI: `"Inter", "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif`
- Data/code: `"JetBrains Mono", "SFMono-Regular", Consolas, monospace`
- Scale: 12px labels, 13px table, 14–16px body, 20–28px section titles,
  clamp(28px, 4vw, 44px) page title. Display tracking ≥ -0.03em.
- Numbers use `font-variant-numeric: tabular-nums`.

## Layout

- Sticky top command bar: brand, snapshot status, theme toggle, refresh.
- Desktop: 264px filter rail + flexible table. Mobile: filter rail becomes a
  modal `<dialog>` triggered by a "筛选" button.
- List page: summary strip (candidate pool / visible / hidden gates), preset
  tabs, then table. Top 10 "今日雷达" appears only when no filters are active.
- Detail page: back link, repo header, score summary, then a two-column grid:
  growth chart + reproducibility report; commercial evidence + risk/actions.
- Table rows 52–60px; sticky header; zebra-free, hairline separators.

## Components

- **Score pill**: 0–100, monospaced, primary soft background; sub-scores in a
  compact stacked bar.
- **License badge**: dot + SPDX text + risk word.
- **Risk chip**: outlined, text label, color only as secondary signal.
- **Sparkline**: 2px line, no fill, last point marked; axis hidden.
- **Growth chart**: SVG line for 30-week cumulative stars; bar chart for daily
  deltas; shared tooltip with keyboard focus.
- **Filter controls**: native select/input/range, 36px height, visible focus.
- **Empty state**: specific cause + one actionable next step.
- **Toast**: bottom-right, 3s, reduced-motion aware.

## Motion

160–220ms ease-out for hover, selection, and view transitions. Detail view enters
with a 180ms opacity + 6px translate. Honor `prefers-reduced-motion: reduce` with
instant or crossfade alternatives. No scroll-triggered reveals.

## Data Visualization

Use the signal palette, not rainbow scales. Green = positive growth, amber =
caution, red = risk, blue-gray = neutral. Grid lines at 12% opacity; axis labels
in `--faint`; tooltips show exact values and source. Never encode license or risk
with color alone.

## Anti-patterns

- No gradient text, glass panels, or identical metric-card grids.
- No side-stripe accent borders. Use full borders, tints, or leading labels.
- No pure black + neon green terminal styling.
- No cards inside cards. Use section bands and hairlines.
