"""The "geoDB map layers" panel, the "Field sketches" list and the map-layer
help (usability Q-4 / Q-5 / Q-7, 2026-10-28).

Thin views over :mod:`managers.map_layer_manage` (the words and which actions
a row offers) and the server's own manage doors. Every action refreshes the
list from geoDB afterwards; a refusal shows the server's own sentence.
"""
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)

from ..managers.map_layer_manage import (
    ACTIONS,
    MapLayerManager,
    actions_for,
    publish_label,
    sketch_rows,
    sort_sketches,
    strokes_result_text,
    table_rows,
)
from ..utils.compat import (
    QAbstractItemView_NoEditTriggers,
    QAbstractItemView_SelectRows,
    QAbstractItemView_SingleSelection,
    QHeaderView_ResizeToContents,
    QHeaderView_Stretch,
)
from ..utils.map_layer_kinds import MAP_LAYER_KINDS

MANAGE_COLUMNS = ("Name", "Status", "Kind", "Features", "Last edit", "Traced from",
                  "Crew phones")
SKETCH_COLUMNS = ("Mapping area", "Sketched by", "Date", "Sheets", "Lines",
                  "Traced into")


def _fill_table(table, columns, cells):
    table.clear()
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.setRowCount(len(cells))
    for r, row in enumerate(cells):
        for c, value in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(str(value)))
    header = table.horizontalHeader()
    for c in range(len(columns)):
        header.setSectionResizeMode(c, QHeaderView_Stretch if c == 0
                                    else QHeaderView_ResizeToContents)


def _plain_table(parent):
    table = QTableWidget(parent)
    table.setEditTriggers(QAbstractItemView_NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView_SelectRows)
    table.setSelectionMode(QAbstractItemView_SingleSelection)
    table.verticalHeader().setVisible(False)
    return table


