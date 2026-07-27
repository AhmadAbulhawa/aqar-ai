from typing import Literal


ProductOrigin = Literal[
    "from_template",
    "added_to_complete_room",
    "user_selected",
    "unknown",
]


def public_product_origin(item: dict) -> ProductOrigin:
    """Normalize internal selection metadata into the public origin contract."""

    explicit = item.get("product_origin")
    if explicit in {
        "from_template",
        "added_to_complete_room",
        "user_selected",
    }:
        return explicit

    selection_origin = str(item.get("selection_origin") or "").strip().lower()
    selection_role = str(item.get("selection_role") or "").strip().lower()

    if selection_origin == "template" or selection_role == "template_selected":
        return "from_template"
    if (
        selection_origin == "system"
        or selection_role == "system_completion"
        or bool(item.get("added_for_completion"))
    ):
        return "added_to_complete_room"
    if selection_origin == "user" or selection_role == "user_selected":
        return "user_selected"
    return "unknown"
