# Directum Design System — Quick Reference for Agents

This directory contains the Directum design system extracted from https://www.directum.ru/ui-kit,
for reuse in future tasks.

## Logo

```
Download from https://www.directum.ru/ui-kit → place in src/static/logo.svg (local, not CDN)
Height in header: 26px
Fallback: text "Directum" in var(--orange)
```

## Font

- **Primary**: Inter
- **Fallback**: 'Segoe UI', system-ui, sans-serif
- In Docker without internet Google Fonts is unavailable — use fallback `Segoe UI, system-ui`
- If Inter is needed locally — download and place in `src/static/fonts/`

## Color Palette

| Token          | Value     | Purpose                           |
|----------------|-----------|-----------------------------------|
| `--orange`     | `#FF7A00` | Primary brand, buttons, accent    |
| `--orange-btn` | `#FF8F35` | Button hover state                |
| `--orange-light`| `#FFF3E4`| Orange element backgrounds        |
| `--blue`       | `#3C65CC` | Links, secondary accent           |
| `--blue-light` | `#EEF2FF` | Blue element backgrounds          |
| `--navy`       | `#000E20` | Dark blue text/headings           |
| `--title`      | `#05184A` | Page headings                     |
| `--bg`         | `#F4F4F4` | Page background                   |
| `--surface`    | `#FFFFFF` | Card backgrounds                  |
| `--surface2`   | `#F8F8F8` | Secondary element backgrounds     |
| `--border`     | `#E0E0E0` | Borders                           |
| `--text`       | `#000E20` | Primary text                      |
| `--muted`      | `#625F6A` | Secondary text                    |
| `--subtle`     | `#9C9BA8` | Hints, labels                     |
| `--green`      | `#3AC436` | Success                           |
| `--red`        | `#D32F2F` | Error, interruption               |
| `--amber`      | `#F5A623` | Warning                           |

## Shadows

```css
--shadow-sm:    0 1px 3px rgba(1,12,28,.07);
--shadow-md:    0 4px 12px rgba(1,12,28,.08);
--shadow-hover: 0 8px 24px rgba(1,12,28,.10);
```

## Border Radius

```css
--radius:    10px;  /* cards, modals */
--radius-sm:  6px;  /* buttons, badges */
```

## Typography

```css
/* Headings */
font-size: 22px; font-weight: 700; color: var(--title);

/* Card subheadings */
font-size: 13px; font-weight: 700; color: var(--navy); text-transform: uppercase; letter-spacing: .06em;

/* Body text */
font-size: 14px; color: var(--text);

/* Small helper text */
font-size: 12px; color: var(--muted);
```

## Components

### Button (primary)
```css
.btn {
  background: var(--orange);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background .15s;
}
.btn:hover { background: var(--orange-btn); }
```

### Button (ghost)
```css
.btn-ghost {
  background: transparent;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 16px;
  font-size: 13px;
  cursor: pointer;
}
.btn-ghost:hover { border-color: var(--orange); color: var(--orange); }
```

### Card
```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  box-shadow: var(--shadow-sm);
  transition: box-shadow .2s;
}
.card:hover { box-shadow: var(--shadow-hover); }
```

### Navigation (sidebar)
```css
/* Active item */
.nav-item.active {
  background: var(--orange-light);
  color: var(--orange);
  border-left: 3px solid var(--orange);
  font-weight: 600;
}
/* Normal item */
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 16px 10px 20px;
  border-radius: 0 8px 8px 0;
  cursor: pointer;
  color: var(--muted);
  transition: all .15s;
}
.nav-item:hover { background: var(--bg); color: var(--text); }
```

### Badge
```css
.badge { padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge-green  { background: var(--green-light);  color: var(--green); }
.badge-red    { background: var(--red-light);    color: var(--red); }
.badge-amber  { background: var(--amber-light);  color: var(--amber); }
.badge-blue   { background: var(--blue-light);   color: var(--blue); }
.badge-muted  { background: var(--surface2);     color: var(--muted); }
```

### Sort Status Badge
```css
th.sort-asc::after  { content: ' ↑'; color: var(--orange); }
th.sort-desc::after { content: ' ↓'; color: var(--orange); }
```

### KPI Card
```css
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}
.kpi-label { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.kpi-value { font-size: 26px; font-weight: 700; color: var(--text); }
.kpi-sub   { font-size: 12px; color: var(--subtle); margin-top: 4px; }

/* Colored accents — top border stripe */
.kpi.accent-orange { border-top: 3px solid var(--orange); }
.kpi.accent-green  { border-top: 3px solid var(--green); }
.kpi.accent-red    { border-top: 3px solid var(--red); }
.kpi.accent-amber  { border-top: 3px solid var(--amber); }
```

## Charts (Chart.js Settings)

```js
Chart.defaults.color = '#625F6A';          // label color
Chart.defaults.borderColor = '#E0E0E0';    // grid color
Chart.defaults.font.family = "Inter, 'Segoe UI', system-ui, sans-serif";

// Primary palette
const COLORS = {
  orange: '#FF7A00', blue:   '#3C65CC', green:  '#3AC436',
  red:    '#D32F2F', amber:  '#F5A623', purple: '#7C3AED',
  ...
};
```

## Layout

```
Header: height 56px, fixed, white, shadow-sm
Sidebar: width 224px, white, right border
Content: margin-left 224px, padding 24px, background --bg
```

## Principles

1. **Light theme** — no dark mode, background #F4F4F4, cards white
2. **Orange is the main accent** (#FF7A00), blue is secondary (#3C65CC)
3. **Inter** — the only font
4. **Soft shadows** — not harsh, rgba with low opacity
5. **Rounded corners** — 10px cards, 6px small elements
6. **Hover effects** — translateY(-1px) + shadow intensification on cards
