from byax.checks import run_checks
from byax.model import new_document


def test_truncation_does_not_hide_failure():
    doc = new_document(text='hello')
    doc['settings']['requirements'] = {
        'a': {'kind': 'required_text', 'value': 'missing'},
        'b': {'kind': 'max_words', 'value': 0},
    }
    report = run_checks(doc, ['requirements'], limit=0)
    assert report['validation'] == 'failed'
    assert report['findings'] == []
    assert report['summary']['errors'] == 2
    assert report['summary']['truncated']


def test_publication_reports_unmeasurable_checks():
    report = run_checks(new_document(), ['publication'])
    assert report['validation'] == 'incomplete'
    assert any(c['status'] == 'unmeasurable' for c in report['coverage'])


def test_typed_required_fields_and_false_zero():
    doc = new_document()
    doc['fields'] = {
        'missing': {'type': 'text', 'required': True},
        'n': {'type': 'number', 'required': True, 'value': 0},
        'b': {'type': 'boolean', 'required': True, 'value': False},
    }
    report = run_checks(doc, ['requirements'])
    assert report['summary']['errors'] == 1
    assert report['findings'][0]['measured']['field'] == 'missing'


def test_heading_and_alt_checks_are_advisory_to_writing():
    doc = new_document()
    doc['blocks'][0].update(type='heading', attrs={'level': 3})
    report = run_checks(doc, ['accessibility'])
    assert report['validation'] == 'passed'
    assert report['summary']['warnings'] == 1


def test_print_check_accepts_image_without_attrs():
    doc = new_document(text='hello')
    doc['blocks'].append({'id': 'img', 'type': 'image', 'text': '', 'style': 'body', 'marks': []})
    report = run_checks(doc, ['print'])
    assert report['validation'] != 'not_run'
