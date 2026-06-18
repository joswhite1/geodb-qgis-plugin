# -*- coding: utf-8 -*-
"""SQL identifier safety helpers for the GeodbIO plugin.

SQLite (like every DB-API driver) cannot bind table or column *names* with
``?`` placeholders — only values. When a query must name a table whose
identifier is chosen at runtime (e.g. the metadata table differs between the
legacy QClaims and current geodb GeoPackage formats), the identifier has to
be interpolated into the SQL text.

To keep that safe — and to avoid the f-string-into-execute() pattern that
security scanners flag as a SQL-injection vector — route every such name
through ``quote_identifier``: it rejects anything that is not a bare SQL
identifier and returns the name double-quoted for literal use in the query.
Validating up front means a malformed/hostile name raises instead of
reaching the database.
"""
import re

# A bare SQL identifier: letter/underscore start, then letters/digits/_,
# capped at a sane length. No spaces, quotes, semicolons, or punctuation —
# so nothing that could break out of the identifier position survives.
_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')


def quote_identifier(name):
    """Validate ``name`` is a plain SQL identifier and return it double-quoted.

    Args:
        name: The table or column name to use literally in a query.

    Returns:
        The identifier wrapped in double quotes, ready to splice into SQL.

    Raises:
        ValueError: If ``name`` is not a bare SQL identifier.
    """
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f'"{name}"'
