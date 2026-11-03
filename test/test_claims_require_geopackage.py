# -*- coding: utf-8 -*-
"""The claims wizard refuses to move forward on memory layers (v2.35.6).

On 2026-11-02 a customer lost a 366-claim block: no GeoPackage had been made in
Step 1, so every layer was in memory, and QGIS crashed.

Runs the REAL wizard in REAL QGIS, headless, with no server:

    /usr/bin/python3 test/test_claims_require_geopackage.py [plugin_dir]

(Do not run via ``-m unittest test.<name>``.) Saved work is read back from the
file with sqlite3, in the same way a reopen after a crash has only the file.
"""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PLUGIN_DIR = os.path.abspath(
    sys.argv.pop(1) if len(sys.argv) > 1 and os.path.isdir(sys.argv[1])
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
)

from qgis.core import (  # noqa: E402
    QgsApplication, QgsFeature, QgsField, QgsGeometry, QgsProject, QgsVectorLayer,
    QgsVectorFileWriter,
)
from qgis.PyQt.QtCore import QVariant  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

# Import the plugin as the package `geodb` from the chosen checkout.
_PKG_PARENT = tempfile.mkdtemp(prefix='plugpkg_')
os.symlink(PLUGIN_DIR, os.path.join(_PKG_PARENT, 'geodb'))
sys.path.insert(0, _PKG_PARENT)

from geodb.ui.claims_wizard_widget import ClaimsWizardWidget  # noqa: E402
import geodb.ui.claims_wizard_widget as wizard_module  # noqa: E402


def _memory_layout(count=3, name='My Layout'):
    layer = QgsVectorLayer('Polygon?crs=EPSG:26911', name, 'memory')
    layer.dataProvider().addAttributes([QgsField('name', QVariant.String)])
    layer.updateFields()
    features = []
    for i in range(count):
        feature = QgsFeature(layer.fields())
        x = 400000 + i * 500
        feature.setGeometry(QgsGeometry.fromWkt(
            f'POLYGON(({x} 4900000,{x + 400} 4900000,{x + 400} 4900200,'
            f'{x} 4900200,{x} 4900000))'
        ))
        feature.setAttributes([f'PH {i + 1}'])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def _rows_on_disk(path, table='initial_layout'):
    """Read straight from the file: this is all that survives a crash."""
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            f'SELECT name FROM "{table}" ORDER BY name'
        ).fetchall()
    finally:
        connection.close()


