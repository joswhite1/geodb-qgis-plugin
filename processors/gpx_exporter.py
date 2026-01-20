# -*- coding: utf-8 -*-
"""
GPX exporter for mining claim waypoints.

Exports processed claim corners and monuments as GPX waypoints
for use with handheld GPS devices during field staking.
"""
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom

from ..utils.logger import PluginLogger


class GPXExporter:
    """
    Exports claim waypoints to GPX format.

    Supports:
    - Corner waypoints (numbered 1-4)
    - Discovery monument waypoints
    - Sideline/endline monuments (where required)
    - Custom waypoint symbols
    - Route generation for staking order
    """

    # GPX namespace
    GPX_NS = "http://www.topografix.com/GPX/1/1"
    XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
    SCHEMA_LOCATION = "http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd"

    # Garmin waypoint symbols
    # These match standard Garmin GPS symbol names for cross-compatibility
    SYMBOLS = {
        'corner': 'City (Medium)',          # Black circle for corners
        'discovery': 'Flag, Red',           # Discovery monument
        'sideline': 'Flag, Green',          # Sideline monument
        'endline': 'Flag, Green',           # Endline monument
        'location_monument': 'Navaid, Green',  # Location monument (lime green)
        'witness': 'Navaid, White',         # Witness point (white)
        'default': 'Waypoint',
    }

    def __init__(self):
        """Initialize GPX exporter."""
        self.logger = PluginLogger.get_logger()

    def export_waypoints(
        self,
        waypoints: List[Dict[str, Any]],
        output_path: str,
        creator: str = "geodb.io QClaims",
        include_route: bool = True
    ) -> bool:
        """
        Export waypoints to GPX file.

        Args:
            waypoints: List of waypoint dicts with:
                - lat: float
                - lon: float
                - name: str (optional)
                - type: str ('corner', 'discovery', 'sideline', 'endline')
                - claim: str (claim name)
                - corner_number: int (for corners)
                - sequence_number: int (for staking order)
            output_path: Path to output GPX file
            creator: Creator string for GPX metadata
            include_route: Whether to include a route for staking order

        Returns:
            True if export successful
        """
        self.logger.info(f"[GPX] Exporting {len(waypoints)} waypoints to {output_path}")

        try:
            # Create GPX root element
            gpx = ET.Element('gpx')
            gpx.set('xmlns', self.GPX_NS)
            gpx.set('xmlns:xsi', self.XSI_NS)
            gpx.set('xsi:schemaLocation', self.SCHEMA_LOCATION)
            gpx.set('version', '1.1')
            gpx.set('creator', creator)

            # Add metadata
            metadata = ET.SubElement(gpx, 'metadata')
            name = ET.SubElement(metadata, 'name')
            name.text = 'Mining Claim Waypoints'
            time_elem = ET.SubElement(metadata, 'time')
            time_elem.text = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

            # Sort waypoints by sequence number if available
            sorted_waypoints = sorted(
                waypoints,
                key=lambda w: w.get('sequence_number', 0)
            )

            # Add waypoints
            for wpt_data in sorted_waypoints:
                wpt = self._create_waypoint_element(wpt_data)
                gpx.append(wpt)

            # Add route if requested
            if include_route and len(sorted_waypoints) > 1:
                route = self._create_route_element(sorted_waypoints)
                gpx.append(route)

            # Write to file with pretty printing
            xml_string = ET.tostring(gpx, encoding='unicode')
            dom = minidom.parseString(xml_string)
            pretty_xml = dom.toprettyxml(indent='  ')

            # Remove extra blank lines
            lines = [line for line in pretty_xml.split('\n') if line.strip()]
            pretty_xml = '\n'.join(lines)

            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(pretty_xml)

            self.logger.info(f"[GPX] Successfully exported to {output_path}")
            return True

        except Exception as e:
            self.logger.error(f"[GPX] Export failed: {e}")
            return False

    def _create_waypoint_element(self, wpt_data: Dict[str, Any]) -> ET.Element:
        """Create a GPX waypoint element."""
        wpt = ET.Element('wpt')
        wpt.set('lat', str(wpt_data.get('lat', 0)))
        wpt.set('lon', str(wpt_data.get('lon', 0)))

        # Name
        wpt_type = wpt_data.get('type', 'default')
        claim_name = wpt_data.get('claim', 'Claim')

        if wpt_type == 'corner':
            corner_num = wpt_data.get('corner_number', 0)
            corner_name = wpt_data.get('name', f'C{corner_num}')
            name_text = f"{claim_name} {corner_name}"
        elif wpt_type == 'discovery':
            name_text = f"{claim_name} Discovery"
        elif wpt_type == 'witness':
            # Witness points use their original name (e.g., WIT 1, WP 1)
            name_text = wpt_data.get('name', f"{claim_name} Witness")
        elif wpt_type == 'location_monument':
            name_text = f"{claim_name} LM"
        else:
            name_text = wpt_data.get('name', f"{claim_name} WPT")

        name = ET.SubElement(wpt, 'name')
        name.text = name_text

        # Description
        desc = ET.SubElement(wpt, 'desc')
        desc_parts = [f"Claim: {claim_name}"]
        if wpt_type == 'corner':
            desc_parts.append(f"Corner: {wpt_data.get('name', corner_num)}")
        elif wpt_type == 'discovery':
            desc_parts.append("Discovery Monument")
        elif wpt_type == 'witness':
            desc_parts.append("Witness Point - placed on accessible public land")
        elif wpt_type == 'location_monument':
            desc_parts.append("Location Monument")
        desc.text = ', '.join(desc_parts)

        # Symbol - use custom symbol if provided, otherwise look up by type
        sym = ET.SubElement(wpt, 'sym')
        custom_symbol = wpt_data.get('symbol')
        if custom_symbol:
            sym.text = custom_symbol
        else:
            sym.text = self.SYMBOLS.get(wpt_type, self.SYMBOLS['default'])

        # Type
        type_elem = ET.SubElement(wpt, 'type')
        type_elem.text = wpt_type

        return wpt

    def _create_route_element(self, waypoints: List[Dict[str, Any]]) -> ET.Element:
        """Create a GPX route element for staking order."""
        rte = ET.Element('rte')

        name = ET.SubElement(rte, 'name')
        name.text = 'Staking Route'

        desc = ET.SubElement(rte, 'desc')
        desc.text = f'Route for staking {len(waypoints)} waypoints'

        # Add route points
        for wpt_data in waypoints:
            rtept = ET.SubElement(rte, 'rtept')
            rtept.set('lat', str(wpt_data.get('lat', 0)))
            rtept.set('lon', str(wpt_data.get('lon', 0)))

            # Name
            wpt_type = wpt_data.get('type', 'default')
            claim_name = wpt_data.get('claim', 'Claim')

            if wpt_type == 'corner':
                corner_num = wpt_data.get('corner_number', 0)
                corner_name = wpt_data.get('name', f'C{corner_num}')
                name_text = f"{claim_name} {corner_name}"
            elif wpt_type == 'discovery':
                name_text = f"{claim_name} Discovery"
            elif wpt_type == 'witness':
                name_text = wpt_data.get('name', f"{claim_name} Witness")
            elif wpt_type == 'location_monument':
                name_text = f"{claim_name} LM"
            else:
                name_text = wpt_data.get('name', f"{claim_name} WPT")

            rtept_name = ET.SubElement(rtept, 'name')
            rtept_name.text = name_text

        return rte

    def export_claims(
        self,
        claims: List[Dict[str, Any]],
        output_path: str,
        include_discovery: bool = True,
        include_sideline: bool = True
    ) -> bool:
        """
        Export processed claims as GPX waypoints.

        Args:
            claims: List of processed claim dicts from server
            output_path: Output file path
            include_discovery: Include discovery monuments
            include_sideline: Include sideline/endline monuments

        Returns:
            True if successful
        """
        waypoints = []
        seq_num = 1

        for claim in claims:
            claim_name = claim.get('name', 'Claim')
            state = claim.get('state', '').upper()

            # Get the LM corner number for this claim (Idaho/New Mexico use this)
            # LM corner is where the location monument is placed relative to
            lm_corner_num = claim.get('lm_corner', None)

            # Add corners
            corners = claim.get('corners', [])
            for corner in corners:
                corner_num = corner.get('corner_number', 0)

                # Check if this corner is the LM corner (Idaho/New Mexico)
                # LM corners get Navaid, Green symbol
                is_lm_corner = (
                    corner.get('is_lm_corner', False) or
                    corner.get('is_location_monument', False) or
                    (lm_corner_num is not None and corner_num == lm_corner_num)
                )

                if is_lm_corner:
                    wpt_type = 'location_monument'
                    wpt_name = corner.get('name', f"LM C{corner_num}")
                else:
                    wpt_type = 'corner'
                    wpt_name = corner.get('name', f"C{corner_num}")

                waypoints.append({
                    'lat': corner.get('lat'),
                    'lon': corner.get('lon'),
                    'name': wpt_name,
                    'type': wpt_type,
                    'claim': claim_name,
                    'corner_number': corner_num,
                    'sequence_number': seq_num,
                })
                seq_num += 1

            # Add discovery monument (centerline monument)
            # Skip for Idaho (ID) and New Mexico (NM) - these states use "monument-as-corner"
            # system where the location monument IS one of the corners, not a separate point
            # on the centerline
            if include_discovery and state not in ['ID', 'NM']:
                discovery = claim.get('discovery_monument')
                if discovery:
                    waypoints.append({
                        'lat': discovery.get('lat'),
                        'lon': discovery.get('lon'),
                        'name': 'Discovery',
                        'type': 'discovery',
                        'claim': claim_name,
                        'sequence_number': seq_num,
                    })
                    seq_num += 1

            # Add sideline monuments
            if include_sideline:
                for monument in claim.get('sideline_monuments', []):
                    waypoints.append({
                        'lat': monument.get('lat'),
                        'lon': monument.get('lon'),
                        'name': monument.get('name', 'Sideline'),
                        'type': 'sideline',
                        'claim': claim_name,
                        'sequence_number': seq_num,
                    })
                    seq_num += 1

                for monument in claim.get('endline_monuments', []):
                    waypoints.append({
                        'lat': monument.get('lat'),
                        'lon': monument.get('lon'),
                        'name': monument.get('name', 'Endline'),
                        'type': 'endline',
                        'claim': claim_name,
                        'sequence_number': seq_num,
                    })
                    seq_num += 1

        return self.export_waypoints(waypoints, output_path)


def export_to_gpx(
    waypoints: List[Dict[str, Any]],
    output_path: str,
    include_route: bool = True
) -> bool:
    """
    Convenience function to export waypoints to GPX.

    Args:
        waypoints: List of waypoint dicts
        output_path: Output file path
        include_route: Include staking route

    Returns:
        True if successful
    """
    exporter = GPXExporter()
    return exporter.export_waypoints(waypoints, output_path, include_route=include_route)


def export_claims_to_gpx(
    claims: List[Dict[str, Any]],
    output_path: str
) -> bool:
    """
    Convenience function to export processed claims to GPX.

    Args:
        claims: List of processed claim dicts from server
        output_path: Output file path

    Returns:
        True if successful
    """
    exporter = GPXExporter()
    return exporter.export_claims(claims, output_path)
