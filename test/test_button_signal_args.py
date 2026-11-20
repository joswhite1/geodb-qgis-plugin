# -*- coding: utf-8 -*-
"""A button's clicked/triggered signal passes its `checked` flag (a bool) to the
slot. Wiring it straight to a method whose first parameter is an optional
non-bool (e.g. select_layer=None) hands that parameter False: "Refresh list"
under Publish from QGIS crashed on False.id() in 2.35.6. Wire such a method
through a lambda instead. Static check, runs WITHOUT QGIS:
python3 test/test_button_signal_args.py"""
import ast
import glob
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
SIGNALS = {'clicked', 'triggered'}


def _methods_by_file(tree):
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    out[item.name] = item
    return out


def _takes_optional_non_bool(fn):
    args = fn.args.args[1:]  # skip self
    if not args:
        return False
    first = args[0]
    n_defaults = len(fn.args.defaults)
    has_default = len(args) - n_defaults <= 0
    if not has_default:
        return False  # a required arg would be a TypeError, not silent
    ann = first.annotation
    if isinstance(ann, ast.Name) and ann.id == 'bool':
        return False
    return first.arg not in ('checked', 'state', 'on', 'enabled')


def offenders():
    found = []
    for path in glob.glob(os.path.join(ROOT, '**', '*.py'), recursive=True):
        if os.path.relpath(path, ROOT).split(os.sep)[0] == 'test':
            continue
        with open(path, encoding='utf-8', errors='replace') as fh:
            try:
                tree = ast.parse(fh.read())
            except SyntaxError:
                continue
        methods = _methods_by_file(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'connect' and node.args):
                continue
            sig = node.func.value
            if not (isinstance(sig, ast.Attribute) and sig.attr in SIGNALS):
                continue
            slot = node.args[0]
            if (isinstance(slot, ast.Attribute) and isinstance(slot.value, ast.Name)
                    and slot.value.id == 'self' and slot.attr in methods
                    and _takes_optional_non_bool(methods[slot.attr])):
                found.append(f"{os.path.relpath(path, ROOT)}:{node.lineno} "
                             f"{sig.attr} -> {slot.attr}")
    return found


class ButtonSignalArgsTests(unittest.TestCase):
    def test_no_button_feeds_checked_into_an_optional_arg(self):
        self.assertEqual(offenders(), [])


if __name__ == '__main__':
    unittest.main()
