# -*- coding: utf-8 -*-
"""
QGIS layer operations and management.
"""
import os
from typing import Optional, Dict, Any, List
from pathlib import Path
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsFeature,
    QgsGeometry,
    QgsFields,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsEditorWidgetSetup,
    QgsFieldConstraints
)

from ..api.exceptions import LayerError
from ..utils.logger import PluginLogger


class LayerProcessor:
    """
    Handles QGIS layer creation, updates, and queries.

    Supports both memory layers (temporary) and GeoPackage layers (persistent).
    """

    # Geometry type mapping: API type -> QGIS type
    GEOMETRY_TYPE_MAPPING = {
        'Point': QgsWkbTypes.Point,
        'LineString': QgsWkbTypes.LineString,
        'Polygon': QgsWkbTypes.Polygon,
        'MultiPoint': QgsWkbTypes.MultiPoint,
        'MultiLineString': QgsWkbTypes.MultiLineString,
        'MultiPolygon': QgsWkbTypes.MultiPolygon
    }

    # Reverse mapping for GeoPackage creation
    GEOMETRY_TYPE_NAMES = {
        QgsWkbTypes.Point: 'Point',
        QgsWkbTypes.LineString: 'LineString',
        QgsWkbTypes.Polygon: 'Polygon',
        QgsWkbTypes.MultiPoint: 'MultiPoint',
        QgsWkbTypes.MultiLineString: 'MultiLineString',
        QgsWkbTypes.MultiPolygon: 'MultiPolygon'
    }

    def __init__(self, config):
        """
        Initialize layer processor.

        Args:
            config: Configuration instance
        """
        self.config = config
        self.logger = PluginLogger.get_logger()
        self._geopackage_path: Optional[str] = None
        self._use_geopackage: bool = False

    def set_geopackage_path(self, path: Optional[str]):
        """
        Set GeoPackage path for persistent storage.

        Args:
            path: Path to GeoPackage file, or None for memory layers
        """
        self._geopackage_path = path
        self._use_geopackage = path is not None
        if path:
            self.logger.info(f"LayerProcessor using GeoPackage: {path}")
        else:
            self.logger.info("LayerProcessor using memory layers")

    def is_using_geopackage(self) -> bool:
        """Check if using GeoPackage storage."""
        return self._use_geopackage and self._geopackage_path is not None

    def get_geopackage_path(self) -> Optional[str]:
        """Get current GeoPackage path."""
        return self._geopackage_path

    def _build_layer_name(self, model_name: str, project_name: Optional[str] = None) -> str:
        """
        Build layer name with optional project prefix.

        Args:
            model_name: Model name
            project_name: Optional project name to prefix

        Returns:
            Layer name in format 'ProjectName_ModelName' or just 'ModelName'
        """
        if project_name:
            return f"{project_name}_{model_name}"
        return model_name

    def get_or_create_layer(
        self,
        model_name: str,
        geometry_type: str,
        fields: QgsFields,
        crs: Optional[str] = None,
        project_name: Optional[str] = None
    ) -> QgsVectorLayer:
        """
        Get existing layer or create new one.

        Args:
            model_name: Model name (used as layer name)
            geometry_type: Geometry type (Point, Polygon, etc.)
            fields: QgsFields object
            crs: CRS string (default: EPSG:4326)
            project_name: Optional project name to prefix layer name

        Returns:
            QgsVectorLayer
        """
        # Build the display layer name with project prefix
        layer_name = self._build_layer_name(model_name, project_name)

        self.logger.info(f"Getting or creating layer for {layer_name}")

        # Check if layer already exists in project
        layer = self.find_layer_by_name(layer_name)

        if layer:
            self.logger.info(f"Found existing layer: {layer_name}")
            return layer

        # Check if layer exists in GeoPackage but not loaded
        if self.is_using_geopackage():
            layer = self._load_layer_from_geopackage(model_name)
            if layer:
                self.logger.info(f"Loaded layer from GeoPackage: {layer_name}")
                # Update display name if needed
                if layer.name() != layer_name:
                    layer.setName(layer_name)
                return layer

        # Create new layer
        return self.create_layer(model_name, geometry_type, fields, crs, project_name)

    def create_layer(
        self,
        model_name: str,
        geometry_type: str,
        fields: QgsFields,
        crs: Optional[str] = None,
        project_name: Optional[str] = None
    ) -> QgsVectorLayer:
        """
        Create new vector layer.

        Creates either a memory layer or GeoPackage layer depending on configuration.

        Args:
            model_name: Model name
            geometry_type: Geometry type
            fields: Field definitions
            crs: Coordinate reference system
            project_name: Optional project name to prefix layer name

        Returns:
            QgsVectorLayer
        """
        layer_name = self._build_layer_name(model_name, project_name)
        self.logger.info(f"Creating new layer: {layer_name}")

        if crs is None:
            crs = self.config.get('data.default_crs', 'EPSG:4326')

        if self.is_using_geopackage():
            return self._create_geopackage_layer(model_name, geometry_type, fields, crs, layer_name)
        else:
            return self._create_memory_layer(model_name, geometry_type, fields, crs, layer_name)

    def _create_memory_layer(
        self,
        model_name: str,
        geometry_type: str,
        fields: QgsFields,
        crs: str,
        layer_name: str
    ) -> QgsVectorLayer:
        """Create a memory (temporary) layer.

        Args:
            model_name: Model name (not used for memory layers but kept for signature consistency)
            geometry_type: Geometry type string (Point, LineString, Polygon, etc.)
            fields: QgsFields object with field definitions
            crs: CRS string - EPSG code (e.g., 'EPSG:4326')
            layer_name: Display name for the layer

        Returns:
            QgsVectorLayer (memory provider)
        """
        from qgis.core import QgsMessageLog, Qgis

        # Create CRS object from EPSG code
        crs_obj = QgsCoordinateReferenceSystem(crs)

        if not crs_obj.isValid():
            self.logger.warning(f"Invalid CRS '{crs}', falling back to EPSG:4326")
            QgsMessageLog.logMessage(
                f"Invalid CRS, falling back to EPSG:4326. Original: {crs}",
                "GeodbIO", Qgis.Warning
            )
            crs_obj = QgsCoordinateReferenceSystem('EPSG:4326')

        layer_uri = f"{geometry_type}?crs={crs_obj.authid()}"

        self.logger.info(f"Creating memory layer with URI: {layer_uri}")

        layer = QgsVectorLayer(layer_uri, layer_name, "memory")

        if not layer.isValid():
            raise LayerError(f"Failed to create memory layer: {layer_name}")

        # Add fields
        layer.startEditing()
        provider = layer.dataProvider()
        provider.addAttributes(fields.toList())
        layer.updateFields()
        layer.commitChanges()

        # Add to project
        QgsProject.instance().addMapLayer(layer)

        self.logger.info(f"Created memory layer: {layer_name} with CRS: {crs_obj.description()}")
        return layer

    def _create_geopackage_layer(
        self,
        model_name: str,
        geometry_type: str,
        fields: QgsFields,
        crs: str,
        layer_name: str
    ) -> QgsVectorLayer:
        """Create a layer in the GeoPackage file.

        Args:
            model_name: Model name (used as table name in GeoPackage)
            geometry_type: Geometry type string
            fields: QgsFields object with field definitions
            crs: CRS string - can be EPSG code or proj4 string
            layer_name: Display name for the layer

        Returns:
            QgsVectorLayer (ogr provider)
        """
        gpkg_path = self._geopackage_path

        # Ensure GeoPackage exists
        if not os.path.exists(gpkg_path):
            self.logger.info(f"Creating new GeoPackage: {gpkg_path}")

        # Get the QGIS geometry type
        qgs_geom_type = self.GEOMETRY_TYPE_MAPPING.get(geometry_type, QgsWkbTypes.Point)

        # Create CRS object - supports both EPSG codes and proj4 strings
        crs_obj = QgsCoordinateReferenceSystem(crs)
        if not crs_obj.isValid():
            self.logger.warning(f"Invalid CRS '{crs[:50]}...', falling back to EPSG:4326")
            crs_obj = QgsCoordinateReferenceSystem('EPSG:4326')

        # Use authid or proj4 for the temp layer URI
        crs_uri_part = crs_obj.authid() if crs_obj.authid() else crs_obj.toProj()

        # Create a temporary memory layer first to get the schema right
        temp_uri = f"{geometry_type}?crs={crs_uri_part}"
        temp_layer = QgsVectorLayer(temp_uri, model_name, "memory")
        temp_layer.startEditing()
        temp_layer.dataProvider().addAttributes(fields.toList())
        temp_layer.updateFields()
        temp_layer.commitChanges()

        # Write to GeoPackage
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        # Note: GeoPackage layer name (table name) remains as model_name for consistency
        options.layerName = model_name

        # If GeoPackage already exists, update it
        if os.path.exists(gpkg_path):
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        else:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

        # Write the empty layer structure
        error = QgsVectorFileWriter.writeAsVectorFormatV3(
            temp_layer,
            gpkg_path,
            QgsProject.instance().transformContext(),
            options
        )

        if error[0] != QgsVectorFileWriter.NoError:
            raise LayerError(f"Failed to create GeoPackage layer: {error[1]}")

        # Load the layer from GeoPackage with the display name
        layer_uri = f"{gpkg_path}|layername={model_name}"
        layer = QgsVectorLayer(layer_uri, layer_name, "ogr")

        if not layer.isValid():
            raise LayerError(f"Failed to load GeoPackage layer: {layer_name}")

        # Add to project
        QgsProject.instance().addMapLayer(layer)

        self.logger.info(f"Created GeoPackage layer: {layer_name} in {gpkg_path}")
        return layer

    def _load_layer_from_geopackage(self, model_name: str) -> Optional[QgsVectorLayer]:
        """
        Try to load a layer from the GeoPackage if it exists.

        Args:
            model_name: Layer/table name in GeoPackage

        Returns:
            QgsVectorLayer if found, None otherwise
        """
        if not self._geopackage_path or not os.path.exists(self._geopackage_path):
            return None

        layer_uri = f"{self._geopackage_path}|layername={model_name}"
        layer = QgsVectorLayer(layer_uri, model_name, "ogr")

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            return layer

        return None

    def delete_layer_from_geopackage(self, model_name: str) -> bool:
        """
        Delete a layer from the GeoPackage file.

        Args:
            model_name: Layer/table name to delete

        Returns:
            True if successful
        """
        if not self._geopackage_path or not os.path.exists(self._geopackage_path):
            return False

        ds = None
        try:
            from osgeo import ogr

            # Open GeoPackage for update
            ds = ogr.Open(self._geopackage_path, 1)  # 1 = update mode
            if ds is None:
                self.logger.error(f"Could not open GeoPackage: {self._geopackage_path}")
                return False

            # Find and delete the layer
            layer_count = ds.GetLayerCount()
            for i in range(layer_count):
                layer = ds.GetLayerByIndex(i)
                if layer and layer.GetName() == model_name:
                    ds.DeleteLayer(i)
                    self.logger.info(f"Deleted layer {model_name} from GeoPackage")
                    return True

            self.logger.warning(f"Layer {model_name} not found in GeoPackage")
            return False

        except ImportError:
            self.logger.error("GDAL/OGR not available for layer deletion")
            return False
        except Exception as e:
            self.logger.error(f"Error deleting layer from GeoPackage: {e}")
            return False
        finally:
            # Ensure datasource is closed
            if ds is not None:
                ds = None

    def remove_and_recreate_layer(
        self,
        model_name: str,
        geometry_type: str,
        fields: QgsFields,
        crs: Optional[str] = None,
        project_name: Optional[str] = None
    ) -> QgsVectorLayer:
        """
        Remove existing layer and create a fresh one.

        Useful when the schema has changed (e.g., field length updates).

        Args:
            model_name: Model name
            geometry_type: Geometry type
            fields: New field definitions
            crs: CRS string
            project_name: Optional project name to prefix layer name

        Returns:
            New QgsVectorLayer
        """
        layer_name = self._build_layer_name(model_name, project_name)
        self.logger.info(f"Removing and recreating layer: {layer_name}")

        # Remove from QGIS project if loaded (check both with and without prefix)
        existing_layer = self.find_layer_by_name(layer_name)
        if not existing_layer:
            # Also check for layer without project prefix (for backwards compatibility)
            existing_layer = self.find_layer_by_name(model_name)

        if existing_layer:
            QgsProject.instance().removeMapLayer(existing_layer.id())

        # Delete from GeoPackage if using GeoPackage
        if self.is_using_geopackage():
            self.delete_layer_from_geopackage(model_name)

        # Create fresh layer
        return self.create_layer(model_name, geometry_type, fields, crs, project_name)

    def find_layer_by_name(self, name: str) -> Optional[QgsVectorLayer]:
        """
        Find layer by name in current project.

        Args:
            name: Layer name

        Returns:
            QgsVectorLayer or None
        """
        layers = QgsProject.instance().mapLayersByName(name)

        if layers:
            return layers[0]

        return None

    def add_features(
        self,
        layer: QgsVectorLayer,
        features_data: List[Dict[str, Any]],
        geometry_field: str = 'geometry'
    ) -> int:
        """
        Add features to layer.

        Args:
            layer: Target layer
            features_data: List of feature dictionaries
            geometry_field: Name of geometry field in data

        Returns:
            Number of features added
        """
        from qgis.core import QgsMessageLog, Qgis

        QgsMessageLog.logMessage(
            f"Adding {len(features_data)} features to layer {layer.name()}",
            "GeodbIO", Qgis.Info
        )

        # Debug: log first feature's geometry info once
        if features_data:
            first_feature = features_data[0]
            geom_sample = first_feature.get(geometry_field)
            if geom_sample:
                if isinstance(geom_sample, dict):
                    QgsMessageLog.logMessage(
                        f"Geometry format: GeoJSON dict with keys {list(geom_sample.keys())}",
                        "GeodbIO", Qgis.Info
                    )
                elif isinstance(geom_sample, str):
                    preview = geom_sample[:100] + '...' if len(geom_sample) > 100 else geom_sample
                    QgsMessageLog.logMessage(
                        f"Geometry format: WKT string: {preview}",
                        "GeodbIO", Qgis.Info
                    )
                else:
                    QgsMessageLog.logMessage(
                        f"Geometry format: {type(geom_sample).__name__}",
                        "GeodbIO", Qgis.Info
                    )
            else:
                QgsMessageLog.logMessage(
                    f"First feature has NO geometry data in field '{geometry_field}'",
                    "GeodbIO", Qgis.Warning
                )
                QgsMessageLog.logMessage(
                    f"Available keys: {list(first_feature.keys())}",
                    "GeodbIO", Qgis.Info
                )

        layer.startEditing()

        added_count = 0
        geom_success = 0
        geom_failed = 0
        first_failure_logged = False

        # Check if layer has geometry (not NoGeometry type)
        layer_has_geometry = layer.geometryType() != 4  # 4 = QgsWkbTypes.NullGeometry
        skipped_no_geom = 0

        for feature_data in features_data:
            feature = QgsFeature()
            feature.setFields(layer.fields())

            # Set geometry - handle both WKT string and GeoJSON dict formats
            # For models like DrillPad, try 'geometry' first, then fall back to 'location'
            geom_data = feature_data.get(geometry_field)
            geometry = None

            # Try primary geometry field (polygon/geometry)
            if geom_data:
                geometry = self._parse_geometry(geom_data)

            # Fallback: try 'location' field if primary geometry is null
            # DrillPad uses 'location' for pad center point when 'geometry' (polygon) is not set
            # For polygon layers, convert point to a 30m x 30m square
            if (not geometry or geometry.isNull()) and 'location' in feature_data:
                location_data = feature_data.get('location')
                if location_data:
                    point_geom = self._parse_geometry(location_data)
                    if point_geom and not point_geom.isNull():
                        # Check if layer expects polygon but we have a point
                        # layer.geometryType() returns QgsWkbTypes.GeometryType enum:
                        # 0=Point, 1=Line, 2=Polygon, 3=Unknown, 4=Null
                        layer_geom_type = layer.geometryType()
                        if layer_geom_type == 2:  # PolygonGeometry
                            # Create 30m x 30m square centered on point
                            geometry = self._point_to_square_polygon(point_geom, size_meters=30)
                        else:
                            geometry = point_geom

            if geometry and not geometry.isNull():
                feature.setGeometry(geometry)
                geom_success += 1
            elif geom_data or feature_data.get('location'):
                geom_failed += 1
                # Log first failure for debugging
                if not first_failure_logged:
                    first_failure_logged = True
                    preview = str(geom_data or feature_data.get('location'))[:200]
                    QgsMessageLog.logMessage(
                        f"First geometry parse failure. Data: {preview}",
                        "GeodbIO", Qgis.Warning
                    )

            # For polygon/line layers, features without geometry are still added (with null geometry)
            # This allows viewing attributes even when geometry is missing

            # Set attributes
            for field_name in layer.fields().names():
                if field_name in feature_data:
                    value = feature_data[field_name]
                    feature.setAttribute(field_name, value)

            # Add feature
            if layer.addFeature(feature):
                added_count += 1

        layer.commitChanges()
        layer.updateExtents()
        layer.triggerRepaint()

        # Log summary
        msg = f"Added {added_count} features (geometry: {geom_success} success, {geom_failed} failed)"
        if skipped_no_geom > 0:
            msg += f", {skipped_no_geom} skipped (no geometry)"
            QgsMessageLog.logMessage(msg, "GeodbIO", Qgis.Warning)
        else:
            QgsMessageLog.logMessage(msg, "GeodbIO", Qgis.Info)

        return added_count

    def _parse_geometry(self, geom_data) -> Optional[QgsGeometry]:
        """
        Parse geometry from various formats (WKT string, EWKT string, GeoJSON dict, or GeoJSON string).

        Args:
            geom_data: Geometry data as WKT/EWKT string, GeoJSON string, or GeoJSON dict

        Returns:
            QgsGeometry or None
        """
        if not geom_data:
            return None

        try:
            # If it's a string, could be WKT, EWKT, or GeoJSON string
            if isinstance(geom_data, str):
                wkt_str = geom_data.strip()
                if not wkt_str:
                    return None

                # Check if it's a GeoJSON string (starts with '{')
                if wkt_str.startswith('{'):
                    try:
                        import json
                        geojson_dict = json.loads(wkt_str)
                        # Recursively call with the parsed dict
                        return self._parse_geometry(geojson_dict)
                    except json.JSONDecodeError:
                        pass  # Not valid JSON, try as WKT

                # Handle EWKT format: "SRID=4326;MULTIPOLYGON(...)"
                # Strip the SRID prefix if present
                if wkt_str.upper().startswith('SRID='):
                    # Find the semicolon and take everything after it
                    semicolon_idx = wkt_str.find(';')
                    if semicolon_idx != -1:
                        wkt_str = wkt_str[semicolon_idx + 1:]

                geometry = QgsGeometry.fromWkt(wkt_str)
                if geometry and not geometry.isNull():
                    return geometry
                return None

            # If it's a dict, assume GeoJSON
            if isinstance(geom_data, dict):
                # Check required GeoJSON fields
                if 'type' not in geom_data or 'coordinates' not in geom_data:
                    return None

                # Try OGR first (most reliable for GeoJSON)
                try:
                    import json
                    from osgeo import ogr
                    geojson_str = json.dumps(geom_data)
                    ogr_geom = ogr.CreateGeometryFromJson(geojson_str)
                    if ogr_geom:
                        wkt = ogr_geom.ExportToWkt()
                        ogr_geom = None  # Release OGR geometry
                        if wkt:
                            geometry = QgsGeometry.fromWkt(wkt)
                            if geometry and not geometry.isNull():
                                return geometry
                except Exception:
                    pass

                # Fall back to manual conversion
                try:
                    wkt = self._geojson_to_wkt(geom_data)
                    if wkt:
                        geometry = QgsGeometry.fromWkt(wkt)
                        if geometry and not geometry.isNull():
                            return geometry
                except Exception:
                    pass

                return None

            return None

        except Exception as e:
            self.logger.error(f"Error parsing geometry: {e}")
            return None

    def _geojson_to_wkt(self, geojson: dict) -> str:
        """
        Convert simple GeoJSON geometry to WKT.

        Args:
            geojson: GeoJSON geometry dict with 'type' and 'coordinates'

        Returns:
            WKT string
        """
        geom_type = geojson.get('type', '').upper()
        coords = geojson.get('coordinates', [])

        if not geom_type or not coords:
            return ''

        if geom_type == 'POINT':
            return f"POINT ({coords[0]} {coords[1]})"

        elif geom_type == 'LINESTRING':
            coord_str = ', '.join(f"{c[0]} {c[1]}" for c in coords)
            return f"LINESTRING ({coord_str})"

        elif geom_type == 'POLYGON':
            rings = []
            for ring in coords:
                ring_str = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                rings.append(f"({ring_str})")
            return f"POLYGON ({', '.join(rings)})"

        elif geom_type == 'MULTIPOINT':
            points = ', '.join(f"({c[0]} {c[1]})" for c in coords)
            return f"MULTIPOINT ({points})"

        elif geom_type == 'MULTILINESTRING':
            lines = []
            for line in coords:
                line_str = ', '.join(f"{c[0]} {c[1]}" for c in line)
                lines.append(f"({line_str})")
            return f"MULTILINESTRING ({', '.join(lines)})"

        elif geom_type == 'MULTIPOLYGON':
            polygons = []
            for polygon in coords:
                rings = []
                for ring in polygon:
                    ring_str = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                    rings.append(f"({ring_str})")
                polygons.append(f"({', '.join(rings)})")
            return f"MULTIPOLYGON ({', '.join(polygons)})"

        else:
            self.logger.warning(f"Unsupported geometry type: {geom_type}")
            return ''

    def _point_to_square_polygon(
        self,
        point_geom: QgsGeometry,
        size_meters: float = 30
    ) -> Optional[QgsGeometry]:
        """
        Create a square polygon centered on a point.

        Used for DrillPad when only a center point (location) is provided
        but no polygon boundary. Creates a square of the specified size.

        Args:
            point_geom: Point geometry (in WGS84/EPSG:4326)
            size_meters: Side length of square in meters (default 30m)

        Returns:
            Polygon geometry or None if conversion fails
        """
        try:
            if not point_geom or point_geom.isNull():
                return None

            point = point_geom.asPoint()
            lon, lat = point.x(), point.y()

            # Approximate meters to degrees conversion
            # At the equator, 1 degree ≈ 111,320 meters
            # Adjust for latitude (longitude degrees get smaller toward poles)
            import math
            meters_per_degree_lat = 111320
            meters_per_degree_lon = 111320 * math.cos(math.radians(lat))

            half_size_lat = (size_meters / 2) / meters_per_degree_lat
            half_size_lon = (size_meters / 2) / meters_per_degree_lon

            # Create square corners (clockwise from bottom-left)
            # Use 6 decimal precision for consistency with snapshot hashing
            min_lon = round(lon - half_size_lon, 6)
            max_lon = round(lon + half_size_lon, 6)
            min_lat = round(lat - half_size_lat, 6)
            max_lat = round(lat + half_size_lat, 6)

            # WKT for polygon (must close the ring)
            # Use explicit 6-decimal formatting to match sync_manager snapshot generation
            wkt = (
                f"POLYGON (("
                f"{min_lon:.6f} {min_lat:.6f}, "
                f"{max_lon:.6f} {min_lat:.6f}, "
                f"{max_lon:.6f} {max_lat:.6f}, "
                f"{min_lon:.6f} {max_lat:.6f}, "
                f"{min_lon:.6f} {min_lat:.6f}))"
            )

            polygon = QgsGeometry.fromWkt(wkt)
            if polygon and not polygon.isNull():
                return polygon

            return None

        except Exception as e:
            self.logger.error(f"Error creating square polygon from point: {e}")
            return None

    def update_feature(
        self,
        layer: QgsVectorLayer,
        feature_id: int,
        attributes: Dict[str, Any]
    ) -> bool:
        """
        Update existing feature attributes.

        Args:
            layer: Target layer
            feature_id: Feature ID
            attributes: New attribute values

        Returns:
            True if successful
        """
        layer.startEditing()

        # Get feature
        feature = layer.getFeature(feature_id)

        if not feature.isValid():
            self.logger.warning(f"Feature {feature_id} not found in layer")
            return False

        # Update attributes
        for field_name, value in attributes.items():
            field_index = layer.fields().indexFromName(field_name)
            if field_index >= 0:
                layer.changeAttributeValue(feature_id, field_index, value)

        layer.commitChanges()
        layer.triggerRepaint()

        return True

    def delete_feature(self, layer: QgsVectorLayer, feature_id: int) -> bool:
        """
        Delete feature from layer.

        Args:
            layer: Target layer
            feature_id: Feature ID to delete

        Returns:
            True if successful
        """
        layer.startEditing()
        result = layer.deleteFeature(feature_id)
        layer.commitChanges()
        layer.triggerRepaint()

        return result

    def get_all_features(self, layer: QgsVectorLayer) -> List[QgsFeature]:
        """
        Get all features from layer.

        Args:
            layer: Source layer

        Returns:
            List of QgsFeature objects
        """
        return list(layer.getFeatures())

    def layer_exists(self, model_name: str) -> bool:
        """
        Check if layer exists in project.

        Args:
            model_name: Model/layer name

        Returns:
            True if layer exists
        """
        return self.find_layer_by_name(model_name) is not None

    def clear_layer(self, layer: QgsVectorLayer) -> bool:
        """
        Remove all features from a layer.

        Args:
            layer: Layer to clear

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            return False

        layer.startEditing()
        feature_ids = [f.id() for f in layer.getFeatures()]
        layer.deleteFeatures(feature_ids)
        layer.commitChanges()
        layer.triggerRepaint()

        self.logger.info(f"Cleared {len(feature_ids)} features from {layer.name()}")
        return True

    def configure_field_widget(
        self,
        layer: QgsVectorLayer,
        field_name: str,
        widget_type: str,
        config: Dict[str, Any]
    ):
        """
        Configure QGIS editor widget for a field.

        Args:
            layer: Vector layer
            field_name: Field name
            widget_type: Widget type (e.g., 'ValueMap', 'DateTime', 'TextEdit')
            config: Widget configuration dict
        """
        if not layer or not layer.isValid():
            return

        field_idx = layer.fields().indexOf(field_name)
        if field_idx >= 0:
            setup = QgsEditorWidgetSetup(widget_type, config)
            layer.setEditorWidgetSetup(field_idx, setup)
            self.logger.info(f"Configured {widget_type} widget for field '{field_name}'")

    def set_field_readonly(
        self,
        layer: QgsVectorLayer,
        field_name: str,
        readonly: bool = True
    ):
        """
        Make a field non-editable in attribute forms.

        Args:
            layer: Vector layer
            field_name: Field name
            readonly: True to make readonly, False to make editable
        """
        if not layer or not layer.isValid():
            return

        field_idx = layer.fields().indexOf(field_name)
        if field_idx >= 0:
            # Configure form widget to be read-only
            if readonly:
                # Get existing widget setup or create default
                setup = layer.editorWidgetSetup(field_idx)
                widget_type = setup.type() if setup.type() else 'TextEdit'
                config = setup.config() if setup.config() else {}

                # Add read-only config
                config['IsMultiline'] = False
                config['UseHtml'] = False
                config['ReadOnly'] = True

                layer.setEditorWidgetSetup(field_idx, QgsEditorWidgetSetup(widget_type, config))
            self.logger.info(f"Set field '{field_name}' readonly={readonly}")

    def set_field_as_url(
        self,
        layer: QgsVectorLayer,
        field_name: str
    ):
        """
        Configure a field to display URLs as clickable links.

        Uses the ExternalResource widget which provides:
        - Clickable links that open in default browser/application
        - Support for both local file paths and HTTP(S) URLs
        - Read-only display (URLs cannot be edited in form)

        Args:
            layer: Vector layer
            field_name: Field name containing URLs
        """
        if not layer or not layer.isValid():
            return

        field_idx = layer.fields().indexOf(field_name)
        if field_idx >= 0:
            # Configure as ExternalResource (attachment/URL widget)
            config = {
                'DocumentViewer': 0,  # Use external viewer/browser
                'DocumentViewerWidth': 0,
                'DocumentViewerHeight': 0,
                'RelativeStorage': 0,  # Absolute paths/URLs (not relative)
                'FileWidget': True,  # Show as file/link widget
                'FileWidgetButton': True,  # Show button to open URL
                'FileWidgetFilter': '',  # No file filter (accept all URLs)
                'StorageMode': 0,  # Files and URLs
                'UseLink': True,  # Display as clickable link
                'FullUrl': True  # Treat as full URL
            }

            layer.setEditorWidgetSetup(
                field_idx,
                QgsEditorWidgetSetup('ExternalResource', config)
            )
            self.logger.info(f"Configured field '{field_name}' as clickable URL")

    def set_field_alias(
        self,
        layer: QgsVectorLayer,
        field_name: str,
        alias: str
    ):
        """
        Set a user-friendly alias for a field.

        Args:
            layer: Vector layer
            field_name: Field name
            alias: Display alias
        """
        if not layer or not layer.isValid():
            return

        field_idx = layer.fields().indexOf(field_name)
        if field_idx >= 0:
            layer.setFieldAlias(field_idx, alias)

    def set_layer_project_metadata(
        self,
        layer: QgsVectorLayer,
        project_name: str,
        company_name: str,
        crs_metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Store project metadata in the GeoPackage metadata table.

        This metadata is used when pushing new features to ensure they're
        assigned to the correct project, even if the user has switched
        to a different active project. Stored in GeoPackage for offline
        portability.

        Args:
            layer: Vector layer
            project_name: Project name
            company_name: Company name
            crs_metadata: Optional CRS metadata dict
        """
        if not layer or not layer.isValid():
            return

        # Store in GeoPackage if using GeoPackage storage
        if self.is_using_geopackage() and self._geopackage_path:
            self._store_metadata_in_geopackage(
                project_name=project_name,
                company_name=company_name,
                crs_metadata=crs_metadata
            )
        else:
            # Fallback to layer custom properties for memory layers
            import json
            layer.setCustomProperty('geodb_project_name', project_name)
            layer.setCustomProperty('geodb_company_name', company_name)
            if crs_metadata:
                layer.setCustomProperty('geodb_crs_metadata', json.dumps(crs_metadata))

        self.logger.debug(f"Stored project metadata: {project_name} / {company_name}")

    def _store_metadata_in_geopackage(
        self,
        project_name: str,
        company_name: str,
        crs_metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Store project metadata in a custom table in the GeoPackage.

        Creates a geodb_metadata table if it doesn't exist.

        Args:
            project_name: Project name
            company_name: Company name
            crs_metadata: Optional CRS metadata dict
        """
        import sqlite3
        import json

        if not self._geopackage_path:
            return

        try:
            conn = sqlite3.connect(self._geopackage_path)
            cursor = conn.cursor()

            # Create metadata table if it doesn't exist
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS geodb_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Store project info
            cursor.execute('''
                INSERT OR REPLACE INTO geodb_metadata (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', ('project_name', project_name))

            cursor.execute('''
                INSERT OR REPLACE INTO geodb_metadata (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', ('company_name', company_name))

            if crs_metadata:
                cursor.execute('''
                    INSERT OR REPLACE INTO geodb_metadata (key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                ''', ('crs_metadata', json.dumps(crs_metadata)))

            conn.commit()
            conn.close()

            self.logger.info(f"Stored metadata in GeoPackage: {project_name} / {company_name}")

        except sqlite3.Error as e:
            self.logger.error(f"Failed to store metadata in GeoPackage: {e}")

    def get_layer_project_metadata(
        self,
        layer: QgsVectorLayer
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve project metadata from GeoPackage or layer custom properties.

        Args:
            layer: Vector layer

        Returns:
            Dict with 'project_name', 'company_name', and optional 'crs_metadata',
            or None if not set
        """
        # Try GeoPackage first
        if self.is_using_geopackage() and self._geopackage_path:
            metadata = self._get_metadata_from_geopackage()
            if metadata:
                return metadata

        # Fallback to layer custom properties (for memory layers or migration)
        if not layer or not layer.isValid():
            return None

        import json
        project_name = layer.customProperty('geodb_project_name')
        company_name = layer.customProperty('geodb_company_name')

        if not project_name or not company_name:
            return None

        result = {
            'project_name': project_name,
            'company_name': company_name,
            'project_natural_key': {
                'name': project_name,
                'company': company_name
            }
        }

        crs_metadata_str = layer.customProperty('geodb_crs_metadata')
        if crs_metadata_str:
            try:
                result['crs_metadata'] = json.loads(crs_metadata_str)
            except json.JSONDecodeError:
                pass

        return result

    def _get_metadata_from_geopackage(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve project metadata from the GeoPackage metadata table.

        Returns:
            Dict with project metadata, or None if not found
        """
        import sqlite3
        import json

        if not self._geopackage_path or not os.path.exists(self._geopackage_path):
            return None

        try:
            conn = sqlite3.connect(self._geopackage_path)
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute('''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='geodb_metadata'
            ''')
            if not cursor.fetchone():
                conn.close()
                return None

            # Get all metadata
            cursor.execute('SELECT key, value FROM geodb_metadata')
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return None

            metadata = {row[0]: row[1] for row in rows}

            project_name = metadata.get('project_name')
            company_name = metadata.get('company_name')

            if not project_name or not company_name:
                return None

            result = {
                'project_name': project_name,
                'company_name': company_name,
                'project_natural_key': {
                    'name': project_name,
                    'company': company_name
                }
            }

            crs_metadata_str = metadata.get('crs_metadata')
            if crs_metadata_str:
                try:
                    result['crs_metadata'] = json.loads(crs_metadata_str)
                except json.JSONDecodeError:
                    pass

            return result

        except sqlite3.Error as e:
            self.logger.error(f"Failed to read metadata from GeoPackage: {e}")
            return None
