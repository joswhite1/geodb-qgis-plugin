# -*- coding: utf-8 -*-
"""Hand-moved LMs survive closing and reopening QGIS (2.35.5.1).

The incident (2026-11-05, a 366-claim Oregon block): the operator moved LMs by
hand in Step 5, finalized and pushed, then closed QGIS. To move two more LMs
they would go back to Step 5 or 6. On the reopened wizard:

* Step 5 found no monument layer it owned (the layer IDs lived only in
  memory), so it REGENERATED its layers from the server's defaults, which
  also overwrote the ``monuments`` table in the GeoPackage;
* Step 6's Generate Documents read no moved monuments for the same reason, so
  the notices, and the Step 7 push, carried the default LM positions.

The layers were never lost: they sit in the block's GeoPackage. The fix
re-attaches them by GeoPackage FILE + TABLE (never by layer name: a name can
belong to another block).

Runs in REAL QGIS, headless, against a real GeoPackage on disk. No server.

    QT_QPA_PLATFORM=offscreen /usr/bin/python3 test/test_monument_layers_survive_reopen.py

(/usr/bin/python3 has the QGIS 3.34 bindings; ~/venv does not.)
"""

import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qgis.core import (  # noqa: E402
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransformContext,
    QgsFeature, QgsField, QgsGeometry, QgsPointXY, QgsProject, QgsVectorFileWriter,
    QgsVectorLayer,
)

_APP = QgsApplication([], True)
_APP.initQgis()

# The plugin's modules use package-relative imports: load the checkout as the
# package ``geodb`` through a symlink (the same trick as the live e2e harness).
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_PARENT = tempfile.mkdtemp(prefix='plugpkg_')
os.symlink(_REPO, os.path.join(_PKG_PARENT, 'geodb'))
sys.path.insert(0, _PKG_PARENT)

from qgis.PyQt.QtCore import QVariant  # noqa: E402

from geodb.ui.claims_wizard_state import ClaimsWizardState  # noqa: E402
from geodb.ui.claims_step_widgets.step5_adjust import ClaimsStep5AdjustWidget  # noqa: E402
from geodb.ui.claims_step_widgets.step6_finalize import ClaimsStep6Widget  # noqa: E402

EPSG = 32611  # UTM 11N, an Oregon zone
CLAIMS = [('PH 1', 500000.0, 4800000.0), ('PH 2', 500200.0, 4800000.0)]
#: Where the operator dragged each LM: NOT where the server would put it.
MOVED = {'PH 1': (500031.25, 4800011.5), 'PH 2': (500233.75, 4800088.25)}


def _write_table(gpkg, table, geom_type, fields, rows, first):
    """Write one table into ``gpkg`` through QGIS's own writer."""
    layer = QgsVectorLayer(f"{geom_type}?crs=EPSG:{EPSG}", table, 'memory')
    prov = layer.dataProvider()
    prov.addAttributes([QgsField(name, kind) for name, kind in fields])
    layer.updateFields()
    feats = []
    for geom, attrs in rows:
        f = QgsFeature(layer.fields())
        f.setGeometry(geom)
        for k, v in attrs.items():
            f.setAttribute(k, v)
        feats.append(f)
    prov.addFeatures(feats)
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = 'GPKG'
    opts.layerName = table
    opts.actionOnExistingFile = (
        QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile if first
        else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
    err = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, gpkg, QgsCoordinateTransformContext(), opts)
    assert err[0] == QgsVectorFileWriter.WriterError.NoError, err


def _square(x, y, w=180.0, h=400.0):
    return QgsGeometry.fromPolygonXY([[QgsPointXY(x, y), QgsPointXY(x + w, y),
                                       QgsPointXY(x + w, y + h), QgsPointXY(x, y + h),
                                       QgsPointXY(x, y)]])


def make_block_geopackage(path):
    """A finalized block's GeoPackage, as Step 5 leaves it after the LMs were moved."""
    S, D = QVariant.String, QVariant.Double
    polys = [(_square(x, y), {'Name': n, 'State': 'OR'}) for n, x, y in CLAIMS]
    _write_table(path, 'initial_layout', 'Polygon', [('Name', S)],
                 [(g, {'Name': a['Name']}) for g, a in polys], first=True)
    _write_table(path, 'lode_claims', 'Polygon', [('Name', S), ('State', S)], polys, first=False)
    _write_table(path, 'monuments', 'Point',
                 [('Claim', S), ('Name', S), ('Easting', D), ('Northing', D)],
                 [(QgsGeometry.fromPointXY(QgsPointXY(*MOVED[n])),
                   {'Claim': n, 'Name': f"LM {n.split()[-1]}",
                    'Easting': MOVED[n][0], 'Northing': MOVED[n][1]})
                  for n, _, _ in CLAIMS], first=False)


