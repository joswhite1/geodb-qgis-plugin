# -*- coding: utf-8 -*-
"""
Photo caching utility for FieldNote photos.

Provides a simple file-based cache for downloaded photos to improve
performance and reduce network requests when viewing the same photos
multiple times.
"""

import os
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from qgis.core import QgsApplication

from .logger import PluginLogger


class PhotoCache:
    """
    Simple file-based photo cache.

    Stores downloaded photos locally with URL-based hashing for
    fast lookups. Supports cache expiration and size limits.
    """

    # Default cache settings
    DEFAULT_MAX_SIZE_MB = 500  # Maximum cache size in MB
    DEFAULT_MAX_AGE_DAYS = 30  # Maximum age of cached files

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_size_mb: int = DEFAULT_MAX_SIZE_MB,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS
    ):
        """
        Initialize the photo cache.

        Args:
            cache_dir: Custom cache directory (defaults to QGIS settings dir)
            max_size_mb: Maximum cache size in megabytes
            max_age_days: Maximum age of cached files in days
        """
        self.logger = PluginLogger.get_logger()

        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            settings_dir = Path(QgsApplication.qgisSettingsDirPath())
            self.cache_dir = settings_dir / 'geodb_cache' / 'photos'

        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_age_days = max_age_days

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"PhotoCache initialized at {self.cache_dir}")

    def _url_to_filename(self, url: str) -> str:
        """
        Convert URL to a cache filename using MD5 hash.

        Args:
            url: Image URL

        Returns:
            Cache filename (hash + extension)
        """
        url_hash = hashlib.md5(url.encode()).hexdigest()

        # Try to preserve original extension
        ext = '.png'
        url_lower = url.lower()
        if '.jpg' in url_lower or '.jpeg' in url_lower:
            ext = '.jpg'
        elif '.gif' in url_lower:
            ext = '.gif'
        elif '.webp' in url_lower:
            ext = '.webp'

        return f"{url_hash}{ext}"

    def get_cache_path(self, url: str) -> Path:
        """
        Get the cache file path for a URL.

        Args:
            url: Image URL

        Returns:
            Path to cache file (may not exist yet)
        """
        filename = self._url_to_filename(url)
        return self.cache_dir / filename

    def has_cached(self, url: str) -> bool:
        """
        Check if a URL has a valid cached file.

        Args:
            url: Image URL

        Returns:
            True if cached file exists and is valid
        """
        cache_path = self.get_cache_path(url)

        if not cache_path.exists():
            return False

        # Check if file is expired
        if self._is_expired(cache_path):
            return False

        return True

    def get_cached(self, url: str) -> Optional[Path]:
        """
        Get cached file path if it exists and is valid.

        Args:
            url: Image URL

        Returns:
            Path to cached file if valid, None otherwise
        """
        if self.has_cached(url):
            return self.get_cache_path(url)
        return None

    def cache_data(self, url: str, data: bytes) -> Path:
        """
        Cache image data for a URL.

        Args:
            url: Image URL
            data: Image binary data

        Returns:
            Path to cached file
        """
        cache_path = self.get_cache_path(url)

        # Write data to cache file
        with open(cache_path, 'wb') as f:
            f.write(data)

        self.logger.debug(f"Cached photo: {url} -> {cache_path.name}")

        return cache_path

    def _is_expired(self, path: Path) -> bool:
        """
        Check if a cached file has expired.

        Args:
            path: Path to cache file

        Returns:
            True if file is expired
        """
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            age = datetime.now() - mtime
            return age > timedelta(days=self.max_age_days)
        except Exception:
            return True

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        total_size = 0
        file_count = 0
        oldest_file = None
        newest_file = None

        try:
            for path in self.cache_dir.glob('*'):
                if path.is_file():
                    file_count += 1
                    size = path.stat().st_size
                    total_size += size
                    mtime = datetime.fromtimestamp(path.stat().st_mtime)

                    if oldest_file is None or mtime < oldest_file:
                        oldest_file = mtime
                    if newest_file is None or mtime > newest_file:
                        newest_file = mtime
        except Exception as e:
            self.logger.error(f"Error getting cache stats: {e}")

        return {
            'total_size_mb': total_size / (1024 * 1024),
            'file_count': file_count,
            'cache_dir': str(self.cache_dir),
            'oldest_file': oldest_file.isoformat() if oldest_file else None,
            'newest_file': newest_file.isoformat() if newest_file else None,
            'max_size_mb': self.max_size_bytes / (1024 * 1024),
        }

    def cleanup(self, force: bool = False) -> int:
        """
        Clean up expired and excess cached files.

        Args:
            force: If True, clean up even if under size limit

        Returns:
            Number of files removed
        """
        removed_count = 0

        try:
            # Get all cached files with stats
            files: list[tuple[Path, float, int]] = []
            total_size = 0

            for path in self.cache_dir.glob('*'):
                if path.is_file():
                    stat = path.stat()
                    files.append((path, stat.st_mtime, stat.st_size))
                    total_size += stat.st_size

            # Remove expired files first
            for path, mtime, size in files[:]:
                if self._is_expired(path):
                    try:
                        path.unlink()
                        total_size -= size
                        files.remove((path, mtime, size))
                        removed_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to remove expired cache file: {e}")

            # If still over size limit, remove oldest files
            if total_size > self.max_size_bytes or force:
                # Sort by modification time (oldest first)
                files.sort(key=lambda x: x[1])

                while total_size > self.max_size_bytes * 0.8 and files:  # Clean to 80%
                    path, mtime, size = files.pop(0)
                    try:
                        path.unlink()
                        total_size -= size
                        removed_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to remove cache file: {e}")

            if removed_count > 0:
                self.logger.info(f"Cache cleanup: removed {removed_count} files")

        except Exception as e:
            self.logger.error(f"Cache cleanup error: {e}")

        return removed_count

    def clear(self) -> int:
        """
        Clear all cached files.

        Returns:
            Number of files removed
        """
        removed_count = 0

        try:
            for path in self.cache_dir.glob('*'):
                if path.is_file():
                    try:
                        path.unlink()
                        removed_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to remove cache file: {e}")

            self.logger.info(f"Cache cleared: removed {removed_count} files")

        except Exception as e:
            self.logger.error(f"Cache clear error: {e}")

        return removed_count


# Global cache instance
_cache_instance: Optional[PhotoCache] = None


def get_photo_cache() -> PhotoCache:
    """
    Get the global photo cache instance.

    Returns:
        PhotoCache instance
    """
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = PhotoCache()
    return _cache_instance


def clear_photo_cache() -> int:
    """
    Clear the global photo cache.

    Returns:
        Number of files removed
    """
    cache = get_photo_cache()
    return cache.clear()
