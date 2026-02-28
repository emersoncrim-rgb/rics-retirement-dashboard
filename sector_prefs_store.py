"""
sector_prefs_store.py — Adapter for reading, validating, and saving sector preferences.
"""

import profile_store


def load_sector_preferences(merged_profile: dict) -> dict:
    """Load sector preferences from the merged profile, providing defaults if missing."""
    prefs = merged_profile.get("sector_preferences", {}) or {}
    return {
        "liked_sectors": prefs.get("liked_sectors", []) or [],
        "avoided_sectors": prefs.get("avoided_sectors", []) or [],
        "tilt_strength": prefs.get("tilt_strength", 0),
    }


def validate_sector_preferences(prefs: dict) -> tuple[dict, list[str]]:
    """
    Validate sector preferences.
    Trims whitespace, drops empties, dedupes case-insensitively, enforces max length,
    checks for overlaps, and clamps tilt_strength.
    """
    errors: list[str] = []
    clean_prefs = {"liked_sectors": [], "avoided_sectors": [], "tilt_strength": 0}

    def clean_list(items):
        if not isinstance(items, list):
            return []
        result = []
        seen = set()
        for item in items:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if not stripped:
                continue
            lower_item = stripped.lower()
            if lower_item not in seen:
                seen.add(lower_item)
                result.append(stripped)
        return result

    liked = clean_list((prefs or {}).get("liked_sectors", []))
    avoided = clean_list((prefs or {}).get("avoided_sectors", []))

    if len(liked) > 20:
        errors.append("Maximum 20 liked sectors allowed.")
        liked = liked[:20]

    if len(avoided) > 20:
        errors.append("Maximum 20 avoided sectors allowed.")
        avoided = avoided[:20]

    liked_lower = {x.lower() for x in liked}
    avoided_lower = {x.lower() for x in avoided}
    overlap = liked_lower.intersection(avoided_lower)
    if overlap:
        errors.append(
            f"Overlap detected between liked and avoided sectors: {', '.join(sorted(overlap))}."
        )

    clean_prefs["liked_sectors"] = liked
    clean_prefs["avoided_sectors"] = avoided

    tilt = (prefs or {}).get("tilt_strength", 0)
    try:
        tilt = int(tilt)
        if tilt < 0:
            tilt = 0
        elif tilt > 5:
            tilt = 5
    except (ValueError, TypeError):
        errors.append("tilt_strength must be an integer between 0 and 5.")
        tilt = 0

    clean_prefs["tilt_strength"] = tilt

    return clean_prefs, errors


def save_sector_preferences(
    prefs: dict, tax_path: str | None = None, const_path: str | None = None
) -> None:
    """Validate and save sector preferences via the profile patch mechanism."""
    clean_prefs, errors = validate_sector_preferences(prefs)
    if errors:
        raise ValueError(" ".join(errors))

    patch = {"sector_preferences": clean_prefs}
    _, save_errors = profile_store.save_profile(
        patch, tax_profile_path=tax_path, constraints_path=const_path
    )
    if save_errors:
        raise ValueError(" ".join(save_errors))
