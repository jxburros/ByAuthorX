"""Bounded CSV exchange through canonical cell operations.

CSV contains values only. Import retains existing table, row and cell identities,
attributes, layout and annotations. Growth is explicitly requested and uses the
same add_row/add_column operations as desktop editing.
"""

from __future__ import annotations

import codecs
import copy
import csv
import math
import os
import tempfile
from pathlib import Path

from .errors import ByaxError
from .model import find_object, new_id

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 1000
MAX_CELLS = 100000


def _options(encoding, dialect, delimiter=None, newline=None):
    try:
        codecs.lookup(encoding)
        csv.get_dialect(dialect)
    except (LookupError, csv.Error, TypeError) as exc:
        raise ByaxError('invalid_input', 'Unknown CSV encoding or dialect') from exc
    options = {'dialect': dialect, 'strict': True}
    if delimiter is not None:
        if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in '\r\n\x00':
            raise ByaxError('invalid_input', 'CSV delimiter must be one non-newline character')
        options['delimiter'] = delimiter
    if newline is not None:
        if newline not in ('lf', 'crlf', '\n', '\r\n'):
            raise ByaxError('invalid_input', 'CSV newline must be lf or crlf')
        options['lineterminator'] = {'lf': '\n', 'crlf': '\r\n'}.get(newline, newline)
    return options


def read_csv(path, *, encoding='utf-8-sig', dialect='excel', delimiter=None):
    options = _options(encoding, dialect, delimiter)
    source = Path(path)
    if source.stat().st_size > MAX_BYTES:
        raise ByaxError('limit_exceeded', 'CSV source exceeds 16 MiB')
    rows, columns, cells, characters = [], None, 0, 0
    blank = False
    try:
        with source.open(encoding=encoding, newline='') as handle:
            for row in csv.reader(handle, **options):
                if not row:
                    blank = True
                    continue
                if blank:
                    # Only trailing blank lines are tolerated.
                    raise ByaxError(
                        'invalid_input', 'CSV rows must be rectangular and nonempty', row=len(rows) + 1
                    )
                if columns is None:
                    columns = len(row)
                if not row or len(row) != columns:
                    raise ByaxError(
                        'invalid_input', 'CSV rows must be rectangular and nonempty', row=len(rows) + 1
                    )
                cells += len(row)
                characters += sum(len(value) for value in row)
                if characters > MAX_BYTES:
                    raise ByaxError('limit_exceeded', 'CSV decoded values exceed 16 MiB')
                if len(rows) >= MAX_ROWS or len(row) > MAX_COLUMNS or cells > MAX_CELLS:
                    raise ByaxError('limit_exceeded', 'CSV exceeds 10000 rows, 1000 columns, or 100000 cells')
                if any('\x00' in value for value in row):
                    raise ByaxError(
                        'invalid_input', 'CSV cells cannot contain NUL characters', row=len(rows) + 1
                    )
                # CSV readers retain quoted CRLF/CR characters. Rich document
                # text uses LF soft breaks regardless of the source platform.
                rows.append([value.replace('\r\n', '\n').replace('\r', '\n') for value in row])
    except (UnicodeError, csv.Error, LookupError) as exc:
        raise ByaxError('invalid_input', f'CSV decoding failed: {exc}') from exc
    if not rows:
        raise ByaxError('invalid_input', 'CSV contains no rows')
    return rows


def _table(doc, table_id):
    table = find_object(doc, table_id)
    if table.get('type') != 'table':
        raise ByaxError('invalid_input', 'CSV target must be a table', target=table_id)
    rows, columns = len(table['rows']), len(table['rows'][0]['cells'])
    if rows > MAX_ROWS or columns > MAX_COLUMNS or rows * columns > MAX_CELLS:
        raise ByaxError('limit_exceeded', 'Table exceeds CSV exchange dimension limits')
    return table


def import_csv(
    doc,
    table_id,
    path,
    expected_revision=None,
    *,
    grow=False,
    encoding='utf-8-sig',
    dialect='excel',
    delimiter=None,
    dry_run=False,
    request_id=None,
):
    from .engine import apply, revision_document

    previous = doc.get('requests', {}).get(request_id) if request_id else None
    if previous:
        base = previous['result']['base_revision']
        expected_revision = expected_revision or base
        table = _table(revision_document(doc, base), table_id)
    else:
        table = _table(doc, table_id)
    values = read_csv(path, encoding=encoding, dialect=dialect, delimiter=delimiter)
    old_rows, old_columns = len(table['rows']), len(table['rows'][0]['cells'])
    new_rows, new_columns = len(values), len(values[0])
    if (new_rows, new_columns) != (old_rows, old_columns) and (
        not grow or new_rows < old_rows or new_columns < old_columns
    ):
        raise ByaxError(
            'invalid_input',
            'CSV dimensions must match the table; explicit growth may add rows/columns but never removes existing cells',
            expected={'rows': old_rows, 'columns': old_columns},
            actual={'rows': new_rows, 'columns': new_columns},
            target=table_id,
        )
    operations = []
    for ri, row in enumerate(table['rows']):
        for ci, cell in enumerate(row['cells']):
            if cell.get('text', '') != values[ri][ci]:
                operations.append(
                    {
                        'type': 'update_cell',
                        'target': cell['id'],
                        'text': values[ri][ci],
                        'expected_text': cell.get('text', ''),
                    }
                )
    for ci in range(old_columns, new_columns):
        operations.append(
            {'type': 'add_column', 'target': table_id, 'values': [values[ri][ci] for ri in range(old_rows)]}
        )
    for ri in range(old_rows, new_rows):
        operations.append({'type': 'add_row', 'target': table_id, 'values': values[ri]})
    # Keep no-op requests in the canonical revision/precondition path.
    if not operations:
        cell = table['rows'][0]['cells'][0]
        operations = [
            {'type': 'update_cell', 'target': cell['id'], 'text': cell['text'], 'expected_text': cell['text']}
        ]
    request = {
        'operations': operations,
        'expected_revision': expected_revision or doc['revision'],
        'origin': 'csv',
    }
    if request_id:
        request['request_id'] = request_id
    candidate, result = apply(doc, request, dry_run=dry_run)
    result['csv'] = {
        'input': str(Path(path).resolve()),
        'encoding': encoding,
        'dialect': dialect,
        'delimiter': delimiter or csv.get_dialect(dialect).delimiter,
        'rows': new_rows,
        'columns': new_columns,
        'values_only': True,
        'existing_identities_preserved': True,
        'growth': {'rows': new_rows - old_rows, 'columns': new_columns - old_columns},
    }
    return candidate, result


