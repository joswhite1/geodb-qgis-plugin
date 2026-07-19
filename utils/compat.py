# -*- coding: utf-8 -*-
"""
Qt/QGIS version compatibility utilities.

Handles three different QgsField type argument conventions:
- QGIS 3.38+ / 4.0 (Qt6): QgsField expects QMetaType instances
- QGIS 3.30-3.36 (Qt6): QgsField expects QMetaType.Type enums
- QGIS < 3.30 (Qt5): QgsField expects QVariant.Type enums

Also provides cross-version Qt enum constants that work on both Qt5 and Qt6.
In Qt6 (QGIS 4.0+), unscoped enums like Qt.Checked moved to Qt.CheckState.Checked.
"""

import sys
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QSizePolicy, QFrame

# Debug logging — logs to QGIS message log
def _compat_log(msg):
    """Log compat module debug info to QGIS message log."""
    try:
        from qgis.core import QgsMessageLog, Qgis
        QgsMessageLog.logMessage(f"[compat] {msg}", 'GeodbIO', Qgis.Info)
    except Exception:
        pass

# ============================================================
# QgsField type constants
# ============================================================
_compat_log(f"Python {sys.version}")

try:
    from qgis.PyQt.QtCore import QMetaType
    _compat_log(f"QMetaType available — Qt6 path")
    _compat_log(f"QMetaType.Type.QString = {QMetaType.Type.QString!r} (type: {type(QMetaType.Type.QString).__name__})")

    from qgis.core import QgsField, Qgis
    _qgis_version = getattr(Qgis, 'version', lambda: 'unknown')
    if callable(_qgis_version):
        _compat_log(f"QGIS version: {_qgis_version()}")
    else:
        _compat_log(f"QGIS version: {_qgis_version}")

    # Qt6 path — but QgsField signature changed between QGIS 3.36 and 3.38.
    # Probe which form works by trying to construct a test QgsField.
    try:
        # QGIS 3.38+: expects QMetaType instances
        _test_type = QMetaType(QMetaType.Type.QString)
        _compat_log(f"QMetaType instance: {_test_type!r} (type: {type(_test_type).__name__})")
        _test_field = QgsField("_compat_test", _test_type)
        _compat_log(f"QgsField probe SUCCESS with QMetaType instance")
        FieldType_QString = QMetaType(QMetaType.Type.QString)
        FieldType_Int = QMetaType(QMetaType.Type.Int)
        FieldType_Double = QMetaType(QMetaType.Type.Double)
        FieldType_Bool = QMetaType(QMetaType.Type.Bool)
        FieldType_QDate = QMetaType(QMetaType.Type.QDate)
        FieldType_QDateTime = QMetaType(QMetaType.Type.QDateTime)
        FieldType_QTime = QMetaType(QMetaType.Type.QTime)
        FieldType_LongLong = QMetaType(QMetaType.Type.LongLong)
        _compat_log("Using QMetaType INSTANCES for QgsField")
    except Exception as _e1:
        _compat_log(f"QMetaType instance probe FAILED: {type(_e1).__name__}: {_e1}")
        try:
            # QGIS 3.30-3.36: expects QMetaType.Type enums directly
            _test_field = QgsField("_compat_test", QMetaType.Type.QString)
            _compat_log(f"QgsField probe SUCCESS with QMetaType.Type enum")
            FieldType_QString = QMetaType.Type.QString
            FieldType_Int = QMetaType.Type.Int
            FieldType_Double = QMetaType.Type.Double
            FieldType_Bool = QMetaType.Type.Bool
            FieldType_QDate = QMetaType.Type.QDate
            FieldType_QDateTime = QMetaType.Type.QDateTime
            FieldType_QTime = QMetaType.Type.QTime
            FieldType_LongLong = QMetaType.Type.LongLong
            _compat_log("Using QMetaType.Type ENUMS for QgsField")
        except Exception as _e2:
            _compat_log(f"QMetaType.Type enum probe ALSO FAILED: {type(_e2).__name__}: {_e2}")
            # Last resort for Qt6: try QVariant if available
            try:
                from qgis.PyQt.QtCore import QVariant
                _test_field = QgsField("_compat_test", QVariant.String)
                _compat_log("QgsField probe SUCCESS with QVariant.Type (Qt6 fallback)")
                FieldType_QString = QVariant.String
                FieldType_Int = QVariant.Int
                FieldType_Double = QVariant.Double
                FieldType_Bool = QVariant.Bool
                FieldType_QDate = QVariant.Date
                FieldType_QDateTime = QVariant.DateTime
                FieldType_QTime = QVariant.Time
                FieldType_LongLong = QVariant.LongLong
                _compat_log("Using QVariant.Type for QgsField (Qt6 with QVariant)")
            except Exception as _e3:
                _compat_log(f"QVariant probe ALSO FAILED: {type(_e3).__name__}: {_e3}")
                # Final fallback: string type names (some Mac QGIS builds)
                try:
                    _test_field = QgsField("_compat_test", typeName="QString")
                    _compat_log("QgsField probe SUCCESS with string typeName")
                    FieldType_QString = "QString"
                    FieldType_Int = "Integer"
                    FieldType_Double = "Real"
                    FieldType_Bool = "Boolean"
                    FieldType_QDate = "Date"
                    FieldType_QDateTime = "DateTime"
                    FieldType_QTime = "Time"
                    FieldType_LongLong = "Integer64"
                    _compat_log("Using string type names for QgsField")
                except Exception as _e4:
                    _compat_log(f"ALL QgsField probes FAILED. Last: {type(_e4).__name__}: {_e4}")
                    raise _e4