class RequireGeoPackage(unittest.TestCase):

    def setUp(self):
        QgsProject.instance().clear()
        self.dir = tempfile.mkdtemp(prefix='claims_gpkg_test_')
        self.gpkg = os.path.join(self.dir, 'PH_claims.gpkg')

        patcher = mock.patch.object(wizard_module, 'QMessageBox')
        self.message_box = patcher.start()
        self.addCleanup(patcher.stop)

        self.wizard = ClaimsWizardWidget(mock.MagicMock())
        self.state = self.wizard.state
        self.step1 = self.wizard.step_widgets[0]
        self.step1.validate = lambda: []  # only the GeoPackage rule is under test

    def _select_geopackage(self):
        self.state.geopackage_path = self.gpkg
        self.assertTrue(self.state.save_to_geopackage())
        self.wizard._update_navigation_buttons()

    def test_without_geopackage_next_is_off_and_banner_says_why(self):
        self.wizard._update_navigation_buttons()
        self.assertFalse(self.wizard.next_btn.isEnabled())
        self.assertFalse(self.wizard.persistence_banner.isHidden())
        self.assertIn('GeoPackage', self.wizard.persistence_banner.text())

    def test_next_click_cannot_get_past_it(self):
        self.wizard._on_next_clicked()
        self.assertEqual(self.wizard.current_step, 0)
        self.message_box.warning.assert_called_once()

    def test_step_jump_cannot_get_past_it(self):
        self.state.completed_steps = [1, 2, 3]
        self.wizard._on_step_indicator_clicked(2)
        self.assertEqual(self.wizard.current_step, 0)
        self.message_box.warning.assert_called_once()

    def test_later_step_resumed_on_memory_cannot_go_forward(self):
        self.wizard.go_to_step(3)
        self.wizard.step_widgets[3].validate = lambda: []
        self.wizard._update_navigation_buttons()
        self.assertFalse(self.wizard.next_btn.isEnabled())
        self.assertFalse(self.wizard.persistence_banner.isHidden())

    def test_with_geopackage_the_block_lifts(self):
        self._select_geopackage()
        self.assertTrue(self.wizard.persistence_banner.isHidden())
        self.assertTrue(self.wizard.next_btn.isEnabled())

    def _pump(self):
        end = time.time() + 0.4
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def test_banner_link_reaches_the_buttons_from_a_later_step(self):
        self.wizard.resize(1150, 620)
        self.wizard.show()
        self.wizard.go_to_step(3)
        self.wizard._update_navigation_buttons()
        self._pump()
        self.wizard.persistence_banner.linkActivated.emit('geopackage')
        self._pump()
        self.assertEqual(self.wizard.current_step, 0)
        self.assertFalse(self.step1.create_gpkg_btn.visibleRegion().isEmpty())
        self.wizard.hide()

    def test_step_1_scrolls_the_buttons_into_view_when_entered(self):
        self.wizard.resize(1150, 620)
        self.wizard.show()
        self._pump()
        self.step1._scroll_area.verticalScrollBar().setValue(0)
        self._pump()
        self.assertTrue(
            self.step1.create_gpkg_btn.visibleRegion().isEmpty(),
            'window too tall for this test to prove anything'
        )
        self.step1.on_enter()
        self._pump()
        self.assertFalse(self.step1.create_gpkg_btn.visibleRegion().isEmpty())
        self.wizard.hide()

    def test_a_path_whose_file_is_gone_still_blocks(self):
        self.state.geopackage_path = os.path.join(self.dir, 'missing.gpkg')
        self.wizard._update_navigation_buttons()
        self.assertFalse(self.wizard.next_btn.isEnabled())

    def test_memory_layout_adopted_survives_and_memory_copy_is_removed(self):
        self._select_geopackage()
        source = _memory_layout()
        source_id = source.id()

        adopted = self.state.adopt_layer_into_geopackage(source)

        self.assertEqual(adopted.dataProvider().name(), 'ogr')
        self.assertEqual(self.state.claims_layer_id, adopted.id())
        self.assertIsNone(QgsProject.instance().mapLayer(source_id))
        self.assertEqual(
            _rows_on_disk(self.gpkg), [('PH 1',), ('PH 2',), ('PH 3',)]
        )

    def test_the_block_can_be_reopened_from_the_file_alone(self):
        self._select_geopackage()
        self.state.adopt_layer_into_geopackage(_memory_layout())

        QgsProject.instance().clear()  # what a crash leaves
        reopened = QgsVectorLayer(
            f'{self.gpkg}|layername=initial_layout', 'reopened', 'ogr'
        )
        self.assertTrue(reopened.isValid())
        self.assertEqual(reopened.featureCount(), 3)
        self.assertEqual(reopened.crs().authid(), 'EPSG:26911')

    def test_users_own_file_layer_is_copied_and_left_in_place(self):
        self._select_geopackage()
        memory = _memory_layout()
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'GPKG'
        own_path = os.path.join(self.dir, 'mine.gpkg')
        QgsVectorFileWriter.writeAsVectorFormatV3(
            memory, own_path, QgsProject.instance().transformContext(), options
        )
        own = QgsVectorLayer(own_path, 'mine', 'ogr')
        QgsProject.instance().addMapLayer(own)

        self.state.adopt_layer_into_geopackage(own)

        self.assertIsNotNone(QgsProject.instance().mapLayer(own.id()))
        self.assertEqual(len(_rows_on_disk(self.gpkg)), 3)

    def test_pulled_claims_move_into_the_file_on_leaving_step_1(self):
        features = [{
            'id': 7 + i,
            'geometry': {
                'type': 'Polygon',
                'coordinates': [[[-118.2 + i / 100, 44.3], [-118.19 + i / 100, 44.3],
                                 [-118.19 + i / 100, 44.31], [-118.2 + i / 100, 44.3]]],
            },
            'properties': {'claim_name': f'PH {i + 1}', 'approved': True},
        } for i in range(2)]
        # Pulled BEFORE a GeoPackage is chosen: nothing may be lost by that order.
        self.step1._load_proposed_claims_to_layer({
            'claims': features, 'project_name': 'Pheasant',
        })
        self._select_geopackage()

        self.step1.save_state()

        self.assertEqual(_rows_on_disk(self.gpkg), [('PH 1',), ('PH 2',)])
        self.assertEqual(self.state.claims_layer.dataProvider().name(), 'ogr')
        self.assertEqual(self.state.claims_layer.featureCount(), 2)
        memory_left = [
            layer for layer in QgsProject.instance().mapLayers().values()
            if layer.dataProvider().name() == 'memory'
        ]
        self.assertEqual(memory_left, [])

    def test_wizard_metadata_survives_adoption(self):
        self._select_geopackage()
        self.state.adopt_layer_into_geopackage(_memory_layout())
        self.assertTrue(self.state.save_to_geopackage())

        fresh_state = type(self.state)()
        self.assertTrue(fresh_state.load_from_geopackage(self.gpkg))
        self.assertEqual(fresh_state.geopackage_path, self.gpkg)


if __name__ == '__main__':
    result = unittest.main(exit=False, verbosity=2).result
    sys.exit(not result.wasSuccessful())
