# -*- coding: utf-8 -*-
"""
Qt/QGIS version compatibility utilities.

QGIS 3.30+ (Qt6) uses QMetaType.Type for field types.
Older versions (Qt5) use QVariant.Type.
This module provides a unified set of type constants that work on both.
"""

try:
    from qgis.PyQt.QtCore import QMetaType
    # Qt6 / QGIS 3.30+
    FieldType_QString = QMetaType.Type.QString
    FieldType_Int = QMetaType.Type.Int
    FieldType_Double = QMetaType.Type.Double
    FieldType_Bool = QMetaType.Type.Bool
    FieldType_QDate = QMetaType.Type.QDate
    FieldType_QDateTime = QMetaType.Type.QDateTime
    FieldType_QTime = QMetaType.Type.QTime
    FieldType_LongLong = QMetaType.Type.LongLong
except (ImportError, AttributeError):
    from PyQt5.QtCore import QVariant
    # Qt5 / QGIS < 3.30
    FieldType_QString = QVariant.String
    FieldType_Int = QVariant.Int
    FieldType_Double = QVariant.Double
    FieldType_Bool = QVariant.Bool
    FieldType_QDate = QVariant.Date
    FieldType_QDateTime = QVariant.DateTime
    FieldType_QTime = QVariant.Time
    FieldType_LongLong = QVariant.LongLong
