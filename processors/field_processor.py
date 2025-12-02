# -*- coding: utf-8 -*-
"""
Field mapping and type conversion between API and QGIS.
"""
from typing import Any, Dict, List, Optional
from qgis.core import QgsField, QgsFields
from qgis.PyQt.QtCore import QVariant

from ..api.exceptions import FieldMappingError
from ..utils.logger import PluginLogger


class FieldProcessor:
    """
    Handles field mapping and data type conversion.
    """
    
    # Field type mapping: API type -> QGIS type
    TYPE_MAPPING = {
        'string': QVariant.String,
        'text': QVariant.String,
        'integer': QVariant.Int,
        'decimal': QVariant.Double,
        'float': QVariant.Double,
        'boolean': QVariant.Bool,
        'date': QVariant.Date,
        'datetime': QVariant.DateTime,
        'time': QVariant.Time
    }
    
    # Read-only fields that should not be edited
    READONLY_FIELDS = [
        'id',
        'created_at',
        'updated_at',
        'created_by',
        'updated_by'
    ]
    
    def __init__(self):
        """Initialize field processor."""
        self.logger = PluginLogger.get_logger()
    
    def create_qgs_fields(self, field_definitions: List[Dict[str, Any]]) -> QgsFields:
        """
        Create QGIS fields from API field definitions.
        
        Args:
            field_definitions: List of field definition dicts
                [{'name': 'field1', 'type': 'string', 'length': 255}, ...]
            
        Returns:
            QgsFields object
        """
        qgs_fields = QgsFields()
        
        for field_def in field_definitions:
            field_name = field_def.get('name')
            field_type = field_def.get('type', 'string')
            field_length = field_def.get('length', 255)
            
            # Map API type to QGIS type
            qgs_type = self.TYPE_MAPPING.get(field_type, QVariant.String)
            
            # Create field
            qgs_field = QgsField(field_name, qgs_type)

            # Set length for string fields
            # -1 or 0 means unlimited (for GeoPackage TEXT fields)
            # Positive value sets explicit length limit
            if qgs_type == QVariant.String:
                if field_length <= 0:
                    # Use 0 for unlimited length (GeoPackage TEXT)
                    qgs_field.setLength(0)
                else:
                    qgs_field.setLength(field_length)

            qgs_fields.append(qgs_field)
        
        return qgs_fields
    
    def api_to_qgs_value(self, value: Any, field_type: str) -> Any:
        """
        Convert API value to QGIS-compatible value.
        
        Args:
            value: Value from API
            field_type: API field type
            
        Returns:
            Converted value
        """
        if value is None:
            return None
        
        try:
            if field_type in ['integer']:
                return int(value)
            elif field_type in ['decimal', 'float']:
                return float(value)
            elif field_type == 'boolean':
                return bool(value)
            else:
                return str(value)
        except (ValueError, TypeError) as e:
            self.logger.warning(f"Failed to convert value {value} to {field_type}: {e}")
            return value
    
    def qgs_to_api_value(self, value: Any, field_type: str) -> Any:
        """
        Convert QGIS value to API-compatible value.

        Args:
            value: Value from QGIS
            field_type: API field type

        Returns:
            Converted value
        """
        if value is None or value == '':
            return None

        # Don't convert dicts/lists - they're already in API format (natural keys)
        if isinstance(value, (dict, list)):
            return value

        try:
            if field_type in ['integer']:
                return int(value)
            elif field_type in ['decimal', 'float']:
                return float(value)
            elif field_type == 'boolean':
                return bool(value)
            elif field_type in ['date', 'datetime', 'time']:
                # Convert QDate/QDateTime to ISO string
                if hasattr(value, 'toString'):
                    return value.toString('yyyy-MM-dd HH:mm:ss')
                return str(value)
            else:
                return str(value)
        except (ValueError, TypeError) as e:
            self.logger.warning(f"Failed to convert value {value} to {field_type}: {e}")
            return value
    
    def is_readonly_field(self, field_name: str) -> bool:
        """
        Check if field is read-only.
        
        Args:
            field_name: Field name
            
        Returns:
            True if field is read-only
        """
        return field_name in self.READONLY_FIELDS
    
    def extract_attributes(
        self,
        feature_data: Dict[str, Any],
        field_definitions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Extract and convert attributes from API feature data.

        Args:
            feature_data: Feature data from API
            field_definitions: Field definitions

        Returns:
            Dictionary of field name -> converted value
        """
        import json

        attributes = {}

        for field_def in field_definitions:
            field_name = field_def.get('name')
            field_type = field_def.get('type', 'string')

            # Get value from feature data
            value = feature_data.get(field_name)

            # For natural key fields, convert dict to JSON string
            if field_name in self.NATURAL_KEY_FIELDS and isinstance(value, dict):
                value = json.dumps(value)
            # For list fields (like retain_records), convert to JSON string
            elif isinstance(value, list):
                value = json.dumps(value)

            # Convert to QGIS value
            converted_value = self.api_to_qgs_value(value, field_type)
            attributes[field_name] = converted_value

        return attributes
    
    # Fields that should be parsed as JSON objects (natural keys)
    NATURAL_KEY_FIELDS = {
        'project', 'land_status', 'bhid', 'lithology', 'alteration',
        'mineralization', 'structure', 'company'
    }

    def prepare_for_push(
        self,
        attributes: Dict[str, Any],
        field_definitions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Prepare attributes for pushing to API.

        Args:
            attributes: Attributes from QGIS feature
            field_definitions: Field definitions

        Returns:
            Dictionary ready for API
        """
        import json

        prepared = {}

        for field_def in field_definitions:
            field_name = field_def.get('name')
            field_type = field_def.get('type', 'string')

            # Skip read-only fields
            if self.is_readonly_field(field_name):
                # Include id if present (needed for updates)
                if field_name == 'id' and field_name in attributes:
                    prepared[field_name] = attributes[field_name]
                continue

            # Get value
            value = attributes.get(field_name)

            # Handle natural key fields - parse JSON strings back to objects
            if field_name in self.NATURAL_KEY_FIELDS and isinstance(value, str):
                value = self._parse_natural_key(value)

            # Convert to API value
            converted_value = self.qgs_to_api_value(value, field_type)
            prepared[field_name] = converted_value

        return prepared

    def _parse_natural_key(self, value: str) -> Any:
        """
        Parse a natural key from string representation.

        Handles both JSON format and Python dict repr format.

        Args:
            value: String that may contain a natural key object

        Returns:
            Parsed dict or original value if parsing fails
        """
        import json
        import ast

        if not value or not isinstance(value, str):
            return value

        value = value.strip()
        if not value:
            return None

        # Try JSON first (handles {"name": "...", "company": "..."})
        if value.startswith('{'):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass

            # Try Python literal eval (handles {'name': '...', 'company': '...'})
            try:
                result = ast.literal_eval(value)
                if isinstance(result, dict):
                    return result
            except (ValueError, SyntaxError):
                pass

        return value