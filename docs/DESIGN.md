# ORBIT — Design System

Product name: **ORBIT** (EV Charge Orchestrator). The orbit glyph from the reference pack is the mark:
every vehicle circles a depot, a charger, a tariff window. The name is short, ownable and reads well
in the condensed display type below.

## References taken from the attached material

| Reference | What we take | What we leave |
|---|---|---|
| Editorial timeline page (cream page, huge condensed uppercase title, chapter columns with a colour band) | Display typography, chapter-column layout for the landing page and report sections, thin rules with small caps labels | The red band (replaced with orange/sage), historical photos |
| endspeciesism.org (dark green header, orange "Support us" button, right-hand vertical progress timeline) | Dark green app chrome, orange primary button, vertical timeline nav for the charging schedule | Illustrated animals, the western landscape |
| Palette export (`motorq_k_colors`) | All seven colours, used exactly | — |
| Renault neon pack | Neon road track and highway as hero backgrounds (blurred, badges hidden), generic 32px UI icons | Product shots, diamond logo, RS badges, client logos |
| Agency glyph pack | Orbit (mark), converge (orchestrate), sun-box (tariff/off-peak), prism (battery), toggle-spark (live), arrow | Client logos, starburst "N" logo |
| Farm illustration pack | Crumpled paper texture, torn-edge dividers, orange brush stroke | Animals, trucks, barns |

## Colour tokens (from figma-colors.json)

| Token | Hex | Use |
|---|---|---|
| `--ink` | #1A1E1A | App background (dark), display text (light) |
| `--moss` | #627061 | Secondary surfaces, muted text, borders on dark |
| `--sage` | #A4C29F | Healthy / charged / on-plan; success; chart series 1 |
| `--ember` | #F78522 | Primary action, energy, alerts needing attention; chart series 2 |
| `--mist` | #E5E7EB | Light page background, body text on dark |
| `--paper` | #FFFFFF | Cards on light, headline text on dark |
| `--void` | #000000 | Hero backgrounds under neon imagery |

Semantic: `--ok` = sage, `--warn` = ember, `--danger` = #E24B4B (only colour added, for critical strand alerts), `--info` = mist at 70%.

Two themes. The **app** is dark by default (ink background, mist text, sage/ember accents) because a fleet
operations screen is watched for hours. The **landing / report** pages are light (mist background, ink text)
in the editorial style. Both are defined on `:root` and `[data-theme]`.

## Typography (Google Fonts)

- Display: **Anton** (stand-in for poster-cut-neue), uppercase, tight leading (0.9), letter-spacing -0.01em. Used for page titles, KPI numbers, chapter numbers.
- Body / UI: **Manrope** 400/500/700. Numbers use `font-variant-numeric: tabular-nums`.
- Mono (code, VINs, timestamps): **JetBrains Mono**.

Scale: 12 / 14 / 16 / 20 / 28 / 40 / 64 / 112 px. Display never below 40.

## Layout language

- **Chapter columns**: sections are numbered `CH.1 … CH.n` in small caps with a hairline above and content below, four columns on desktop, stacked on mobile.
- **Colour band**: one full-bleed band per page (ember on light pages, moss on dark) carrying the section's KPI numbers in Anton.
- **Vertical timeline**: the charging schedule and the live event feed both use the right-rail timeline from the reference: a 2px rail with dots, the active item in ember.
- **Torn edge**: transitions from hero to content use the torn-edge PNG as a mask, once per page, never between cards.
- **Paper texture**: 6% opacity over light pages only.
- **Neon hero**: landing page only, `neon-track.jpg` under a 60% ink overlay with the ORBIT wordmark.

## Components

Button (primary = ember fill, ink text; secondary = 1px moss outline; ghost), KPI tile (Anton number + small-caps label + sparkline), Vehicle chip (VIN mono + SoC bar), Alert row (severity dot, message, age, ack), Map (MapLibre, dark style, sage dots = vehicles OK, ember = at risk, white = charging), Schedule Gantt (15-min slots, off-peak slots hatched with sun-box glyph), Drawer (right, 420px), Toast, Table (keyset paginated), Empty state (orbit glyph, one line).

Motion: 160ms ease-out for hover, 320ms for drawer, live dots pulse at 2s. Respect `prefers-reduced-motion`.

Accessibility: 4.5:1 minimum for text (mist on ink = 12.9:1, ink on ember = 7.3:1, ink on sage = 8.6:1). Focus ring 2px ember offset 2px.

## Asset manifest (web/public/brand)

```
hero/neon-track.jpg, hero/neon-highway.jpg
textures/paper-crumple.png, textures/torn-edge-light.png, textures/torn-edge-dark.png
glyphs/orbit.svg (mark), converge.svg, sunbox.svg, prism.svg, toggle-spark.svg, brush-orange.svg, hand-rule.svg
icons/home car pin history calendar battery info menu pause play chevron-right chevron-up search filter close arrow-right (32px, currentColor)
```
Only these files are shipped. The raw reference packs in `/design` stay out of the repository.
