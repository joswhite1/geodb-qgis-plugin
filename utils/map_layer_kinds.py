"""The kinds a geoDB map layer can be (usability Q-1, 2026-10-27).

MIRROR of the server's list: ``geodb/geodata/models/model_variables.py``
``vector_layer_kinds`` (the ``VectorLayer.kind`` choices, served by no read
endpoint). The server refuses an unknown kind on push (400), so when the
server list changes, change this one in the same release. Order and labels
are the server's.
"""

MAP_LAYER_KINDS = (
    ('geology_unit', 'Geology — Map Unit'),
    ('geology_contact', 'Geology — Contact'),
    ('geology_fault', 'Geology — Fault'),
    ('alteration_zone', 'Alteration Zone'),
    ('vein', 'Vein'),
    ('mineralization', 'Mineralization'),
    ('structure', 'Structure'),
    ('outcrop', 'Outcrop'),
    ('target', 'Target'),
    ('anomaly', 'Anomaly'),
    ('survey_grid', 'Survey Grid'),
    ('infrastructure', 'Infrastructure'),
    ('annotation', 'Annotation'),
    ('contours', 'Contours / Topolines'),
    ('other', 'Other'),
)

#: What each geometry choice in "New geoDB map layer…" creates, and the kind
#: it suggests first. Single parts, 2D: a traced sketch has no elevation, and
#: a Z the user never set would be invented data.
MAP_LAYER_GEOMETRIES = (
    ('line', 'Lines (contacts, faults, veins…)', 'LineString', 'geology_contact'),
    ('polygon', 'Areas (map units, alteration zones, targets…)', 'Polygon', 'geology_unit'),
    ('point', 'Points (outcrops, annotations…)', 'Point', 'outcrop'),
)


def kind_label(code):
    """The label of a kind code, or the code itself if unknown."""
    for value, label in MAP_LAYER_KINDS:
        if value == code:
            return label
    return code or ''
