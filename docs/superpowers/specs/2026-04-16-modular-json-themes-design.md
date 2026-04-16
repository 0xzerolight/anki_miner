# Modular JSON Theme System

**Date:** 2026-04-16
**Status:** Approved
**Scope:** `anki_miner/gui/resources/styles/`

## Summary

Replace hardcoded Python color dicts and per-theme QSS files with auto-discovered JSON theme files. One JSON file = one theme. Drop file in `themes/` folder, done.

**Target audience:** Developers and AI agents authoring new themes.

## Theme File Format

Each theme is a JSON file in `anki_miner/gui/resources/styles/themes/`.

### Required Schema

All color tokens are required. Supports hex (`#RRGGBB`) and CSS color values (`rgba()`).
Grouped by role for readability — flat in JSON.

```json
{
  "name": "<string> — display name",
  "author": "<string> — theme author",
  "colors": {
    // --- Core ---
    "primary": "",
    "primary-hover": "",
    "primary-pressed": "",
    "primary-light": "",        // subtle primary bg (secondary btn hover, selected items)
    "primary-dark": "",         // darker primary (selected item text)
    "secondary": "",            // progress bar gradient end, accent
    "background": "",           // main window, scroll areas
    "surface": "",              // cards, inputs, dialogs, group boxes
    "surface-hover": "",        // ghost btn hover, tab hover, item hover
    "surface-alt": "",          // header bg, log-header, disabled input bg
    "text": "",                 // primary text
    "text-muted": "",           // placeholders, helper text, tab text, stat labels
    "text-disabled": "",        // disabled input text
    "text-on-primary": "",      // text on primary-colored elements
    "border": "",               // default borders
    "border-focus": "",         // focused input borders
    "border-subtle": "",        // checkbox borders, lighter borders
    "disabled": "",             // disabled button bg

    // --- Inputs ---
    "input-bg": "",             // input field background
    "input-disabled-bg": "",    // disabled input background

    // --- Status ---
    "error": "",
    "error-hover": "",          // danger button hover
    "success": "",
    "warning": "",
    "info": "",

    // --- UI Chrome ---
    "scrollbar": "",
    "scrollbar-hover": "",
    "tooltip-bg": "",
    "tooltip-text": "",
    "tooltip-border": "",
    "divider": "",              // separators, divider frames
    "update-banner-bg": "",
    "update-banner-text": "",
    "decorative": "",           // decorative elements (e.g. sakura section borders)

    // --- Status Badges (bg + text per status) ---
    "badge-success-bg": "",
    "badge-success-text": "",
    "badge-warning-bg": "",
    "badge-warning-text": "",
    "badge-error-bg": "",
    "badge-error-text": "",
    "badge-info-bg": "",
    "badge-info-text": "",
    "badge-pending-bg": "",
    "badge-pending-text": "",

    // --- Table Selection ---
    "table-selected-bg": "",
    "table-selected-text": ""
  }
}
```

45 color tokens. All required. No auto-derivation — full explicit control.
AI agents fill these easily; humans can copy an existing theme and tweak.

### Example: `light.json`

