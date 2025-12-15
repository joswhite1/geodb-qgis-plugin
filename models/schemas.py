# -*- coding: utf-8 -*-
"""
Model schema definitions for all supported geoDB models.

Defines field types, geometry types, and constraints for proper
QGIS layer creation and API synchronization.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class FieldType(Enum):
    """QGIS field types mapped to API types."""
    STRING = 'string'
    INTEGER = 'integer'
    DOUBLE = 'double'
    BOOLEAN = 'boolean'
    DATE = 'date'
    DATETIME = 'datetime'


class GeometryType(Enum):
    """QGIS geometry types."""
    POINT = 'Point'
    LINESTRING = 'LineString'
    POLYGON = 'Polygon'
    MULTIPOINT = 'MultiPoint'
    MULTILINESTRING = 'MultiLineString'
    MULTIPOLYGON = 'MultiPolygon'
    NONE = 'NoGeometry'


@dataclass
class FieldSchema:
    """Schema definition for a single field."""
    name: str
    field_type: FieldType
    length: int = 255
    required: bool = False
    readonly: bool = False
    default: Any = None
    description: str = ""


@dataclass
class ModelSchema:
    """Schema definition for a complete model."""
    name: str
    api_endpoint: str
    geometry_type: GeometryType
    fields: List[FieldSchema]
    display_name: str = ""
    description: str = ""
    supports_push: bool = True
    supports_pull: bool = True
    natural_key_fields: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.name

    def get_field(self, name: str) -> Optional[FieldSchema]:
        """Get field schema by name."""
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def get_writable_fields(self) -> List[FieldSchema]:
        """Get fields that can be written (not readonly)."""
        return [f for f in self.fields if not f.readonly]

    def get_required_fields(self) -> List[FieldSchema]:
        """Get required fields."""
        return [f for f in self.fields if f.required]

    def filter_for_push(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Filter data to only include fields accepted by the API for writes.

        This removes computed fields, read-only fields, and QGIS-internal fields
        that would cause a 400 Bad Request from the API.

        IMPORTANT: The 'id' field is NEVER included. The server determines which
        record to update based on the URL path (e.g., /landholdings/28/), not
        the request body. Including 'id' in the body could cause issues if
        QGIS assigns local feature IDs that don't match server IDs.

        Args:
            data: Full feature data from QGIS layer

        Returns:
            Filtered data with only writable fields plus 'geometry' (never 'id')
        """
        # Get writable field names from schema
        writable_field_names = {f.name for f in self.get_writable_fields()}
        # Include 'geometry' for spatial data
        # NEVER include 'id' - server uses URL path for record identification
        writable_field_names.add('geometry')
        writable_field_names.discard('id')  # Ensure id is never included

        # Filter to only include known writable fields
        filtered = {}
        for key, value in data.items():
            if key in writable_field_names:
                filtered[key] = value

        return filtered

    def get_natural_key(self, feature_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract natural key fields from feature data.

        Args:
            feature_data: Feature dictionary

        Returns:
            Dict with natural key field values, or None if no natural key defined
        """
        if not self.natural_key_fields:
            return None
        nk = {}
        for field_name in self.natural_key_fields:
            if field_name in feature_data:
                nk[field_name] = feature_data[field_name]
        return nk if nk else None


# =============================================================================
# DRILL MODELS
# =============================================================================

DRILL_COLLAR_SCHEMA = ModelSchema(
    name='DrillCollar',
    api_endpoint='drill-collars',
    geometry_type=GeometryType.POINT,
    display_name='Drill Collars',
    description='Drill hole collar locations with survey data',
    natural_key_fields=['name', 'project'],
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('name', FieldType.STRING, length=100, required=True,
                   description='Hole ID (e.g., DDH-001)'),
        FieldSchema('project', FieldType.STRING, length=0, required=True,
                   description='Project natural key (JSON object)'),
        FieldSchema('latitude', FieldType.DOUBLE, required=True),
        FieldSchema('longitude', FieldType.DOUBLE, required=True),
        FieldSchema('elevation', FieldType.DOUBLE),
        FieldSchema('total_depth', FieldType.DOUBLE),
        FieldSchema('azimuth', FieldType.DOUBLE, description='0-360 degrees'),
        FieldSchema('dip', FieldType.DOUBLE, description='Negative for down'),
        FieldSchema('hole_type', FieldType.STRING, length=10,
                   description='DD=Diamond Core, RC=Reverse Circ, DC=Direct Circ'),
        FieldSchema('hole_status', FieldType.STRING, length=10,
                   description='CP=Completed, AB=Abandoned, PL=Planned, IP=In Progress'),
        FieldSchema('hole_size', FieldType.STRING, length=10,
                   description='Core size: AQ, BQ, NQ, NQ2, HQ, HQ3, PQ'),
        FieldSchema('length_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('date_started', FieldType.DATE),
        FieldSchema('date_completed', FieldType.DATE),
        FieldSchema('drilling_contractor', FieldType.STRING, length=100),
        FieldSchema('geologist', FieldType.STRING, length=100),
        FieldSchema('purpose', FieldType.STRING, length=255),
        FieldSchema('comments', FieldType.STRING, length=1000),
        FieldSchema('coordinate_system_metadata', FieldType.STRING, length=0,
                   description='CRS metadata for plugin sync (JSON object)'),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
        FieldSchema('created_by', FieldType.STRING, readonly=True),
        FieldSchema('last_edited_by', FieldType.STRING, readonly=True),
    ]
)

DRILL_SAMPLE_SCHEMA = ModelSchema(
    name='DrillSample',
    api_endpoint='drill-samples',
    geometry_type=GeometryType.LINESTRING,
    display_name='Drill Samples',
    description='Drill sample intervals with assay data',
    supports_push=False,  # Read-only - samples managed via web interface
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('name', FieldType.STRING, length=100, required=True),
        FieldSchema('bhid', FieldType.STRING, length=100, required=True,
                   description='Parent hole ID'),
        FieldSchema('depth_from', FieldType.DOUBLE, required=True),
        FieldSchema('depth_to', FieldType.DOUBLE, required=True),
        FieldSchema('depth_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('sample_type', FieldType.STRING, length=50),
        FieldSchema('sample_weight', FieldType.DOUBLE),
        FieldSchema('recovery_pct', FieldType.DOUBLE),
        FieldSchema('comments', FieldType.STRING, length=1000),
        # Merged assay fields (populated when merge_assays=true)
        FieldSchema('assay_Au', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Ag', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Cu', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Pb', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Zn', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Fe', FieldType.DOUBLE, readonly=True),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
    ]
)

DRILL_PAD_SCHEMA = ModelSchema(
    name='DrillPad',
    api_endpoint='drill-pads',
    geometry_type=GeometryType.POLYGON,
    display_name='Drill Pads',
    description='Drill pad locations for organizing multiple drill holes',
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('name', FieldType.STRING, length=100, required=True,
                   description='Pad name or identifier'),
        FieldSchema('project', FieldType.STRING, length=0, required=True,
                   description='Project natural key (JSON object)'),
        FieldSchema('status', FieldType.STRING, length=15,
                   description='planned, built, or historic'),
        FieldSchema('permit_number', FieldType.STRING, length=50,
                   description='Drilling permit number'),
        FieldSchema('constructed_date', FieldType.DATE,
                   description='Date pad was constructed'),
        FieldSchema('reclaimed_date', FieldType.DATE,
                   description='Date pad was reclaimed/restored'),
        FieldSchema('access_type', FieldType.STRING, length=50,
                   description='Access method (road, helicopter, etc.)'),
        FieldSchema('disturbance_area', FieldType.DOUBLE,
                   description='Disturbed area in square meters'),
        FieldSchema('notes', FieldType.STRING, length=0,
                   description='General notes'),
        FieldSchema('environmental_notes', FieldType.STRING, length=0,
                   description='Environmental considerations'),
        FieldSchema('coordinate_system_metadata', FieldType.STRING, length=0,
                   description='CRS metadata for plugin sync (JSON object)'),
        FieldSchema('hole_count', FieldType.INTEGER, readonly=True,
                   description='Number of drill holes on this pad'),
        FieldSchema('total_meters_drilled', FieldType.DOUBLE, readonly=True,
                   description='Total meters drilled from this pad'),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
        FieldSchema('created_by', FieldType.STRING, readonly=True),
        FieldSchema('last_edited_by', FieldType.STRING, readonly=True),
    ]
)

DRILL_LITHOLOGY_SCHEMA = ModelSchema(
    name='DrillLithology',
    api_endpoint='drill-lithologies',
    geometry_type=GeometryType.NONE,
    display_name='Drill Lithology',
    description='Lithology intervals for drill holes',
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('bhid', FieldType.STRING, length=100, required=True),
        FieldSchema('depth_from', FieldType.DOUBLE, required=True),
        FieldSchema('depth_to', FieldType.DOUBLE, required=True),
        FieldSchema('depth_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('lithology', FieldType.STRING, length=100, required=True),
        FieldSchema('lithology_code', FieldType.STRING, length=20),
        FieldSchema('description', FieldType.STRING, length=1000),
        FieldSchema('color', FieldType.STRING, length=50),
        FieldSchema('hardness', FieldType.STRING, length=50),
        FieldSchema('grain_size', FieldType.STRING, length=50),
        FieldSchema('texture', FieldType.STRING, length=100),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
    ]
)

DRILL_ALTERATION_SCHEMA = ModelSchema(
    name='DrillAlteration',
    api_endpoint='drill-alterations',
    geometry_type=GeometryType.NONE,
    display_name='Drill Alteration',
    description='Alteration intervals for drill holes',
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('bhid', FieldType.STRING, length=100, required=True),
        FieldSchema('depth_from', FieldType.DOUBLE, required=True),
        FieldSchema('depth_to', FieldType.DOUBLE, required=True),
        FieldSchema('depth_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('alteration_type', FieldType.STRING, length=100, required=True),
        FieldSchema('intensity', FieldType.STRING, length=50),
        FieldSchema('minerals', FieldType.STRING, length=255),
        FieldSchema('description', FieldType.STRING, length=1000),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
    ]
)

DRILL_STRUCTURE_SCHEMA = ModelSchema(
    name='DrillStructure',
    api_endpoint='drill-structures',
    geometry_type=GeometryType.NONE,
    display_name='Drill Structure',
    description='Structural features in drill holes',
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('bhid', FieldType.STRING, length=100, required=True),
        FieldSchema('depth', FieldType.DOUBLE, required=True),
        FieldSchema('depth_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('structure_type', FieldType.STRING, length=100, required=True),
        FieldSchema('alpha_angle', FieldType.DOUBLE),
        FieldSchema('beta_angle', FieldType.DOUBLE),
        FieldSchema('dip', FieldType.DOUBLE),
        FieldSchema('dip_direction', FieldType.DOUBLE),
        FieldSchema('aperture', FieldType.DOUBLE),
        FieldSchema('infill', FieldType.STRING, length=100),
        FieldSchema('description', FieldType.STRING, length=1000),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
    ]
)

DRILL_MINERALIZATION_SCHEMA = ModelSchema(
    name='DrillMineralization',
    api_endpoint='drill-mineralizations',
    geometry_type=GeometryType.NONE,
    display_name='Drill Mineralization',
    description='Mineralization intervals for drill holes',
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('bhid', FieldType.STRING, length=100, required=True),
        FieldSchema('depth_from', FieldType.DOUBLE, required=True),
        FieldSchema('depth_to', FieldType.DOUBLE, required=True),
        FieldSchema('depth_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('mineral', FieldType.STRING, length=100, required=True),
        FieldSchema('percentage', FieldType.DOUBLE),
        FieldSchema('style', FieldType.STRING, length=100),
        FieldSchema('description', FieldType.STRING, length=1000),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
    ]
)

DRILL_SURVEY_SCHEMA = ModelSchema(
    name='DrillSurvey',
    api_endpoint='drill-surveys',
    geometry_type=GeometryType.NONE,
    display_name='Drill Surveys',
    description='Downhole survey measurements',
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('bhid', FieldType.STRING, length=100, required=True),
        FieldSchema('depth', FieldType.DOUBLE, required=True),
        FieldSchema('depth_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('azimuth', FieldType.DOUBLE, required=True),
        FieldSchema('dip', FieldType.DOUBLE, required=True),
        FieldSchema('survey_method', FieldType.STRING, length=50),
        FieldSchema('magnetic_declination', FieldType.DOUBLE),
        FieldSchema('comments', FieldType.STRING, length=1000),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
    ]
)

DRILL_PHOTO_SCHEMA = ModelSchema(
    name='DrillPhoto',
    api_endpoint='drill-photos',
    geometry_type=GeometryType.NONE,
    display_name='Drill Photos',
    description='Core and chip photos',
    supports_push=False,  # Photos require special handling
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('bhid', FieldType.STRING, length=100, required=True),
        FieldSchema('depth_from', FieldType.DOUBLE),
        FieldSchema('depth_to', FieldType.DOUBLE),
        FieldSchema('depth_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('photo_type', FieldType.STRING, length=50),
        FieldSchema('description', FieldType.STRING, length=1000),
        FieldSchema('image_url', FieldType.STRING, length=500, readonly=True),
        FieldSchema('thumbnail_url', FieldType.STRING, length=500, readonly=True),
        FieldSchema('date_taken', FieldType.DATETIME),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
    ]
)

DRILL_TRACE_SCHEMA = ModelSchema(
    name='DrillTrace',
    api_endpoint='drill-traces',
    geometry_type=GeometryType.LINESTRING,
    display_name='Drill Traces',
    description='Desurveyed 3D drill hole paths',
    supports_push=False,  # Read-only - traces are calculated server-side
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('bhid', FieldType.INTEGER, readonly=True,
                   description='DrillCollar foreign key'),
        FieldSchema('collar_name', FieldType.STRING, length=100, readonly=True,
                   description='Drill hole name from collar'),
        FieldSchema('project', FieldType.INTEGER, readonly=True),
        FieldSchema('project_name', FieldType.STRING, length=100, readonly=True),
        FieldSchema('company_name', FieldType.STRING, length=100, readonly=True),
        FieldSchema('method', FieldType.STRING, length=50, readonly=True,
                   description='Desurvey method used'),
        FieldSchema('resolution_meters', FieldType.DOUBLE, readonly=True,
                   description='Spacing between trace points'),
        FieldSchema('survey_count', FieldType.INTEGER, readonly=True),
        FieldSchema('max_depth', FieldType.DOUBLE, readonly=True),
        FieldSchema('point_count', FieldType.INTEGER, readonly=True),
        FieldSchema('last_calculated', FieldType.DATETIME, readonly=True),
        FieldSchema('needs_recalculation', FieldType.BOOLEAN, readonly=True),
    ]
)


# =============================================================================
# LAND HOLDING MODEL
# =============================================================================

LAND_HOLDING_SCHEMA = ModelSchema(
    name='LandHolding',
    api_endpoint='landholdings',
    geometry_type=GeometryType.MULTIPOLYGON,
    display_name='Land Holdings',
    description='Mining claims and land tenements',
    natural_key_fields=['name', 'project'],
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('name', FieldType.STRING, length=100, required=True),
        FieldSchema('project', FieldType.STRING, length=0, required=True,
                   description='Project natural key (JSON object)'),
        FieldSchema('serial_number', FieldType.STRING, length=100),
        FieldSchema('claim_type', FieldType.STRING, length=50),
        FieldSchema('county', FieldType.STRING, length=100),
        FieldSchema('state', FieldType.STRING, length=100),
        FieldSchema('country', FieldType.STRING, length=100),
        FieldSchema('township', FieldType.STRING, length=50),
        FieldSchema('range', FieldType.STRING, length=50),
        FieldSchema('section', FieldType.STRING, length=50),
        FieldSchema('quarter', FieldType.STRING, length=50),
        FieldSchema('area_acres', FieldType.DOUBLE),
        FieldSchema('area_hectares', FieldType.DOUBLE),
        FieldSchema('date_staked', FieldType.DATE),
        FieldSchema('date_recorded', FieldType.DATE),
        FieldSchema('expiry_date', FieldType.DATE),
        FieldSchema('staked_by', FieldType.STRING, length=100),
        FieldSchema('owner', FieldType.STRING, length=100),
        FieldSchema('dropped', FieldType.BOOLEAN, default=False),
        FieldSchema('land_status', FieldType.STRING, length=255,
                   description='Land holding type/status'),
        FieldSchema('current_retain', FieldType.BOOLEAN, readonly=True),
        FieldSchema('retain_fiscal_year', FieldType.INTEGER, readonly=True),
        FieldSchema('serial_link', FieldType.STRING, length=500, readonly=True),
        FieldSchema('comments', FieldType.STRING, length=1000),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
        FieldSchema('created_by', FieldType.STRING, readonly=True),
        FieldSchema('last_edited_by', FieldType.STRING, readonly=True),
    ]
)


# =============================================================================
# POINT SAMPLE MODEL
# =============================================================================

POINT_SAMPLE_SCHEMA = ModelSchema(
    name='PointSample',
    api_endpoint='point-samples',
    geometry_type=GeometryType.POINT,
    display_name='Point Samples',
    description='Surface geochemical samples',
    natural_key_fields=['name', 'project'],
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        # name is now nullable for planned samples (lab sample ID set in field)
        FieldSchema('name', FieldType.STRING, length=100, required=False,
                   description='Lab sample bag ID (set when collected)'),
        FieldSchema('project', FieldType.STRING, length=0, required=True,
                   description='Project natural key (JSON object)'),
        FieldSchema('sample_type', FieldType.STRING, length=3,
                   description='SL=Soil, RK=Rock Chip, OC=Outcrop, etc.'),
        FieldSchema('ps_type', FieldType.INTEGER,
                   description='FK to PointSampleType (company-specific)'),
        FieldSchema('lithology', FieldType.STRING, length=255,
                   description='FK to Lithology'),
        FieldSchema('alteration', FieldType.STRING, length=255,
                   description='FK to Alteration'),
        # Actual coordinates (where sample was collected - set in field)
        FieldSchema('latitude', FieldType.DOUBLE, required=False,
                   description='Actual collection latitude (set in field)'),
        FieldSchema('longitude', FieldType.DOUBLE, required=False,
                   description='Actual collection longitude (set in field)'),
        FieldSchema('elevation', FieldType.DOUBLE),
        FieldSchema('date_collected', FieldType.DATE),
        FieldSchema('collected_by', FieldType.STRING, length=100),
        FieldSchema('sample_weight', FieldType.DOUBLE),
        FieldSchema('length_units', FieldType.STRING, length=5, default='M'),
        FieldSchema('description', FieldType.STRING, length=1000),
        FieldSchema('comments', FieldType.STRING, length=1000),
        FieldSchema('coordinate_system_metadata', FieldType.STRING, length=0,
                   description='CRS metadata for plugin sync (JSON object)'),
        # === FIELD WORKFLOW FIELDS ===
        # Status tracking
        FieldSchema('status', FieldType.STRING, length=2, default='CO',
                   description='PL=Planned, AS=Assigned, CO=Collected, SK=Skipped'),
        FieldSchema('status_notes', FieldType.STRING, length=1000,
                   description='Reason for skipping, access issues, etc.'),
        # Sequence number for planned samples (display/navigation ID)
        FieldSchema('sequence_number', FieldType.STRING, length=50,
                   description='Planning reference ID (e.g., SS-001)'),
        # Target coordinates (where to go - set during planning)
        FieldSchema('target_latitude', FieldType.DOUBLE,
                   description='Planned collection latitude'),
        FieldSchema('target_longitude', FieldType.DOUBLE,
                   description='Planned collection longitude'),
        FieldSchema('target_elevation', FieldType.DOUBLE,
                   description='Planned collection elevation'),
        FieldSchema('target_epsg', FieldType.INTEGER,
                   description='EPSG code of original target coordinates'),
        # Assignment fields (readonly - managed by server/dashboard)
        FieldSchema('assigned_to', FieldType.STRING, length=100, readonly=True,
                   description='Assigned field worker email'),
        FieldSchema('assigned_by', FieldType.STRING, length=100, readonly=True,
                   description='Manager who made assignment'),
        FieldSchema('assigned_date', FieldType.DATETIME, readonly=True,
                   description='When assignment was made'),
        FieldSchema('due_date', FieldType.DATE, readonly=True,
                   description='Target collection date'),
        # === END FIELD WORKFLOW FIELDS ===
        # Merged assay fields (populated when merge_assays=true)
        FieldSchema('assay_Au', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Ag', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Cu', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Pb', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Zn', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Fe', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_As', FieldType.DOUBLE, readonly=True),
        FieldSchema('assay_Sb', FieldType.DOUBLE, readonly=True),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
        FieldSchema('created_by', FieldType.STRING, readonly=True),
        FieldSchema('last_edited_by', FieldType.STRING, readonly=True),
    ]
)


# =============================================================================
# PHOTO MODEL (Field photos with GPS coordinates)
# =============================================================================

PHOTO_SCHEMA = ModelSchema(
    name='Photo',
    api_endpoint='photos',
    geometry_type=GeometryType.POINT,
    display_name='Photos',
    description='Field photos with GPS coordinates from EXIF or manual entry',
    supports_push=False,  # Pull-only for now (uploads require multipart handling)
    supports_pull=True,
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('original_filename', FieldType.STRING, length=255, readonly=True,
                   description='Original filename of uploaded image'),
        FieldSchema('category', FieldType.STRING, length=3,
                   description='Photo category (DRL=Drill, MAP=Map, FLD=Field, etc.)'),
        FieldSchema('category_display', FieldType.STRING, readonly=True,
                   description='Human-readable category name'),
        FieldSchema('description', FieldType.STRING, length=0,  # 0 = unlimited (TextField)
                   description='Photo description'),
        FieldSchema('latitude', FieldType.DOUBLE,
                   description='GPS latitude (from EXIF or manual)'),
        FieldSchema('longitude', FieldType.DOUBLE,
                   description='GPS longitude (from EXIF or manual)'),
        FieldSchema('elevation', FieldType.DOUBLE,
                   description='GPS elevation in meters'),
        FieldSchema('length_units', FieldType.STRING, length=5, default='M', readonly=True),
        FieldSchema('file_size', FieldType.INTEGER, readonly=True,
                   description='File size in bytes'),
        FieldSchema('image_url', FieldType.STRING, length=500, readonly=True,
                   description='Pre-signed S3 URL for full image'),
        FieldSchema('thumbnail_url', FieldType.STRING, length=500, readonly=True,
                   description='Pre-signed S3 URL for thumbnail (750x750)'),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
        FieldSchema('created_by', FieldType.STRING, readonly=True),
        FieldSchema('last_edited_by', FieldType.STRING, readonly=True),
    ]
)


# =============================================================================
# PROJECT FILE MODEL (GeoTIFFs, DEMs, Rasters)
# =============================================================================

PROJECT_FILE_SCHEMA = ModelSchema(
    name='ProjectFile',
    api_endpoint='project-files',
    geometry_type=GeometryType.NONE,  # Rasters don't have vector geometry
    display_name='Project Files (Rasters)',
    description='GeoTIFFs, DEMs, and other georeferenced raster files',
    supports_push=False,  # TODO: Enable when upload is implemented
    supports_pull=True,
    natural_key_fields=['name', 'project'],
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('name', FieldType.STRING, length=255, required=True,
                   description='Name of the file'),
        FieldSchema('category', FieldType.STRING, length=2, required=True,
                   description='File category (DM=DEM, MG=Magnetics, GL=Geology, etc.)'),
        FieldSchema('category_display', FieldType.STRING, readonly=True,
                   description='Human-readable category name'),
        FieldSchema('description', FieldType.STRING, length=0,  # 0 = unlimited (TextField)
                   description='Description of the file'),
        FieldSchema('file_size', FieldType.INTEGER, readonly=True,
                   description='File size in bytes'),
        FieldSchema('file_url', FieldType.STRING, length=500, readonly=True,
                   description='Pre-signed URL for file download'),
        FieldSchema('is_raster', FieldType.BOOLEAN, default=False,
                   description='Is this file a georeferenced raster?'),
        FieldSchema('crs', FieldType.STRING, length=50,
                   description='Coordinate Reference System (e.g., EPSG:4326)'),
        FieldSchema('bounds', FieldType.STRING, length=255,
                   description='Bounding box as [minX, minY, maxX, maxY]'),
        FieldSchema('resolution', FieldType.DOUBLE,
                   description='Pixel resolution in CRS units'),
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
        FieldSchema('created_by', FieldType.STRING, readonly=True),
        FieldSchema('last_edited_by', FieldType.STRING, readonly=True),
    ]
)


# =============================================================================
# ASSAY RANGE CONFIGURATION MODEL (for visualization)
# =============================================================================

ASSAY_RANGE_CONFIG_SCHEMA = ModelSchema(
    name='AssayRangeConfiguration',
    api_endpoint='assay-range-configurations',
    geometry_type=GeometryType.NONE,
    display_name='Assay Range Config',
    description='Color schemes for assay visualization',
    supports_push=False,  # Read-only for now in QGIS
    fields=[
        FieldSchema('id', FieldType.INTEGER, readonly=True),
        FieldSchema('name', FieldType.STRING, length=100, required=True),
        FieldSchema('element', FieldType.STRING, length=10, required=True,
                   description='Element symbol (Au, Cu, Ag, etc.)'),
        FieldSchema('element_display', FieldType.STRING, readonly=True),
        FieldSchema('units', FieldType.STRING, length=10, readonly=True,
                   description='Derived from assay_merge_settings'),
        FieldSchema('units_display', FieldType.STRING, readonly=True),
        FieldSchema('assay_merge_settings', FieldType.INTEGER, required=True,
                   description='FK to AssayMergeSettings (ID)'),
        FieldSchema('description', FieldType.STRING, length=1000),
        FieldSchema('default_color', FieldType.STRING, length=7, default='#CCCCCC'),
        FieldSchema('is_active', FieldType.BOOLEAN, default=True),
        FieldSchema('enable_compositing', FieldType.BOOLEAN, default=False),
        FieldSchema('composite_interval', FieldType.DOUBLE),
        FieldSchema('composite_alignment_offset', FieldType.DOUBLE),
        # Note: 'ranges' is a nested list returned by API (not a field)
        # Access via response['ranges'] which contains AssayRangeItem objects
        FieldSchema('date_created', FieldType.DATETIME, readonly=True),
        FieldSchema('last_edited', FieldType.DATETIME, readonly=True),
        FieldSchema('created_by', FieldType.STRING, readonly=True),
        FieldSchema('last_edited_by', FieldType.STRING, readonly=True),
    ]
)


# =============================================================================
# REGISTRY
# =============================================================================

# Map of model name to schema
MODEL_SCHEMAS: Dict[str, ModelSchema] = {
    'DrillCollar': DRILL_COLLAR_SCHEMA,
    'DrillSample': DRILL_SAMPLE_SCHEMA,
    'DrillPad': DRILL_PAD_SCHEMA,
    'DrillLithology': DRILL_LITHOLOGY_SCHEMA,
    'DrillAlteration': DRILL_ALTERATION_SCHEMA,
    'DrillStructure': DRILL_STRUCTURE_SCHEMA,
    'DrillMineralization': DRILL_MINERALIZATION_SCHEMA,
    'DrillSurvey': DRILL_SURVEY_SCHEMA,
    'DrillPhoto': DRILL_PHOTO_SCHEMA,
    'DrillTrace': DRILL_TRACE_SCHEMA,
    'LandHolding': LAND_HOLDING_SCHEMA,
    'PointSample': POINT_SAMPLE_SCHEMA,
    'Photo': PHOTO_SCHEMA,
    'ProjectFile': PROJECT_FILE_SCHEMA,
    'AssayRangeConfiguration': ASSAY_RANGE_CONFIG_SCHEMA,
}


# Models that are raster-based (require special handling)
RASTER_MODELS = ['ProjectFile']


def is_raster_model(model_name: str) -> bool:
    """Check if a model is raster-based (requires file download, not vector layer)."""
    return model_name in RASTER_MODELS


def get_schema(model_name: str) -> Optional[ModelSchema]:
    """Get schema for a model by name."""
    return MODEL_SCHEMAS.get(model_name)


def get_all_schemas() -> List[ModelSchema]:
    """Get all model schemas."""
    return list(MODEL_SCHEMAS.values())


def get_pullable_models() -> List[str]:
    """Get model names that support pull operations."""
    return [name for name, schema in MODEL_SCHEMAS.items() if schema.supports_pull]


def get_pushable_models() -> List[str]:
    """Get model names that support push operations."""
    return [name for name, schema in MODEL_SCHEMAS.items() if schema.supports_push]
