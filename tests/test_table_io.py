import copy
import csv
from pathlib import Path

import pytest

from byax.cli import execute, main
from byax.engine import apply
from byax.errors import ByaxError
from byax.model import new_document
from byax.storage import load_document, save_document
from byax.table_io import export_csv, import_csv, template_rows


def document_with_table():
    doc, _ = apply(new_document('CSV fixture', 'Unrelated paragraph'), {'operations': [
        {'type': 'insert_table', 'rows': 2, 'columns': 2}]})
    table = next(block for block in doc['blocks'] if block['type'] == 'table')
    table['attrs'].update({'width': 400, 'header_rows': 1})
    table['rows'][0]['attrs'] = {'height': 24}
    table['rows'][0]['cells'][0]['attrs'] = {'alignment': 'right', 'currency': 'USD'}
    table['rows'][0]['cells'][0]['text'] = 'Before'
    return doc, table


def test_csv_updates_values_preserving_ids_attributes_and_unrelated_content(tmp_path):
    doc, table = document_with_table()
    original = copy.deepcopy(doc)
    path = tmp_path / 'values.csv'
    path.write_text('Name,Amount\n"Café, Inc.","Line one\nLine two"\n', encoding='utf-8')
    updated, report = import_csv(doc, table['id'], path, expected_revision=doc['revision'], request_id='table-import-1')
    target = next(block for block in updated['blocks'] if block['id'] == table['id'])
    assert target['attrs'] == table['attrs']
    assert [[cell['id'] for cell in row['cells']] for row in target['rows']] == [[cell['id'] for cell in row['cells']] for row in table['rows']]
    assert target['rows'][0]['attrs'] == table['rows'][0]['attrs']
    assert target['rows'][0]['cells'][0]['attrs'] == table['rows'][0]['cells'][0]['attrs']
    assert target['rows'][1]['cells'][0]['text'] == 'Café, Inc.'
    assert target['rows'][1]['cells'][1]['text'] == 'Line one\nLine two'
    assert updated['blocks'][0] == doc['blocks'][0]
    assert doc == original
    replay, replay_report = import_csv(updated, table['id'], path, expected_revision=doc['revision'], request_id='table-import-1')
    assert replay['revision'] == updated['revision']
    assert replay_report['execution'] == 'already_committed'
    assert report['csv']['existing_identities_preserved']


def test_csv_growth_is_explicit_and_one_atomic_revision(tmp_path):
    doc, table = document_with_table()
    source = tmp_path / 'grow.csv'
    source.write_text('a,b,c\nd,e,f\ng,h,i\n', encoding='utf-8')
    with pytest.raises(ByaxError):
        import_csv(doc, table['id'], source)
    grown, result = import_csv(doc, table['id'], source, grow=True, request_id='grow-request')
    target = next(block for block in grown['blocks'] if block['id'] == table['id'])
    assert [[cell['text'] for cell in row['cells']] for row in target['rows']] == [list('abc'), list('def'), list('ghi')]
    assert target['rows'][0]['cells'][0]['id'] == table['rows'][0]['cells'][0]['id']
    assert target['rows'][0]['cells'][0]['attrs'] == table['rows'][0]['cells'][0]['attrs']
    assert len(grown['history']['revisions']) == len(doc['history']['revisions']) + 1
    retried, _ = import_csv(grown, table['id'], source, grow=True, request_id='grow-request')
    assert retried['revision'] == grown['revision']
    assert result['csv']['growth'] == {'rows': 1, 'columns': 1}


def test_csv_export_encoding_dialect_newline_and_atomic_failure(tmp_path):
    doc, table = document_with_table()
    table['rows'][1]['cells'][0]['text'] = 'Café; "quoted"'
    destination = tmp_path / 'table.csv'
    export_csv(doc, table['id'], destination, encoding='utf-16', delimiter=';', newline='crlf')
    with destination.open(encoding='utf-16', newline='') as handle:
        rows = list(csv.reader(handle, delimiter=';'))
    assert rows[1][0] == 'Café; "quoted"'
    assert '\r\n' in destination.read_bytes().decode('utf-16')
    old_bytes = destination.read_bytes()
    with pytest.raises(ByaxError):
        export_csv(doc, table['id'], destination)
    with pytest.raises(ByaxError):
        export_csv(doc, table['id'], destination, encoding='ascii', overwrite=True)
    assert destination.read_bytes() == old_bytes