```json
{
  "name": "Light",
  "author": "anki_miner",
  "colors": {
    "primary": "#6366F1",
    "primary-hover": "#4F46E5",
    "primary-pressed": "#4338CA",
    "primary-light": "#EEF2FF",
    "primary-dark": "#4F46E5",
    "secondary": "#8B5CF6",
    "background": "#F9FAFB",
    "surface": "#FFFFFF",
    "surface-hover": "#F3F4F6",
    "surface-alt": "#F9FAFB",
    "text": "#111827",
    "text-muted": "#6B7280",
    "text-disabled": "#9CA3AF",
    "text-on-primary": "#FFFFFF",
    "border": "#E5E7EB",
    "border-focus": "#6366F1",
    "border-subtle": "#D1D5DB",
    "disabled": "#9CA3AF",
    "input-bg": "#FFFFFF",
    "input-disabled-bg": "#F3F4F6",
    "error": "#EF4444",
    "error-hover": "#DC2626",
    "success": "#10B981",
    "warning": "#F59E0B",
    "info": "#3B82F6",
    "scrollbar": "#D1D5DB",
    "scrollbar-hover": "#9CA3AF",
    "tooltip-bg": "#1F2937",
    "tooltip-text": "#F3F4F6",
    "tooltip-border": "#374151",
    "divider": "#E5E7EB",
    "update-banner-bg": "#4338CA",
    "update-banner-text": "#FFFFFF",
    "decorative": "#E5E7EB",
    "badge-success-bg": "#D1FAE5",
    "badge-success-text": "#065F46",
    "badge-warning-bg": "#FEF3C7",
    "badge-warning-text": "#92400E",
    "badge-error-bg": "#FEE2E2",
    "badge-error-text": "#991B1B",
    "badge-info-bg": "#DBEAFE",
    "badge-info-text": "#1E40AF",
    "badge-pending-bg": "#F3F4F6",
    "badge-pending-text": "#6B7280",
    "table-selected-bg": "#EEF2FF",
    "table-selected-text": "#4F46E5"
  }
}
```

## Architecture Changes

### New

- **`themes/` directory** — `anki_miner/gui/resources/styles/themes/` containing `*.json` theme files
- **JSON schema validation** — validate required keys on load, log warning and skip invalid files

### Modified

- **`common.qss`** — absorb all color rules from theme-specific QSS files as `${color-*}` placeholders (e.g., `${color-primary}`, `${color-background}`). Becomes the single source of all QSS rules.
- **`theme.py`** — remove `LIGHT_COLORS`, `DARK_COLORS`, `SAKURA_COLORS` class dicts. Add:
  - JSON file discovery (scan `themes/` directory)
  - JSON loading and validation
  - Color dict construction from JSON for variable substitution
  - Updated `cycle_theme()` to cycle through discovered themes
  - QPalette generation from JSON colors (same logic, different data source)
- **`_variables.py`** — unchanged (spacing/typography tokens stay as-is)

### Deleted

- `light_theme.qss`
- `dark_theme.qss`
- `sakura_theme.qss`

These files' color rules get merged into `common.qss` as `${color-*}` variables.

## Discovery & Loading

1. On startup, scan `themes/` directory for `*.json` files
2. For each file:
   - Parse JSON
   - Validate: `name` must be string, `colors` must be dict with all 45 required keys
   - Invalid → log warning with filename and reason, skip
3. Store discovered themes in ordered dict (sorted alphabetically by filename)
4. `cycle_theme()` cycles through discovered themes in order

## Theme Selection Persistence

- QSettings stores theme preference by `name` field value (same as current behavior)
- If saved theme name not found among discovered themes → fall back to first available theme
- If no themes discovered → fatal error (ship with at least `light.json`)

## Variable Substitution

Existing `_substitute_variables()` method extended:
- Current: replaces `${spacing-*}`, `${font-size-*}`, `${radius-*}` from `_variables.py`
- New: also replaces `${color-*}` from active theme JSON colors dict
- Single pass regex substitution over combined variable dict

## Migration Path

1. Extract colors from `LIGHT_COLORS` → `themes/light.json`
2. Extract colors from `DARK_COLORS` → `themes/dark.json`
3. Extract colors from `SAKURA_COLORS` → `themes/sakura.json`
4. Move color QSS rules from `light_theme.qss`, `dark_theme.qss`, `sakura_theme.qss` into `common.qss` using `${color-*}` placeholders
5. Delete theme-specific QSS files
6. Refactor `theme.py` to load from JSON instead of class dicts

## Testing

- Unit test: JSON validation accepts valid theme, rejects missing keys
- Unit test: variable substitution produces correct QSS output
- Unit test: theme discovery finds all JSON files in directory
- Unit test: fallback behavior when saved theme missing
- Manual: verify all 3 themes render correctly after migration