except (ImportError, AttributeError) as _e_import:
    _compat_log(f"QMetaType not available ({type(_e_import).__name__}: {_e_import}) — Qt5 path")
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
    _compat_log("Using QVariant.Type for QgsField (Qt5)")

_compat_log(f"FieldType_QString = {FieldType_QString!r} (type: {type(FieldType_QString).__name__})")


# ============================================================
# Qt enum compatibility helpers
# In Qt6 (QGIS 4.0), unscoped enums moved under scoped enum classes.
# These helpers resolve the correct value for both Qt5 and Qt6.
# ============================================================
def _qt(unscoped, scoped):
    """Resolve a Qt enum: try unscoped (Qt5) first, fall back to scoped (Qt6)."""
    val = getattr(Qt, unscoped, None)
    if val is not None:
        return val
    # scoped form e.g. "CheckState.Checked"
    parts = scoped.split('.')
    obj = Qt
    for part in parts:
        obj = getattr(obj, part)
    return obj


def _qframe(unscoped, scoped):
    """Resolve a QFrame enum for Qt5/Qt6."""
    val = getattr(QFrame, unscoped, None)
    if val is not None:
        return val
    parts = scoped.split('.')
    obj = QFrame
    for part in parts:
        obj = getattr(obj, part)
    return obj


def _qpolicy(unscoped):
    """Resolve a QSizePolicy enum for Qt5/Qt6."""
    val = getattr(QSizePolicy, unscoped, None)
    if val is not None:
        return val
    return getattr(QSizePolicy.Policy, unscoped)


def _qabstractitemview(unscoped, scoped):
    """Resolve a QAbstractItemView enum for Qt5/Qt6."""
    from qgis.PyQt.QtWidgets import QAbstractItemView
    val = getattr(QAbstractItemView, unscoped, None)
    if val is not None:
        return val
    parts = scoped.split('.')
    obj = QAbstractItemView
    for part in parts:
        obj = getattr(obj, part)
    return obj


def _qheaderview(unscoped, scoped):
    """Resolve a QHeaderView enum for Qt5/Qt6."""
    from qgis.PyQt.QtWidgets import QHeaderView
    val = getattr(QHeaderView, unscoped, None)
    if val is not None:
        return val
    parts = scoped.split('.')
    obj = QHeaderView
    for part in parts:
        obj = getattr(obj, part)
    return obj


# --- CheckState ---
Qt_Checked = _qt('Checked', 'CheckState.Checked')
Qt_Unchecked = _qt('Unchecked', 'CheckState.Unchecked')

# --- Orientation ---
Qt_Horizontal = _qt('Horizontal', 'Orientation.Horizontal')
Qt_Vertical = _qt('Vertical', 'Orientation.Vertical')

# --- ScrollBarPolicy ---
Qt_ScrollBarAlwaysOff = _qt('ScrollBarAlwaysOff', 'ScrollBarPolicy.ScrollBarAlwaysOff')
Qt_ScrollBarAsNeeded = _qt('ScrollBarAsNeeded', 'ScrollBarPolicy.ScrollBarAsNeeded')

# --- PenStyle ---
Qt_DashLine = _qt('DashLine', 'PenStyle.DashLine')
Qt_SolidLine = _qt('SolidLine', 'PenStyle.SolidLine')

# --- CursorShape ---
Qt_PointingHandCursor = _qt('PointingHandCursor', 'CursorShape.PointingHandCursor')
Qt_CrossCursor = _qt('CrossCursor', 'CursorShape.CrossCursor')
Qt_SizeAllCursor = _qt('SizeAllCursor', 'CursorShape.SizeAllCursor')

# --- ArrowType ---
Qt_RightArrow = _qt('RightArrow', 'ArrowType.RightArrow')
Qt_DownArrow = _qt('DownArrow', 'ArrowType.DownArrow')