class ManageMapLayersDialog(QDialog):
    """Every map layer of the project — live, drafts, archived, and with
    "Show Trash" the discarded ones — and what can be done to each."""

    def __init__(self, parent, api_client, *, on_changed=None):
        super().__init__(parent)
        self.setWindowTitle("geoDB map layers")
        self.setMinimumSize(820, 420)
        self.manager = MapLayerManager(api_client)
        self.on_changed = on_changed
        self.rows = []
        self.can_write = False

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Every map layer of this project. Live layers show on the geoDB web map "
            "and on your crew's phones; drafts show only here until published; "
            "archived layers are off every map and kept.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        top = QHBoxLayout()
        self.trashCheck = QCheckBox("Show Trash (discarded drafts)")
        self.trashCheck.toggled.connect(self.refresh)
        top.addWidget(self.trashCheck)
        top.addStretch(1)
        self.refreshButton = QPushButton("Refresh")
        self.refreshButton.clicked.connect(self.refresh)
        top.addWidget(self.refreshButton)
        layout.addLayout(top)

        self.table = _plain_table(self)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table)

        self.statusLabel = QLabel("")
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)

        buttons = QHBoxLayout()
        self.actionButtons = {}
        for key, label in ACTIONS:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, k=key: self.run_action(k))
            buttons.addWidget(button)
            self.actionButtons[key] = button
        layout.addLayout(buttons)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    # -- data ------------------------------------------------------------
    def refresh(self, *_args):
        try:
            body = self.manager.list(trashed=self.trashCheck.isChecked())
        except Exception as e:  # shown, never swallowed
            self.rows, self.can_write = [], False
            _fill_table(self.table, MANAGE_COLUMNS, [])
            self.statusLabel.setText(f"Could not load the map layers: {e}")
            self._update_buttons()
            return
        self.rows = body.get('results') or []
        self.can_write = bool(body.get('can_write'))
        _fill_table(self.table, MANAGE_COLUMNS, table_rows(self.rows))
        if not self.rows:
            self.statusLabel.setText(
                "Nothing in the Trash." if self.trashCheck.isChecked() else
                "No map layers yet. Make one with “New map layer…”, draw or trace "
                "into it, and press “Publish to geoDB”.")
        elif not self.can_write:
            self.statusLabel.setText("You can see these layers but not change them.")
        else:
            self.statusLabel.setText("")
        self._update_buttons()

    def selected_row(self):
        index = self.table.currentRow()
        if index is None or index < 0 or index >= len(self.rows):
            return None
        selected = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not selected:
            return None
        return self.rows[index]

    def select_layer(self, layer_id):
        for i, row in enumerate(self.rows):
            if row.get('id') == layer_id:
                self.table.selectRow(i)
                return True
        return False

    def _update_buttons(self):
        row = self.selected_row()
        allowed = set(actions_for(row, self.can_write))
        for key, button in self.actionButtons.items():
            button.setVisible(key in allowed or key in ('rename', 'kind', 'archive', 'publish'))
            button.setEnabled(key in allowed)
        if row is not None and 'publish' in allowed:
            self.actionButtons['publish'].setToolTip(publish_label(row))

    # -- actions ---------------------------------------------------------
    def ask_name(self, row):
        name, ok = QInputDialog.getText(
            self, "Rename map layer", "New name:",
            text=row.get('display_name') or row.get('name') or '')
        return name if ok else None

    def ask_kind(self, row):
        labels = [label for _code, label in MAP_LAYER_KINDS]
        codes = [code for code, _label in MAP_LAYER_KINDS]
        current = codes.index(row['kind']) if row.get('kind') in codes else 0
        label, ok = QInputDialog.getItem(self, "Change kind", "Kind:", labels, current, False)
        return codes[labels.index(label)] if ok else None

    def confirm(self, title, text):
        return QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def run_action(self, key, row=None):
        """Run one action on ``row`` (default: the selected one). Returns the
        server's updated row, or None (cancelled / refused — said in the
        status line)."""
        row = row or self.selected_row()
        if row is None or key not in actions_for(row, self.can_write):
            return None
        name = row.get('display_name') or row.get('name')
        m = self.manager
        try:
            if key == 'rename':
                new_name = self.ask_name(row)
                if not new_name:
                    return None
                response = m.rename(row, new_name)
            elif key == 'kind':
                kind = self.ask_kind(row)
                if not kind:
                    return None
                response = m.change_kind(row, kind)
            elif key in ('phones_on', 'phones_off'):
                response = m.set_show_on_phones(row, key == 'phones_on')
            elif key == 'publish':
                if not self.confirm("Publish", f"{publish_label(row)}: “{name}”?"):
                    return None
                response = m.publish(row)
            elif key == 'publish_alongside':
                if not self.confirm("Publish alongside",
                                    f"Put “{name}” live BESIDE the layer it would "
                                    "replace (both stay on the map)?"):
                    return None
                response = m.publish(row, alongside=True)
            elif key == 'archive':
                if not self.confirm("Archive", f"Take “{name}” off the web map and crew "
                                    "phones? geoDB keeps it, and it can be made live again."):
                    return None
                response = m.archive(row)
            elif key == 'reactivate':
                response = m.reactivate(row)
            elif key == 'discard':
                if not self.confirm("Discard draft", f"Move the draft “{name}” to the "
                                    "Trash? It can be restored from “Show Trash”."):
                    return None
                response = m.discard(row)
            elif key == 'restore':
                response = m.restore(row)
            else:
                return None
        except Exception as e:
            self.statusLabel.setText(f"“{name}”: {e}")
            return None
        layer = dict((response or {}).get('layer') or {})
        if 'status' not in layer and (response or {}).get('status'):
            layer['status'] = response['status']      # publish/ answers it beside the layer
        self.refresh()
        if layer.get('id'):
            self.select_layer(layer['id'])
        self.statusLabel.setText(f"✓ {dict(ACTIONS)[key].rstrip('…')}: “{layer.get('display_name') or name}”")
        if self.on_changed:
            self.on_changed()
        return layer


