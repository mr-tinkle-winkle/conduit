"""afterglow's ``resource_qpixmap(file name)`` mapped onto the kit's named icons / textures, so widget
code ported from afterglow keeps working.  New code: use ``icons.icon_pixmap(name)``."""
from __future__ import annotations

from .icons import icon_pixmap, texture

_NAMES = {
    "checkmark_icon.png": "check", "x_icon.png": "close", "search_icon.png": "search",
    "search_icon_active.png": "search_active", "refresh_icon.png": "refresh", "sort_icon.png": "sort",
    "filters_icon.png": "filter", "settings.png": "settings", "edit_icon.png": "edit", "copy_icon.png": "copy",
    "delete_icon.png": "delete", "favorite_star_icon.png": "star", "favorite_star_off_icon.png": "star_off",
    "volume_speaker_icon.png": "volume",
    "handle_texture.png": "handle", "handle_connector_gradient.png": "connector", "bar_marker.png": "marker",
    "active_volume_gradient.png": "volume_active", "inactive_volume_gradient.png": "volume_inactive",
}


def resource_qpixmap(file_name: str):
    name = _NAMES.get(file_name, file_name.rsplit(".", 1)[0])
    if name in ("handle", "connector", "marker", "volume_active", "volume_inactive"):
        return texture(name)
    if name.endswith("gradient") or name.endswith("_bg_gradient"):
        return texture("backdrop")
    return icon_pixmap(name)