# --- MouseButton ---
Qt_LeftButton = _qt('LeftButton', 'MouseButton.LeftButton')

# --- Key ---
Qt_Key_Escape = _qt('Key_Escape', 'Key.Key_Escape')
Qt_Key_Left = _qt('Key_Left', 'Key.Key_Left')
Qt_Key_Right = _qt('Key_Right', 'Key.Key_Right')
Qt_Key_Space = _qt('Key_Space', 'Key.Key_Space')

# --- AspectRatioMode ---
Qt_KeepAspectRatio = _qt('KeepAspectRatio', 'AspectRatioMode.KeepAspectRatio')

# --- TransformationMode ---
Qt_SmoothTransformation = _qt('SmoothTransformation', 'TransformationMode.SmoothTransformation')

# --- AlignmentFlag ---
Qt_AlignCenter = _qt('AlignCenter', 'AlignmentFlag.AlignCenter')

# --- ItemDataRole ---
Qt_UserRole = _qt('UserRole', 'ItemDataRole.UserRole')

# --- QFrame ---
QFrame_NoFrame = _qframe('NoFrame', 'Shape.NoFrame')
QFrame_HLine = _qframe('HLine', 'Shape.HLine')
QFrame_Sunken = _qframe('Sunken', 'Shadow.Sunken')

# --- QSizePolicy ---
QSizePolicy_Preferred = _qpolicy('Preferred')
QSizePolicy_Expanding = _qpolicy('Expanding')
QSizePolicy_Fixed = _qpolicy('Fixed')

# --- QAbstractItemView ---
QAbstractItemView_NoEditTriggers = _qabstractitemview('NoEditTriggers', 'EditTrigger.NoEditTriggers')
QAbstractItemView_SelectRows = _qabstractitemview('SelectRows', 'SelectionBehavior.SelectRows')
QAbstractItemView_SingleSelection = _qabstractitemview('SingleSelection', 'SelectionMode.SingleSelection')
QAbstractItemView_ExtendedSelection = _qabstractitemview('ExtendedSelection', 'SelectionMode.ExtendedSelection')

# --- QHeaderView ---
QHeaderView_Stretch = _qheaderview('Stretch', 'ResizeMode.Stretch')
QHeaderView_ResizeToContents = _qheaderview('ResizeToContents', 'ResizeMode.ResizeToContents')
QHeaderView_Fixed = _qheaderview('Fixed', 'ResizeMode.Fixed')

# --- QTextCursor ---
def _qtextcursor(unscoped, scoped):
    """Resolve a QTextCursor enum for Qt5/Qt6."""
    from qgis.PyQt.QtGui import QTextCursor
    val = getattr(QTextCursor, unscoped, None)
    if val is not None:
        return val
    parts = scoped.split('.')
    obj = QTextCursor
    for part in parts:
        obj = getattr(obj, part)
    return obj

QTextCursor_End = _qtextcursor('End', 'MoveOperation.End')


# --- Generic helper for any Qt class ---
def _qenum(cls, unscoped, scoped):
    """Resolve any Qt class enum for Qt5/Qt6."""
    val = getattr(cls, unscoped, None)
    if val is not None:
        return val
    parts = scoped.split('.')
    obj = cls
    for part in parts:
        obj = getattr(obj, part)
    return obj


# --- QDialog ---
from qgis.PyQt.QtWidgets import QDialog
QDialog_Accepted = _qenum(QDialog, 'Accepted', 'DialogCode.Accepted')
QDialog_Rejected = _qenum(QDialog, 'Rejected', 'DialogCode.Rejected')

# --- QLineEdit ---
from qgis.PyQt.QtWidgets import QLineEdit
QLineEdit_Password = _qenum(QLineEdit, 'Password', 'EchoMode.Password')

# --- QMessageBox ---
from qgis.PyQt.QtWidgets import QMessageBox
QMessageBox_Warning = _qenum(QMessageBox, 'Warning', 'Icon.Warning')

# --- QFont ---
from qgis.PyQt.QtGui import QFont
QFont_Bold = _qenum(QFont, 'Bold', 'Weight.Bold')

# --- QAbstractItemView (additional) ---
QAbstractItemView_NoSelection = _qabstractitemview('NoSelection', 'SelectionMode.NoSelection')

# --- QSizePolicy (additional) ---
QSizePolicy_Ignored = _qpolicy('Ignored')

# --- QStandardPaths ---
# Qt6 moved these under QStandardPaths.StandardLocation.*
from qgis.PyQt.QtCore import QStandardPaths
QStandardPaths_DocumentsLocation = _qenum(
    QStandardPaths, 'DocumentsLocation', 'StandardLocation.DocumentsLocation')
QStandardPaths_TempLocation = _qenum(
    QStandardPaths, 'TempLocation', 'StandardLocation.TempLocation')
