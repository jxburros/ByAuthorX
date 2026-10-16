"""Fail CI if a required desktop workflow suite is absent, skipped or failed."""

import argparse
from pathlib import Path
from xml.etree import ElementTree

REQUIRED = (
    'test_gui',
    'test_gui_ai',
    'test_gui_libraries',
    'test_gui_review',
    'test_gui_workspace',
    'test_accessibility',
    'test_authoring_workflow',
)


def check_report(path):
    cases = ElementTree.parse(path).getroot().findall('.//testcase')
    errors = []
    for module in REQUIRED:
        selected = [case for case in cases if case.get('classname', '').split('.')[-1] == module]
        if not selected:
            errors.append(f'{module}: no test results')
        for case in selected:
            if any(case.find(tag) is not None for tag in ('skipped', 'failure', 'error')):
                errors.append(f'{module}.{case.get("name")}: did not pass')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    errors = check_report(args.report)
    if errors:
        parser.exit(1, '\n'.join(errors) + '\n')
    print('Required desktop workflow suites executed without skips or failures.')


if __name__ == '__main__':
    main()
