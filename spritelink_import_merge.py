"""Conflict-safe merging of portable SpriteLink emojis and identity presets."""
from __future__ import annotations

import re
from typing import Any, Callable


def merge_emoji_data(
    local: dict[str, str],
    incoming: dict[str, str],
    validate: Callable[[str, str], bool],
) -> tuple[dict[str, str], int]:
    """Preserve local emojis and import only new name+image combinations.

    Name comparison is case-insensitive. Different images with an occupied
    name receive _2, _3, ...; reimporting a previously suffixed image is a no-op.
    """
    if not isinstance(incoming, dict):
        raise ValueError("Invalid Emoji data in archive.")
    for name, encoded in incoming.items():
        if not validate(name, encoded):
            raise ValueError("Invalid emoji in archive.")

    def numbered_name(name: str, suffix: str, encoded: str) -> str:
        # The 32-character limit may split a hyphen/apostrophe in a
        # multiword name. Trim that dangling punctuation too.
        base = name[:32 - len(suffix)]
        while base and not validate(base + suffix, encoded):
            base = base[:-1]
        if not base:
            raise ValueError("Could not give imported emoji a valid name.")
        return base + suffix

    result = dict(local)
    names = {name.casefold(): name for name in result}
    added = 0
    for name, encoded in incoming.items():
        folded = name.casefold()
        existing_name = names.get(folded)
        if existing_name is None:
            result[name] = encoded
            names[folded] = name
            added += 1
            continue
        if result[existing_name] == encoded:
            continue

        # Avoid duplicate images that previous merges already renamed.
        # Matching is by the original name family AND image data; different
        # names may intentionally refer to the same drawing.
        already_imported = False
        for existing_name, existing_image in result.items():
            if existing_image != encoded:
                continue
            match = re.search(r"_[2-9][0-9]*$", existing_name)
            if match is None:
                continue
            suffix = match.group()
            expected_name = numbered_name(name, suffix, encoded)
            if existing_name.casefold() == expected_name.casefold():
                already_imported = True
                break
        if already_imported:
            continue

        for number in range(2, 10001):
            suffix = f"_{number}"
            candidate = numbered_name(name, suffix, encoded)
            existing = names.get(candidate.casefold())
            if existing is not None:
                if result[existing] == encoded:
                    break
                continue
            if not validate(candidate, encoded):
                raise ValueError("Could not give imported emoji a valid name.")
            result[candidate] = encoded
            names[candidate.casefold()] = candidate
            added += 1
            break
        else:
            raise ValueError("Too many emojis with the same name.")
    return result, added


def merge_identity_data(
    local: list[dict[str, str]],
    incoming: list[dict[str, Any]],
    normalize: Callable[[Any], dict[str, str]],
    new_id: Callable[[], str],
    maximum: int,
) -> tuple[list[dict[str, str]], int, int]:
    """Merge identity presets by username, retaining local preset IDs/names.

    A matching name updates color and icon only. The caller retains its
    local signing key and chosen active identity, and syncs affected room
    profiles by the retained IDs.
    """
    if not isinstance(incoming, list) or not incoming:
        raise ValueError("Invalid identity presets in archive.")
    result = [dict(preset) for preset in local]
    by_name = {
        preset["username"].casefold(): i
        for i, preset in enumerate(result)
    }
    existing_ids = {preset["id"] for preset in result}
    added = 0
    updated_ids: set[str] = set()
    for raw in incoming:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("id"), str)
            or not isinstance(raw.get("username"), str)
        ):
            raise ValueError("Invalid identity preset in archive.")
        preset = normalize(raw)
        name = preset["username"].casefold()
        index = by_name.get(name)
        if index is not None:
            current = result[index]
            if (
                current["username_color"] != preset["username_color"]
                or current["profile_icon"] != preset["profile_icon"]
            ):
                current["username_color"] = preset["username_color"]
                current["profile_icon"] = preset["profile_icon"]
                updated_ids.add(current["id"])
            continue
        if len(result) >= maximum:
            raise ValueError("Too many identities to import.")
        if preset["id"] in existing_ids:
            preset["id"] = new_id()
            while preset["id"] in existing_ids:
                preset["id"] = new_id()
        existing_ids.add(preset["id"])
        by_name[name] = len(result)
        result.append(preset)
        added += 1
    return result, added, len(updated_ids)