def export_csv(
    doc,
    table_id,
    path,
    *,
    encoding='utf-8',
    dialect='excel',
    delimiter=None,
    newline=None,
    overwrite=False,
    owner=None,
):
    from .storage import DocumentLock

    table = _table(doc, table_id)
    options = _options(encoding, dialect, delimiter, newline)
    destination = Path(path)
    with DocumentLock(destination, owner=owner):
        if destination.exists() and not overwrite:
            raise ByaxError(
                'conflict', 'CSV output exists; explicit overwrite is required', target=str(destination)
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.byax-csv-', dir=destination.parent) as temporary:
            stage = Path(temporary) / 'table.csv'
            try:
                with stage.open('w', encoding=encoding, newline='') as handle:
                    writer = csv.writer(handle, **options)
                    for row in table['rows']:
                        writer.writerow([cell.get('text', '') for cell in row['cells']])
            except (UnicodeError, csv.Error, LookupError) as exc:
                raise ByaxError('invalid_input', f'CSV encoding failed: {exc}') from exc
            if stage.stat().st_size > MAX_BYTES:
                raise ByaxError('limit_exceeded', 'CSV output exceeds 16 MiB')
            os.replace(stage, destination)
    return {
        'schema_version': 1,
        'execution': 'completed',
        'validation': 'not_run',
        'document_id': doc['id'],
        'revision': doc['revision'],
        'outputs': [str(destination.resolve())],
        'coverage': {'values': 'complete', 'formatting': 'not represented by CSV'},
        'rows': len(table['rows']),
        'columns': len(table['rows'][0]['cells']),
        'encoding': encoding,
        'dialect': dialect,
        'newline': options.get('lineterminator', csv.get_dialect(dialect).lineterminator),
        'losses': [
            {
                'code': 'csv_values_only',
                'target': table_id,
                'message': 'CSV contains table values; styles, identities and annotations remain in the native document.',
            }
        ],
    }


def _typed_value(value, definition, field, row):
    if not isinstance(definition, dict) or value == '':
        return value
    kind = definition.get('type', 'text')
    try:
        if kind == 'integer':
            return int(value.strip())
        if kind == 'number':
            number = float(value.strip())
            if not math.isfinite(number):
                raise ValueError('number must be finite')
            return number
        if kind in ('boolean', 'bool'):
            normalized = value.strip().lower()
            if normalized not in ('true', 'false', '1', '0'):
                raise ValueError('boolean must be true/false or 1/0')
            return normalized in ('true', '1')
        if kind == 'date':
            from datetime import date

            return date.fromisoformat(value.strip()).isoformat()
        if kind in ('enum', 'choice'):
            options = definition.get('options', definition.get('choices', []))
            matches = [option for option in options if str(option) == value]
            if len(matches) != 1:
                raise ValueError('value must match exactly one declared choice')
            return matches[0]
        return value
    except (ValueError, TypeError, OverflowError) as exc:
        raise ByaxError(
            'invalid_input', f'CSV field does not match its declared {kind} type', field=field, csv_row=row
        ) from exc


def template_rows(template, path, *, encoding='utf-8-sig', dialect='excel', delimiter=None):
    """Yield independent filled documents; the first CSV row names template fields."""
    from .engine import apply

    values = read_csv(path, encoding=encoding, dialect=dialect, delimiter=delimiter)
    headers = values[0]
    if any(not header or header.strip() != header for header in headers) or len(set(headers)) != len(headers):
        raise ByaxError(
            'invalid_input', 'CSV field headers must be nonempty, unique, and have no surrounding whitespace'
        )
    if len(values) == 1:
        raise ByaxError('invalid_input', 'Template CSV needs at least one data row')
    for row_number, row in enumerate(values[1:], start=2):
        candidate = copy.deepcopy(template)
        candidate['id'], candidate['revision'] = new_id('doc'), new_id('rev')
        candidate['history'] = {'revisions': [], 'branches': {}, 'current_branch': 'main'}
        candidate['requests'] = {}
        data = dict(zip(headers, row, strict=True))
        try:
            fields = {
                key: (
                    {
                        **template['fields'][key],
                        'value': _typed_value(value, template['fields'][key], key, row_number),
                    }
                    if isinstance(template['fields'].get(key), dict)
                    else value
                )
                for key, value in data.items()
            }
            candidate, result = apply(
                candidate,
                {
                    'operations': [{'type': 'fill_fields', 'values': fields}],
                    'required_suites': ['requirements'],
                },
            )
            result['csv_row'] = row_number
            yield candidate, result
        except ByaxError as exc:
            yield None, {'csv_row': row_number, 'execution': 'failed', 'error': exc.to_dict()}
