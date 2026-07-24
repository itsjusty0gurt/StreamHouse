from __future__ import annotations


LAYOUT_MODE_AUTOMATIC = "automatic"
LAYOUT_MODE_LANDSCAPE = "landscape"
LAYOUT_MODE_PORTRAIT = "portrait"
LAYOUT_MODES = (
    LAYOUT_MODE_AUTOMATIC,
    LAYOUT_MODE_LANDSCAPE,
    LAYOUT_MODE_PORTRAIT,
)
AUTOMATIC_ORIENTATION_RATIO = 1.75
AUTOMATIC_ORIENTATION_TOLERANCE = 0.05


def normalize_layout_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in LAYOUT_MODES else LAYOUT_MODE_AUTOMATIC


def resolve_orientation(
    width: int,
    height: int,
    override: str = LAYOUT_MODE_AUTOMATIC,
    current: str = "",
) -> str:
    """Resolve orientation around the wide-layout breakpoint with hysteresis."""
    mode = normalize_layout_mode(override)
    if mode != LAYOUT_MODE_AUTOMATIC:
        return mode
    safe_height = max(height, 1)
    ratio = max(width, 1) / safe_height
    lower_bound = AUTOMATIC_ORIENTATION_RATIO * (
        1.0 - AUTOMATIC_ORIENTATION_TOLERANCE
    )
    upper_bound = AUTOMATIC_ORIENTATION_RATIO * (
        1.0 + AUTOMATIC_ORIENTATION_TOLERANCE
    )
    if current == LAYOUT_MODE_PORTRAIT and ratio <= upper_bound:
        return LAYOUT_MODE_PORTRAIT
    if current == LAYOUT_MODE_LANDSCAPE and ratio >= lower_bound:
        return LAYOUT_MODE_LANDSCAPE
    return (
        LAYOUT_MODE_PORTRAIT
        if ratio < AUTOMATIC_ORIENTATION_RATIO
        else LAYOUT_MODE_LANDSCAPE
    )
