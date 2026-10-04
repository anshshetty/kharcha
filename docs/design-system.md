# Kharcha design system

Kharcha uses a warm paper background, white surfaces, and forest teal for primary
actions and the main spending total. Sand highlights unavoidable costs. Semantic
status colors distinguish information, success, attention, and errors. Color
always accompanies a label or icon; amounts, category names, and percentages
remain readable without it.

## Where changes belong

| File                             | Responsibility                                                                                |
| -------------------------------- | --------------------------------------------------------------------------------------------- |
| `frontend/styles/tokens.css`     | Light/dark palettes, type scale, spacing, radii, control sizes, motion, and Tailwind aliases. |
| `frontend/styles/foundation.css` | Local font, base text, and accessible focus treatment.                                        |
| `frontend/styles/shell.css`      | Sidebar, header, page layout, and navigation.                                                 |
| `frontend/styles/components.css` | Reusable surfaces, metric cards, tables, forms, settings, and review panels.                  |
| `frontend/styles/spending.css`   | Spending dashboard, category breakdowns, merchant lists, and insights.                        |
| `frontend/styles/utilities.css`  | Shared interaction states, numeric alignment, and animation keyframes.                        |
| `frontend/styles/responsive.css` | Responsive layouts and reduced-motion behavior.                                               |
| `frontend/app/globals.css`       | Ordered imports only.                                                                         |

## Tokens before overrides

Use semantic names (`--card`, `--primary`, `--muted-foreground`,
`--warning-foreground`) instead of color literals. Both themes define the same
roles. New theme colors belong in `tokens.css`; do not add a `.dark` override to a
feature stylesheet. Tailwind utilities such as `bg-card`, `text-primary`, and
`border-border` use these same tokens.

Use `--space-1` through `--space-10` for the shared spacing scale,
`--radius-control` for controls and `--radius-surface` for panels. Common text
sizes use `--text-xs`, `--text-sm`, `--text-base`, `--text-title`, and
`--text-metric`. Reserve custom dimensions for layout needs, not new parallel
scales. Existing specialized forms retain their layout-specific dimensions.

Update the owning selector directly rather than appending a second override at
the end of a stylesheet. Responsive rules belong in `responsive.css`, loaded
last. The application uses the existing Tailwind and Base UI stack; no second
component library or remote font service is required.

## Shared components

- `Button`, `Input`, `Textarea`, and `Select` in `components/ui` define common
  controls. Button variants and sizes are authoritative; avoid global rules
  that force every button to the same height or padding. Default controls grow
  from 40px to 44px on mobile.
- `MetricCard` renders a label, icon, amount, and supporting detail. Use `brand`
  for the main total, `neutral` for supporting totals, and `warm` for a secondary
  cost distinction. Callers own currency formatting and accounting semantics.
- `SectionTitle`, `Picker`, and `Blank` in `components/common.tsx` provide shared
  section headings, selectors, and empty states.
- `categoryColor(name)` in `lib/design-system.ts` deterministically selects a
  chart token. The same category keeps its color across sorting and views.
  Category colors distinguish groups; they do not imply good or bad spending.
- `ThemeToggle` switches between light and dark. The preference is stored only
  in the browser as `kharcha-theme`; light is the default. A small head script
  restores the preference before paint. Storage failures leave the toggle usable.

## Visual checks

Use the disposable synthetic demo in [demo.md](demo.md), never a personal ledger.
Check overview, expanded categories, the full ledger, transaction filters and
details, review, and settings in both themes. Check narrow mobile and desktop
widths, long labels, keyboard focus, theme persistence, and reduced motion.

Run `npm run check --prefix frontend` for lint, types, existing regression tests,
and the production build. Visual changes do not change spending calculations,
API requests, or local-data boundaries.
