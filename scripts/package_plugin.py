#!/usr/bin/env python3
"""
Package the geodb.io QGIS plugin for distribution.

This script creates a properly structured ZIP file ready for upload
to the QGIS Plugin Repository (https://plugins.qgis.org/).

Usage:
    python scripts/package_plugin.py

Output:
    Creates geodb.zip in the parent directory (QPlugin/devel/)
"""

import os
import zipfile
from pathlib import Path


# Directories to include in the package
INCLUDE_DIRS = [
    'api',
    'docs',
    'help',
    'i18n',
    'icons',
    'managers',
    'models',
    'processors',
    'symbols',  # Geological structure symbols (SVG)
    'ui',
    'utils',
]

# Files to include in the package root
INCLUDE_FILES = [
    '__init__.py',
    'plugin.py',
    'metadata.txt',
    'icon.png',
    'resources.py',
    'resources_rc.py',
    'LICENSE',
    'README.md',
]

# File patterns to exclude
EXCLUDE_PATTERNS = [
    '__pycache__',
    '.pyc',
    '.pyo',
    '.git',
    '.DS_Store',
    'Thumbs.db',
    '*.tmp',
    '*.bak',
    '~',
    '.devmode',  # Dev mode flag - must never be included in releases
]


def should_exclude(path: Path) -> bool:
    """Check if a file/directory should be excluded."""
    name = path.name
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith('*'):
            if name.endswith(pattern[1:]):
                return True
        elif pattern in str(path):
            return True
        elif name == pattern:
            return True
        elif name.endswith(pattern):
            return True
    return False


def get_version_from_metadata(plugin_dir: Path) -> str:
    """Extract version from metadata.txt."""
    metadata_path = plugin_dir / 'metadata.txt'
    with open(metadata_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('version='):
                return line.split('=', 1)[1].strip()
    return 'unknown'


def package_plugin():
    """Create the plugin ZIP package."""
    # Determine paths
    script_dir = Path(__file__).parent
    plugin_dir = script_dir.parent  # geodb/
    output_dir = plugin_dir.parent  # devel/

    # Get version for filename
    version = get_version_from_metadata(plugin_dir)

    # Output filename (QGIS expects the ZIP to contain a folder matching plugin name)
    zip_filename = f'geodb_{version}.zip'
    zip_path = output_dir / zip_filename

    # Also create a simple geodb.zip for easy upload
    simple_zip_path = output_dir / 'geodb.zip'

    print(f"Packaging geodb.io plugin v{version}")
    print(f"Source: {plugin_dir}")
    print(f"Output: {zip_path}")
    print()

    files_added = 0

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add root files
        for filename in INCLUDE_FILES:
            filepath = plugin_dir / filename
            if filepath.exists():
                arcname = f'geodb/{filename}'
                zf.write(filepath, arcname)
                print(f"  Added: {arcname}")
                files_added += 1
            else:
                print(f"  WARNING: Missing file: {filename}")

        # Add directories
        for dirname in INCLUDE_DIRS:
            dirpath = plugin_dir / dirname
            if dirpath.exists():
                for root, dirs, files in os.walk(dirpath):
                    root_path = Path(root)

                    # Filter out excluded directories
                    dirs[:] = [d for d in dirs if not should_exclude(root_path / d)]

                    for file in files:
                        filepath = root_path / file
                        if not should_exclude(filepath):
                            # Calculate archive path relative to plugin dir
                            rel_path = filepath.relative_to(plugin_dir)
                            arcname = f'geodb/{rel_path}'
                            zf.write(filepath, arcname)
                            print(f"  Added: {arcname}")
                            files_added += 1
            else:
                print(f"  WARNING: Missing directory: {dirname}")

    print()
    print(f"Package created successfully!")
    print(f"  Files: {files_added}")
    print(f"  Size: {zip_path.stat().st_size / 1024:.1f} KB")
    print(f"  Output: {zip_path}")

    # Create a copy without version for easy upload
    import shutil
    shutil.copy(zip_path, simple_zip_path)
    print(f"  Copy: {simple_zip_path}")

    print()
    print("Next steps:")
    print("  1. Go to https://plugins.qgis.org/")
    print("  2. Log in (or create an account)")
    print("  3. Click 'Share a plugin'")
    print("  4. Upload the ZIP file")
    print()

    return zip_path


if __name__ == '__main__':
    package_plugin()