def test_table_cli_json_dry_run_and_export(tmp_path, capsys):
    doc, table = document_with_table()
    native = tmp_path / 'table.byax'
    save_document(doc, native)
    source = tmp_path / 'input.csv'
    source.write_text('new,values\nmore,values\n', encoding='utf-8')
    assert main(['table', str(native), '--table', table['id'], '--csv', str(source), '--dry-run']) == 0
    assert load_document(native)['revision'] == doc['revision']
    capsys.readouterr()
    execute('table', {'path': str(native), 'table': table['id'], 'csv': str(source)})
    output = str(tmp_path / 'output.csv')
    exported = execute('table', {'path': str(native), 'table': table['id'], 'action': 'export', 'out': output})
    assert exported['outputs'] == [output]
    assert Path(output).read_text() == source.read_text()


def test_template_rows_render_values_and_preserve_template(tmp_path):
    template = new_document('Letter', 'Hello {{name}}, your code is {{code}}. Again, {{name}}!')
    template['blocks'][0]['marks'] = [{'start': 0, 'end': 5, 'type': 'bold', 'value': True}]
    template['fields'] = {'name': {'type': 'text', 'required': True}, 'code': {'type': 'text'}}
    original = copy.deepcopy(template)
    source = tmp_path / 'recipients.csv'
    source.write_text('name,code\nAda,A-1\nGrace,G-2\n', encoding='utf-8')
    rows = list(template_rows(template, source))
    assert len(rows) == 2
    assert rows[0][0]['blocks'][0]['text'] == 'Hello Ada, your code is A-1. Again, Ada!'
    assert rows[1][0]['blocks'][0]['text'] == 'Hello Grace, your code is G-2. Again, Grace!'
    assert rows[0][0]['blocks'][0]['marks'][0] == original['blocks'][0]['marks'][0]
    assert len({template['id'], rows[0][0]['id'], rows[1][0]['id']}) == 3
    assert template == original
    native = tmp_path / 'template.byax'
    save_document(template, native)
    result = execute('template-batch', {'path': str(native), 'csv': str(source), 'out': str(tmp_path / 'letters'), 'format': 'txt'})
    assert result['failed'] == 0
    assert (tmp_path / 'letters' / 'document-0001.txt').read_text() == rows[0][0]['blocks'][0]['text']
    assert load_document(native) == template


def test_csv_rejects_ragged_data_and_stale_revision(tmp_path):
    doc, table = document_with_table()
    source = tmp_path / 'bad.csv'
    source.write_text('a,b\nc\n', encoding='utf-8')
    with pytest.raises(ByaxError, match='rectangular'):
        import_csv(doc, table['id'], source)
    source.write_text('a,b\nc,d\n', encoding='utf-8')
    with pytest.raises(ByaxError) as exc:
        import_csv(doc, table['id'], source, expected_revision='rev-stale')
    assert exc.value.code == 'stale_revision'


def test_affected_pages_return_explicit_incomplete_or_resolved_range(tmp_path):
    path = str(tmp_path / 'pages.byax')
    execute('new', {'out': path, 'text': 'A paragraph'})
    with pytest.raises(ByaxError) as exc:
        execute('render', {'path': path, 'pages': 'affected', 'out': str(tmp_path / 'before')})
    assert exc.value.code == 'incomplete_coverage'
    doc = load_document(path)
    execute('apply', {'path': path, 'input': {'operations': [{'type': 'insert_text', 'target': doc['blocks'][0]['id'], 'offset': 11, 'text': '.'}]}})
    rendered = execute('render', {'path': path, 'pages': 'affected', 'out': str(tmp_path / 'after')})
    assert rendered['selection']['base_revision'] == doc['revision']
    assert rendered['selection']['pages'] == [1]
    assert len(rendered['outputs']) == 1
    assert Path(rendered['outputs'][0]).is_file()


def test_template_csv_coerces_declared_types_and_locates_invalid_rows(tmp_path):
    template = new_document('Typed template', '{{count}} / {{price}} / {{enabled}} / {{date}} / {{tier}}')
    template['fields'] = {
        'count': {'type': 'integer', 'required': True},
        'price': {'type': 'number', 'required': True},
        'enabled': {'type': 'boolean', 'required': True},
        'date': {'type': 'date', 'required': True},
        'tier': {'type': 'enum', 'options': ['basic', 'pro'], 'required': True},
    }
    source = tmp_path / 'typed.csv'
    source.write_text('count,price,enabled,date,tier\n42,3.50,false,2026-10-13,pro\ninvalid,3.50,true,2026-10-13,basic\n', encoding='utf-8')
    rows = list(template_rows(template, source))
    assert rows[0][0]['fields']['count']['value'] == 42
    assert rows[0][0]['fields']['price']['value'] == 3.5
    assert rows[0][0]['fields']['enabled']['value'] is False
    assert rows[0][0]['fields']['date']['value'] == '2026-10-13'
    assert rows[0][0]['fields']['tier']['value'] == 'pro'
    assert rows[1][0] is None
    assert rows[1][1]['error']['field'] == 'count'
    assert rows[1][1]['error']['csv_row'] == 3
