# AKENO design system

AKENO's look is defined in one place, `akeno/ui/theme.py` (tokens and the application style sheet), with the
shared components in `akeno/ui/widgets.py` and the mouse-wheel rules in `akeno/ui/interaction.py`. Pages use
these instead of styling themselves.

## Colour

| Token | Value | Use |
|---|---|---|
| `BG` | `#080C15` | window background |
| `BG_SIDEBAR` | `#0A0F1A` | navigation rail, status bar |
| `SURFACE` | `#0F1522` | cards and panels |
| `SURFACE_2` | `#141C2B` | table headers, hovered rows, secondary buttons |
| `SURFACE_3` | `#1A2436` | pressed and selected neutral |
| `INPUT` | `#0B111C` | text fields, lists, tables (sunken) |
| `BORDER` / `BORDER_HI` / `BORDER_BTN` | `#1C2536` / `#263248` / `#323F58` | dividers / control borders / hovered borders |
| `TEXT` / `TEXT_DIM` / `TEXT_MUTED` | `#E8EDF6` / `#A3AEC2` / `#7C8AA2` | primary / secondary / metadata text |

On `SURFACE`, primary text has 15:1 contrast, secondary 8:1 and metadata 5:1, so every step meets WCAG AA.

- **The accent.** There is one accent: violet by default, or blue or cyan (Settings → General). It marks selection,
  focus, the primary action and links. White text on the primary fill has at least 4.5:1.
- **State colours.** They appear only where they carry a state: success `#34D399`, warning `#F6C152`, danger
  `#F7687C`, information `#4C9DFF`, live data `#3CC8F0`.
- **Card icons.** Section icons on cards are always drawn in the accent text colour. A colour on a header would be
  decoration.

## Type

The scale, in pt:

| Use | Size |
|---|---|
| Body (secondary and metadata use the dim and muted colours) | 10 |
| Metadata | 9 |
| Overline (sidebar sections, "CONTINUE PLAYING") | 8, bold, tracked |
| Subsection | 10.5, semibold |
| Card title | 12, semibold |
| Page title | 17, semibold |
| A game's name on its page | 19 |

The font is Segoe UI Variable / Segoe UI on Windows.

## Space and shape

- **Grid.** A 4 px grid.
- **Page margins.** 28 px at the sides and 22 px at the top. Ultrawide windows centre the content (2300 px at most).
- **Cards.** 20 px padding, with 16 px between cards.
- **Controls.** Related controls are 8 px apart.
- **Radii.** Cards 10 px, controls 7 px, pills fully round.
- **Depth.** Depth comes from the surface steps and 1 px borders. There are no glows or decorative gradients. The
  only artwork in the window is the faint AKENO strip behind the top of each page, which fades out before the
  content.

## Components

| Component | What it is |
|---|---|
| `PageHeader` | Page title, an optional description and the page's own actions. |
| `Card`, `SubCard`, `Divider` | Panels and the lines between groups inside them. |
| `form_row(name, control, help)` | A setting's name (and a short explanation) on the left, the control right-aligned at a width that suits it: numbers about 110–150 px (wide enough for the value and its unit), choices 200–340 px, text 240–460 px (`fit_control`). |
| `indented(widget)` | An option that depends on the one above it. |
| `StatusPill`, `CheckRow`, `status_badge` | State, with the colour on the dot or badge only. |
| `EmptyState` | What a list shows when it is empty: an icon, what is missing and the action that fills it. |
| `StatTile` | A counted or measured number. It shows "—" without a value and never an estimate. |
| `game_cover` | A game's artwork cropped to 16:9 with rounded corners, or the placeholder. |
| `ActionTile` | A large action; only the main action of a row is filled. |

Buttons have the kinds `primary` (one per area), default, `ghost` (quiet secondary), `danger` (removing or
resetting), `chip` (choices) and `link`.

## Mouse wheel and focus

`interaction.WheelGuard` is installed on the application:
- **Focus.** The wheel never gives a control keyboard focus.
- **Value controls.** Spin boxes, combo boxes, sliders and tab bars react to the wheel only when they have focus
  and the scroll starts on them.
- **Page scrolling.** A page scroll that passes over a focused control keeps scrolling the page until the wheel
  pauses.
- **Nested areas.** Lists and logs scroll themselves, then hand the wheel to the page at their end.

`tests/test_wheel_guard.py` checks every value control on every page.

## Scaling

- **Window size.** Pages are laid out for 100 % to 200 % display scaling. The window opens within the screen's
  available area, and its minimum size never exceeds it.
- **Narrow windows.** Cards sit side by side when they fit and stack when they do not (`ResponsiveRow`). Action
  rows wrap (`FlowLayout`). Long paths elide in the middle, with the full path in the tooltip.
- **Checks.** `python -m akeno --screenshot DIR --screenshot-size WxH --screenshot-scale F` renders every page at
  a given size and scale for checking.