class FieldSketchesDialog(QDialog):
    """The project's field sketches, newest first, by mapping area and author.
    ``open_sketch(file_id)`` / ``open_strokes(file_id)`` are the caller's
    (the main dialog's raster path and DataManager.open_sketch_strokes)."""

    def __init__(self, parent, api_client, *, open_sketch, open_strokes, can_edit=True):
        super().__init__(parent)
        self.setWindowTitle("Field sketches")
        self.setMinimumSize(780, 380)
        self.api = api_client
        self.open_sketch_cb = open_sketch
        self.open_strokes_cb = open_strokes
        self.can_edit = can_edit
        self.sketches = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Sketches your crew drew on their phones, in place on the map. Open the "
            "sketch to trace it, or open its strokes as lines to start from them.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = _plain_table(self)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table)
        self.statusLabel = QLabel("")
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)

        row = QHBoxLayout()
        self.openSketchButton = QPushButton("Open sketch")
        self.openSketchButton.setToolTip("Add the sketch image to the map, in place, "
                                         "white made transparent")
        self.openSketchButton.clicked.connect(self.open_sketch)
        self.openStrokesButton = QPushButton("Open strokes as lines")
        self.openStrokesButton.setToolTip(
            "geoDB turns the sketch's strokes into draft line layers (one per sheet) "
            "and opens them here to clean up and publish")
        self.openStrokesButton.clicked.connect(self.open_strokes)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(self.openSketchButton)
        row.addWidget(self.openStrokesButton)
        row.addStretch(1)
        row.addWidget(refresh)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def refresh(self, *_args):
        try:
            self.sketches = sort_sketches(self.api.get_sketch_list())
        except Exception as e:
            self.sketches = []
            self.statusLabel.setText(f"Could not load the field sketches: {e}")
        else:
            self.statusLabel.setText(
                "" if self.sketches else
                "No field sketches yet. Crews sketch on a mapping area in the field "
                "app; each sketch appears here after their next sync.")
        _fill_table(self.table, SKETCH_COLUMNS, sketch_rows(self.sketches))
        self._update_buttons()

    def selected(self):
        index = self.table.currentRow()
        selected = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not selected or index < 0 or index >= len(self.sketches):
            return None
        return self.sketches[index]

    def _update_buttons(self):
        sketch = self.selected()
        self.openSketchButton.setEnabled(sketch is not None)
        self.openStrokesButton.setEnabled(
            sketch is not None and bool(sketch.get('has_strokes')) and self.can_edit)
        if sketch is not None and not sketch.get('has_strokes'):
            self.openStrokesButton.setToolTip(
                "This sketch has no strokes stored in geoDB (it came from an older "
                "field app): trace the sketch image instead.")

    def open_sketch(self):
        sketch = self.selected()
        if sketch is None:
            return None
        try:
            return self.open_sketch_cb(sketch['file_id'])
        except Exception as e:
            self.statusLabel.setText(f"Could not open the sketch: {e}")
            return None

    def open_strokes(self):
        sketch = self.selected()
        if sketch is None:
            return None
        try:
            opened = self.open_strokes_cb(sketch['file_id'])
        except Exception as e:
            self.statusLabel.setText(f"Could not open the strokes: {e}")
            return None
        self.statusLabel.setText(strokes_result_text(opened.get('result') or {}))
        self.refresh()
        self.statusLabel.setText(strokes_result_text(opened.get('result') or {}))
        return opened


HELP_HTML = """
<h3>Map layers: from field sketch to project map</h3>
<p>A <b>map layer</b> is one kind of thing drawn as lines, areas or points, with its
colours: rock units, contacts, faults, alteration zones, targets. Live map layers show on
the geoDB web map and on every crew phone (offline too), where tapping a unit or a fault
says what it is.</p>
<h4>1. Sketch in the field</h4>
<p>On the phone, a crew sketches contacts, faults and units over the basemap on a mapping
area, one sheet per theme. After their next sync the sketch lands in geoDB, in place.</p>
<h4>2. Open it in QGIS</h4>
<p><b>Field sketches…</b> lists every sketch by mapping area, author and date.
<b>Open sketch</b> adds the image in place (white made transparent) to trace over.
<b>Open strokes as lines</b> has geoDB turn the strokes into draft line layers, one per
sheet, so you clean them up instead of tracing from nothing. Eraser marks are not applied
to the lines: check the image.</p>
<h4>3. Trace and attribute</h4>
<p><b>New map layer…</b> makes an empty layer (lines, areas or points) in the project's
coordinate system, kept in a GeoPackage on your computer and linked to the sketch it is
traced from. Add attributes (unit, confidence, notes) as you like; <i>geodb_label</i> is the
name shown when someone taps the feature.</p>
<h4>4. Publish</h4>
<p><b>Publish to geoDB</b> sends the layer. <b>Publish now</b> puts it live on the web map
and crew phones; <b>Save as draft</b> keeps it off every map until someone publishes it.
Publishing an edited layer you pulled replaces the version on the map; geoDB keeps the old
one (archived) and your original coordinates exactly as you drew them.</p>
<h4>5. Manage</h4>
<p><b>Manage map layers…</b> shows every layer with its status (live, draft, archived),
kind, feature count, last edit and the sketch it was traced from. From there: rename,
change kind, hide or show on crew phones, publish a draft (replacing the layer it updates,
or alongside it), archive and make live again, discard a draft and restore it from the
Trash.</p>
<h4>Pulling again</h4>
<p>Pulling a layer replaces your local copy. If it has edits you have not published, the
plugin asks first: publish them, discard them, or cancel.</p>
<p><i>GeoPackage files you store in geoDB with “Store a GeoPackage file in geoDB” are kept
as files; they are not shown on the map or on phones. To put layers on the map, publish
them.</i></p>
"""


class MapLayersHelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Map layers: help")
        self.setMinimumSize(560, 520)
        layout = QVBoxLayout(self)
        browser = QTextBrowser(self)
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
