"""Missing, skipped and failed desktop coverage cannot produce a green evidence gate."""

import importlib.util
from pathlib import Path
from xml.etree import ElementTree

import pytest

SPEC = importlib.util.spec_from_file_location(
    'check_test_report', Path(__file__).resolve().parents[1] / 'scripts' / 'check_test_report.py'
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


@pytest.mark.parametrize('problem', ['missing', 'skipped', 'failure', 'error', None])
def test_report_guard_requires_executed_desktop_suites(tmp_path, problem):
    root = ElementTree.Element('testsuites')
    suite = ElementTree.SubElement(root, 'testsuite')
    for module in guard.REQUIRED:
        if module == 'test_gui' and problem == 'missing':
            continue
        case = ElementTree.SubElement(suite, 'testcase', classname='tests.' + module, name='workflow')
        if module == 'test_gui' and problem in {'skipped', 'failure', 'error'}:
            ElementTree.SubElement(case, problem)
    path = tmp_path / 'tests.xml'
    ElementTree.ElementTree(root).write(path)
    assert bool(guard.check_report(path)) == (problem is not None)
