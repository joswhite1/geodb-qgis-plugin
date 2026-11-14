# -*- coding: utf-8 -*-
"""A 403 carrying reason_code claims_permission_missing gets a friendly dialog
title; the body stays the server's own message. Any other 403 is unchanged.
Runs WITHOUT QGIS: python3 test/test_permission_dialog_title.py"""
import importlib.util
import os
import unittest

_P = os.path.join(os.path.dirname(__file__), '..', 'api', 'exceptions.py')
_spec = importlib.util.spec_from_file_location('geodb_exceptions_under_test', _P)
exc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exc)

REMEDY = 'Ask a project manager for View Claims & Land Holdings.'


class PermissionDialogTitleTests(unittest.TestCase):
    def test_claims_code_gets_friendly_title_and_server_body(self):
        e = exc.APIPermissionError(
            REMEDY, 403, {'detail': REMEDY, 'reason_code': 'claims_permission_missing'})
        self.assertEqual(e.dialog_title, 'No access to claims')
        self.assertEqual(str(e), REMEDY)

    def test_other_403_keeps_old_title(self):
        e = exc.APIPermissionError('nope', 403, {'detail': 'nope', 'reason_code': 'other'})
        self.assertEqual(e.dialog_title, 'Permission Denied')

    def test_no_code_or_no_data_is_inert(self):
        self.assertEqual(exc.APIPermissionError('x', 403, {'detail': 'x'}).dialog_title,
                         'Permission Denied')
        self.assertEqual(exc.APIPermissionError('x').dialog_title, 'Permission Denied')
        self.assertEqual(exc.APIPermissionError('x', 403, ['weird']).dialog_title,
                         'Permission Denied')


if __name__ == '__main__':
    unittest.main()
