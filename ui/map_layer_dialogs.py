"""Map layers in QGIS (usability Q-1 / Q-2, 2026-10-27).

* :class:`NewMapLayerDialog` — "New geoDB map layer…": lines, areas or points,
  its kind, its name, where to keep it (a GeoPackage), and optionally the field
  sketch it is traced from.
* :func:`ask_publish_or_draft` — the "Publish to geoDB" choice: Publish now
  (default) or Save as draft, saying plainly where a published layer shows.

The dialogs only collect answers; ``DataManager.create_map_layer`` /
``publish_map_layer`` do the work.
"""
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..utils.map_layer_kinds import MAP_LAYER_GEOMETRIES, MAP_LAYER_KINDS


class NewMapLayerDialog(QDialog):
    """Collects what a new map layer needs. ``values()`` after ``exec()``."""

    def __init__(self, parent=None, *, crs_label='', default_gpkg='', sketches=None):
        super().__init__(parent)
        self.setWindowTitle("New geoDB map layer")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Make an empty layer to draw or trace into. When it is ready, press "
            "“Publish to geoDB” and it shows on the web map and on your crew's "
            "phones."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        layout.addLayout(form)

        self.geometryCombo = QComboBox()
        for value, label, _qgis_type, _kind in MAP_LAYER_GEOMETRIES:
            self.geometryCombo.addItem(label, value)
        form.addRow("What it holds:", self.geometryCombo)

        self.kindCombo = QComboBox()
        for value, label in MAP_LAYER_KINDS:
            self.kindCombo.addItem(label, value)
        form.addRow("Kind:", self.kindCombo)
        self.geometryCombo.currentIndexChanged.connect(self._suggest_kind)
        self._suggest_kind()

        self.nameEdit = QLineEdit()
        self.nameEdit.setPlaceholderText("e.g. Alteration zones 2026")
        form.addRow("Name:", self.nameEdit)

        self.sketchCombo = QComboBox()
        self.sketchCombo.addItem("— none —", None)
        for sketch in sketches or []:
            when = str(sketch.get('date_created') or '')[:10]
            label = sketch.get('name') or f"Sketch {sketch.get('id')}"
            self.sketchCombo.addItem(f"{label} ({when})" if when else label, sketch.get('id'))
        self.sketchCombo.setToolTip(
            "The field sketch you are tracing, if any. geoDB keeps the link, so "
            "anyone can open the sketch behind this layer."
        )
        form.addRow("Traced from sketch:", self.sketchCombo)

        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.pathEdit = QLineEdit(default_gpkg or '')
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_layout.addWidget(self.pathEdit)
        path_layout.addWidget(browse)
        form.addRow("Keep it in (GeoPackage):", path_row)

        crs_note = QLabel(f"Coordinate system: {crs_label} (the project's)")
        crs_note.setWordWrap(True)
        layout.addWidget(crs_note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create layer")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _suggest_kind(self):
        geometry = self.geometryCombo.currentData()
        for value, _label, _qgis_type, kind in MAP_LAYER_GEOMETRIES:
            if value == geometry:
                index = self.kindCombo.findData(kind)
                if index >= 0:
                    self.kindCombo.setCurrentIndex(index)

    def _browse(self):
        path, _filter = QFileDialog.getSaveFileName(
            self, "GeoPackage for the new map layer", self.pathEdit.text(),
            "GeoPackage (*.gpkg)")
        if path:
            if not path.lower().endswith('.gpkg'):
                path += '.gpkg'
            self.pathEdit.setText(path)

    def _accept(self):
        if not self.nameEdit.text().strip():
            QMessageBox.warning(self, "Name needed", "Give the map layer a name.")
            return
        if not self.pathEdit.text().strip():
            QMessageBox.warning(self, "GeoPackage needed",
                                "Choose the GeoPackage file to keep the layer in.")
            return
        self.accept()

    def values(self):
        path = self.pathEdit.text().strip()
        if path and not path.lower().endswith('.gpkg'):
            path += '.gpkg'
        return {
            'name': self.nameEdit.text().strip(),
            'geometry': self.geometryCombo.currentData(),
            'kind': self.kindCombo.currentData(),
            'traced_from': self.sketchCombo.currentData(),
            'gpkg_path': path,
        }


#: The answers of :func:`ask_publish_or_draft`.
PUBLISH, DRAFT, CANCEL = 'publish', 'draft', 'cancel'


def publish_message(layer_name, *, replaces=False, catalog=False):
    """The words of the publish choice (kept apart so a test can read them)."""
    text = (
        f"Send “{layer_name}” to geoDB?\n\n"
        "Publish now: it goes live on the geoDB web map, and on your crew's "
        "phones at their next sync."
    )
    if replaces:
        text += (" It replaces the version on the map now; geoDB keeps that one "
                 "(archived), so it can be brought back.")
    text += ("\n\nSave as draft: nobody sees it on a map until someone publishes "
             "it (geoDB web: Display settings → Map data).")
    if catalog:
        text += ("\n\nThis layer is coloured by the geoDB catalog. What you send "
                 "keeps the colours you see in QGIS; the catalog's own colours are "
                 "changed on the web (Display settings).")
    return text


def ask_publish_or_draft(parent, layer_name, *, replaces=False, catalog=False):
    """PUBLISH, DRAFT or CANCEL. Publish now is the default button."""
    box = QMessageBox(parent)
    box.setWindowTitle("Publish to geoDB")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(publish_message(layer_name, replaces=replaces, catalog=catalog))
    publish_button = box.addButton("Publish now", QMessageBox.ButtonRole.AcceptRole)
    draft_button = box.addButton("Save as draft", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(publish_button)
    box.exec()
    clicked = box.clickedButton()
    if clicked is publish_button:
        return PUBLISH
    if clicked is draft_button:
        return DRAFT
    return CANCEL
