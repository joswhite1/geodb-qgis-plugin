# -*- coding: utf-8 -*-
"""
Cache for custom field schemas to avoid repeated API calls.

Custom field schemas are fetched once per project/model combination
and cached for 5 minutes. This balances freshness vs performance since
schema changes are infrequent (admin action).
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta


# Cache structure: {(project_id, model_type): (fields_list, timestamp)}
_schema_cache: Dict[Tuple[int, str], Tuple[list, datetime]] = {}

# Cache TTL - refresh after 5 minutes
CACHE_TTL = timedelta(minutes=5)


def get_cached_custom_fields(project_id: int, model_type: str) -> Optional[list]:
    """
    Get cached custom fields if not expired.

    Args:
        project_id: Project ID
        model_type: Model type (e.g., 'DrillCollar')

    Returns:
        List of FieldSchema objects, or None if not cached/expired
    """
    key = (project_id, model_type)

    if key in _schema_cache:
        fields, timestamp = _schema_cache[key]
        if datetime.now() - timestamp < CACHE_TTL:
            return fields

    return None


def set_cached_custom_fields(project_id: int, model_type: str, fields: list):
    """
    Cache custom fields for a project/model combination.

    Args:
        project_id: Project ID
        model_type: Model type (e.g., 'DrillCollar')
        fields: List of FieldSchema objects to cache
    """
    key = (project_id, model_type)
    _schema_cache[key] = (fields, datetime.now())


def clear_cache(project_id: Optional[int] = None):
    """
    Clear cache for a specific project or all projects.

    Call this when:
    - User logs out/changes project
    - Schema is known to have changed (e.g., after schema edit in web UI)
    - Manual refresh is requested

    Args:
        project_id: Specific project to clear, or None for all
    """
    global _schema_cache

    if project_id is None:
        _schema_cache = {}
    else:
        keys_to_remove = [k for k in _schema_cache if k[0] == project_id]
        for key in keys_to_remove:
            del _schema_cache[key]


def get_cache_info() -> Dict[str, any]:
    """
    Get cache statistics for debugging.

    Returns:
        Dict with cache entries count and details
    """
    now = datetime.now()
    entries = []
    for (project_id, model_type), (fields, timestamp) in _schema_cache.items():
        age_seconds = (now - timestamp).total_seconds()
        expired = age_seconds > CACHE_TTL.total_seconds()
        entries.append({
            'project_id': project_id,
            'model_type': model_type,
            'field_count': len(fields),
            'age_seconds': round(age_seconds, 1),
            'expired': expired,
        })

    return {
        'total_entries': len(_schema_cache),
        'ttl_seconds': CACHE_TTL.total_seconds(),
        'entries': entries,
    }
