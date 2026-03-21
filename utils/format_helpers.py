"""
Shared formatting helpers for the geoDB QGIS plugin.

These functions generate display strings (HTML, plain text) used by
multiple UI components, avoiding duplication across dialog classes.
"""

from typing import Any, Dict, Optional


def format_merge_settings_html(
    config: Dict[str, Any],
    merge_settings_map: Optional[Dict[int, Dict[str, Any]]] = None,
) -> str:
    """Format assay merge settings as an HTML string for display.

    Builds a multi-line HTML summary of the merge configuration
    including strategy, units, element-specific overrides, and
    below-detection-limit handling.

    Args:
        config: An assay configuration dict. Expected keys:
            - ``assay_merge_settings`` (int): ID into *merge_settings_map*.
            - ``element`` (str, optional): Element symbol used to look up
              per-element overrides.
        merge_settings_map: Mapping of merge-settings IDs to their detail
            dicts.  If ``None`` or the referenced ID is missing, a
            fallback message is returned.

    Returns:
        HTML string suitable for ``QLabel.setText()``.
    """
    if merge_settings_map is None:
        merge_settings_map = {}

    merge_settings_id = config.get('assay_merge_settings')

    if not merge_settings_id or merge_settings_id not in merge_settings_map:
        return "Merge settings information not available."

    merge_settings = merge_settings_map[merge_settings_id]

    # Extract merge settings details
    name = merge_settings.get('name', 'Unknown')
    default_strategy = merge_settings.get('default_strategy', 'high')
    default_units = merge_settings.get('default_units', 'ppm')
    convert_bdl = merge_settings.get('convert_bdl', True)
    bdl_multiplier = merge_settings.get('bdl_multiplier', 0.5)

    # Check for element-specific overrides
    element = config.get('element', '')
    element_overrides = merge_settings.get('element_overrides', [])

    # Find override for this element
    element_override = None
    for override in element_overrides:
        if override.get('element') == element:
            element_override = override
            break

    # Build info text
    info_parts = [
        f"<b>Configuration:</b> {name}",
        f"<b>Merge Strategy:</b> {default_strategy.title()}",
    ]

    if element_override:
        override_strategy = element_override.get('strategy', default_strategy)
        override_units = element_override.get('target_units', default_units)
        info_parts.append(
            f"<b>Element Override ({element}):</b> {override_strategy.title()}, {override_units}"
        )
    else:
        info_parts.append(f"<b>Default Units:</b> {default_units}")

    if convert_bdl:
        info_parts.append(
            f"<b>Below Detection Limit:</b> Convert to {bdl_multiplier * 100:.0f}% of detection limit"
        )
    else:
        info_parts.append("<b>Below Detection Limit:</b> No conversion")

    return "<br>".join(info_parts)