def add_gpkg_layer(path, table, name):
    layer = QgsVectorLayer(f"{path}|layername={table}", name, 'ogr')
    assert layer.isValid(), (path, table)
    QgsProject.instance().addMapLayer(layer)
    return layer


def reopened_state(gpkg):
    """A wizard state as a reopen leaves it: the GeoPackage path is known,
    the monument layer IDs are not (they were never saved)."""
    state = ClaimsWizardState()
    state.geopackage_path = gpkg
    state.project_epsg = EPSG
    state.completed_steps = [1, 2, 3, 4, 5, 6, 7]
    return state


class _Base(unittest.TestCase):

    def setUp(self):
        QgsProject.instance().clear()
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem(f"EPSG:{EPSG}"))
        self.tmp = tempfile.mkdtemp(prefix='reopen_')
        self.gpkg = os.path.join(self.tmp, 'PH_claims.gpkg')
        make_block_geopackage(self.gpkg)

    def tearDown(self):
        QgsProject.instance().clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assert_reads_moved_lms(self, state):
        step6 = ClaimsStep6Widget(state, None)
        got = step6._read_monument_positions_from_layers()
        for name, (e, n) in MOVED.items():
            self.assertIn(name, got, f"Step 6 did not see {name}'s LM at all")
            mon = got[name]['discovery_monument']
            self.assertAlmostEqual(mon['easting'], e, places=3)
            self.assertAlmostEqual(mon['northing'], n, places=3)


class LayersAlreadyInTheProject(_Base):
    """Reopening the saved QGIS project: the layers come back with their names,
    the wizard's layer IDs do not."""

    def setUp(self):
        super().setUp()
        add_gpkg_layer(self.gpkg, 'initial_layout', 'Initial Layout [PH Lode Claims]')
        self.lode = add_gpkg_layer(self.gpkg, 'lode_claims', 'Lode Claims [PH Lode Claims]')
        self.monuments = add_gpkg_layer(self.gpkg, 'monuments', 'Monuments [PH Lode Claims]')
        self.state = reopened_state(self.gpkg)

    def test_state_re_attaches_the_monuments_layer(self):
        self.assertIsNone(self.state.monuments_layer_id)
        self.assertIs(self.state.monuments_layer, self.monuments)
        self.assertEqual(self.state.monuments_layer_id, self.monuments.id())

    def test_step6_reads_the_moved_lms(self):
        self.assert_reads_moved_lms(self.state)

    def test_step5_adopts_its_layers_and_does_not_regenerate(self):
        step5 = ClaimsStep5AdjustWidget(self.state, None)

        def _refuse(*a, **k):
            raise AssertionError("Step 5 regenerated the layers: the moved LMs would be lost")
        step5._generate_layers = _refuse
        layer_ids_before = set(QgsProject.instance().mapLayers())
        step5.on_enter()
        self.assertTrue(step5._layers_generated)
        self.assertEqual(set(QgsProject.instance().mapLayers()), layer_ids_before)
        self.assertIs(self.state.monuments_layer, self.monuments)
        # and the GeoPackage table still holds the moved positions
        self.assert_reads_moved_lms(self.state)


class LayersRestoredFromTheGeoPackage(_Base):
    """The wizard's own reopen restore, with nothing in the project yet."""

    def test_restore_brings_back_the_monuments(self):
        state = reopened_state(self.gpkg)
        state.restore_layers_from_geopackage()
        self.assertIsNotNone(state.monuments_layer)
        self.assertEqual(state.monuments_layer.featureCount(), 2)
        self.assert_reads_moved_lms(state)

    def test_restore_twice_adds_no_second_copy(self):
        state = reopened_state(self.gpkg)
        state.restore_layers_from_geopackage()
        count = len(QgsProject.instance().mapLayers())
        state.restore_layers_from_geopackage()
        self.assertEqual(len(QgsProject.instance().mapLayers()), count)


class AnotherBlocksLayersAreNeverAdopted(_Base):
    """Matching is by GeoPackage file + table, never by layer name."""

    def test_same_name_in_another_geopackage_is_ignored(self):
        other = os.path.join(self.tmp, 'OTHER_claims.gpkg')
        make_block_geopackage(other)
        add_gpkg_layer(other, 'monuments', 'Monuments [PH Lode Claims]')
        state = reopened_state(self.gpkg)
        self.assertIsNone(state.monuments_layer)
        self.assertIsNone(state.monuments_layer_id)

    def test_no_geopackage_means_no_monuments(self):
        add_gpkg_layer(self.gpkg, 'monuments', 'Monuments')
        state = ClaimsWizardState()  # memory-layer block: no GeoPackage
        self.assertIsNone(state.monuments_layer)


if __name__ == '__main__':
    result = unittest.main(exit=False, verbosity=2).result
    shutil.rmtree(_PKG_PARENT, ignore_errors=True)
    sys.exit(0 if result.wasSuccessful() else 1)
