"""Formatting, lists and independent style inheritance (3.2); external dependencies (5.2)."""

import base64

import pytest

from byax import engine
from byax.checks import run_checks
from byax.dependencies import refresh_operations, retrieve
from byax.errors import ByaxError
from byax.formatting import element_report, resolve_lists, style_report
from byax.model import new_document
from byax.storage import load_document, save_document

INLINE = {
    'bold': True,
    'italic': True,
    'underline': True,
    'strike': True,
    'superscript': True,
    'color': '#aa0000',
    'highlight': '#ffff00',
    'link': 'https://example.com',
    'font_family': 'DejaVu Serif',
    'font_size': 14,
}
PARAGRAPH = {
    'alignment': 'justify',
    'indent': 36,
    'first_line_indent': -18,
    'line_spacing': 1.5,
    'space_before': 6,
    'space_after': 12,
    'tabs': [72, 144],
}


def edit(doc, *ops, **envelope):
    return engine.apply(doc, {'operations': list(ops), **envelope})


def test_every_inline_and_paragraph_format_survives_save_reopen_and_layout(tmp_path):
    from byax.layout import build_document

    doc = new_document(text='Formatted\tparagraph with subscript')
    target = doc['blocks'][0]['id']
    ops = [{'type': 'format_text', 'target': target, 'start': 0, 'end': 9, 'marks': INLINE}]
    ops.append(
        {'type': 'format_text', 'target': target, 'start': 25, 'end': 34, 'marks': {'subscript': True}}
    )
    ops.append({'type': 'set_attributes', 'target': target, 'values': PARAGRAPH})
    doc, _ = edit(doc, *ops)
    path = tmp_path / 'formats.byax'
    save_document(doc, path)
    reopened = load_document(path)
    assert reopened['blocks'] == doc['blocks']
    assert {m['type'] for m in reopened['blocks'][0]['marks']} == set(INLINE) | {'subscript'}
    qdoc = build_document(reopened)  # keep the document alive while its blocks are used
    block = qdoc.firstBlock()
    assert block.blockFormat().textIndent() == -18 and len(block.blockFormat().tabPositions()) == 2
    report = element_report(reopened, reopened['blocks'][0])
    assert {o['property'] for o in report['overrides']} >= set(PARAGRAPH) - {'tabs'} | {'tabs'}


def test_list_identity_levels_restart_and_numbering_survive_split_join_undo_and_reopen(tmp_path):
    doc = new_document(text='One\nNested\nTwo\nAside\nThree\nFresh')
    ids = [b['id'] for b in doc['blocks']]
    doc, _ = edit(
        doc,
        {'type': 'set_list', 'target': ids[0], 'values': {'kind': 'ordered', 'new_list': True}},
        {'type': 'set_list', 'target': ids[1], 'values': {'kind': 'ordered', 'level': 2}},
        {'type': 'set_list', 'target': ids[2], 'values': {'kind': 'ordered'}},
        {'type': 'set_list', 'target': ids[4], 'values': {'kind': 'ordered'}},
        {
            'type': 'set_list',
            'target': ids[5],
            'values': {'kind': 'ordered', 'new_list': True, 'restart': True},
        },
        {'type': 'format_text', 'target': ids[2], 'start': 0, 'end': 3, 'marks': {'bold': True}},
    )
    numbering = {item['id']: (item['level'], item['number']) for item in resolve_lists(doc)}
    assert numbering == {ids[0]: (1, 1), ids[1]: (2, 1), ids[2]: (1, 2), ids[4]: (1, 3), ids[5]: (1, 1)}
    list_id = doc['blocks'][0]['attrs']['list']['list_id']
    assert (
        doc['blocks'][4]['attrs']['list']['list_id']
        == list_id
        != doc['blocks'][5]['attrs']['list']['list_id']
    )
    base = doc['revision']
    # Splitting a restarted first item keeps one list; the continuation does not restart again.
    split, _result = edit(doc, {'type': 'split_block', 'target': ids[5], 'offset': 3, 'id': 'p_split'})
    split_numbers = {i['id']: i['number'] for i in resolve_lists(split)}
    assert split_numbers[ids[5]] == 1 and split_numbers['p_split'] == 2
    assert 'restart' not in split['blocks'][-1]['attrs']
    split2, _ = edit(split, {'type': 'split_block', 'target': ids[2], 'offset': 2, 'id': 'p_two'})
    assert next(m for m in split2['blocks'][3]['marks'] if m['type'] == 'bold')['end'] == 1
    assert {i['id']: i['number'] for i in resolve_lists(split2)}[ids[4]] == 4
    joined, _ = edit(split2, {'type': 'join_blocks', 'target': ids[2], 'next': 'p_two'})
    assert joined['blocks'][2]['marks'] == doc['blocks'][2]['marks']
    assert resolve_lists(joined)[:4] == resolve_lists(doc)[:4]
    restored, _ = engine.restore(split, base)
    assert resolve_lists(restored) == resolve_lists(doc)
    path = tmp_path / 'lists.byax'
    save_document(joined, path)
    assert resolve_lists(load_document(path)) == resolve_lists(joined)
    removed, _ = edit(doc, {'type': 'set_list', 'target': ids[2], 'values': {'kind': None}})
    assert 'list' not in removed['blocks'][2]['attrs']
    assert engine.inspect(removed, section='lists')['items'][2]['number'] == 2


def test_character_and_paragraph_styles_inherit_independently_with_override_reports():
    doc = new_document(text='Quoted words here\nPlain')
    target, plain = (b['id'] for b in doc['blocks'])
    doc, _ = edit(
        doc,
        {'type': 'define_style', 'name': 'emphasis', 'values': {'kind': 'character', 'italic': True}},
        {
            'type': 'define_style',
            'name': 'strong_emphasis',
            'values': {'kind': 'character', 'based_on': 'emphasis', 'bold': True},
        },
        {
            'type': 'define_style',
            'name': 'pull_quote',
            'values': {'based_on': 'quotation', 'alignment': 'center'},
        },
        {'type': 'apply_style', 'target': target, 'style': 'pull_quote'},
        {
            'type': 'apply_character_style',
            'target': target,
            'start': 7,
            'end': 12,
            'style': 'strong_emphasis',
        },
        {
            'type': 'format_text',
            'target': target,
            'start': 7,
            'end': 12,
            'marks': {'italic': False, 'color': '#00f'},
        },
        {'type': 'set_attributes', 'target': target, 'values': {'alignment': 'right'}},
    )
    report = element_report(doc, doc['blocks'][0])
    assert report['chain'] == ['pull_quote', 'quotation', 'body']
    assert report['overrides'] == [
        {'property': 'alignment', 'style_value': 'center', 'local_value': 'right', 'source': 'paragraph'}
    ]
    run = report['runs'][0]
    assert run['character_chain'] == ['strong_emphasis', 'emphasis']
    assert (
        run['effective']['bold'] is True and run['effective']['italic'] is True
    )  # italic False mark is removal
    assert run['overrides'] == [
        {'property': 'color', 'style_value': None, 'local_value': '#00f', 'source': 'text'}
    ]
    for bad in (
        {'type': 'define_style', 'name': 'mixed', 'values': {'kind': 'character', 'based_on': 'body'}},
        {
            'type': 'define_style',
            'name': 'emphasis',
            'values': {'kind': 'character', 'based_on': 'strong_emphasis'},
        },
        {'type': 'apply_style', 'target': plain, 'style': 'emphasis'},
        {'type': 'apply_character_style', 'target': plain, 'start': 0, 'end': 5, 'style': 'quotation'},
    ):
        with pytest.raises(ByaxError) as error:
            edit(doc, bad)
        assert error.value.code == 'schema_error'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'define_style', 'name': 'pull_quote', 'values': {'kind': 'character'}})
    assert error.value.code == 'conflict' and error.value.details['affected_ids'] == [target]
    # Changing an ancestor reports every dependent element without touching content.
    changed, result = edit(
        doc, {'type': 'define_style', 'name': 'emphasis', 'values': {'kind': 'character', 'underline': True}}
    )
    assert target in result['changed_ids'] and plain not in result['changed_ids']
    assert [b['text'] for b in changed['blocks']] == [b['text'] for b in doc['blocks']]
    changed, result = edit(doc, {'type': 'define_style', 'name': 'body', 'values': {'font_size': 12}})
    assert {target, plain} <= set(result['changed_ids'])
    styles = {s['name']: s for s in style_report(doc)}
    assert styles['emphasis']['dependents'] == ['strong_emphasis'] and styles['emphasis']['affected_ids'] == [
        target
    ]
    assert engine.inspect(doc, section='formatting', target=target)['items'][0]['has_overrides']
    # Broken imported style references are reported instead of failing inspection.
    doc['styles']['orphan'] = {'based_on': 'nowhere'}
    doc['blocks'][1]['style'] = 'orphan'
    assert element_report(doc, doc['blocks'][1])['style_problems'][0]['style'] == 'orphan'
    assert {s['name']: s for s in style_report(doc)}['orphan']['problems']


def test_clear_formatting_keeps_text_semantic_marks_and_structure_and_is_tracked():
    doc = new_document(text='Linked bold words')
    target = doc['blocks'][0]['id']
    doc['blocks'][0]['marks'] = [
        {'type': 'link', 'value': 'https://example.com', 'start': 0, 'end': 6},
        {'type': 'bold', 'value': True, 'start': 0, 'end': 17},
        {'type': 'field', 'value': 'name', 'start': 12, 'end': 17},
    ]
    doc['blocks'][0]['attrs'] = {'alignment': 'center', 'indent': 20, 'list': {'kind': 'bullet', 'level': 1}}
    doc, _ = edit(doc, {'type': 'set_settings', 'values': {'track_changes': True}})
    cleared, _ = edit(
        doc, {'type': 'clear_formatting', 'target': target, 'start': 7, 'end': 11, 'scope': 'text'}
    )
    assert sorted((m['type'], m['start'], m['end']) for m in cleared['blocks'][0]['marks']) == [
        ('bold', 0, 7),
        ('bold', 11, 17),
        ('field', 12, 17),
        ('link', 0, 6),
    ]
    cleared, _ = edit(cleared, {'type': 'clear_formatting', 'target': target})
    block = cleared['blocks'][0]
    assert block['text'] == 'Linked bold words' and block['attrs'] == {'list': {'kind': 'bullet', 'level': 1}}
    assert {m['type'] for m in block['marks']} == {'link', 'field'}
    change = cleared['review']['tracked_changes'][-1]
    restored, _ = edit(cleared, {'type': 'reject_change', 'target': change['id']})
    assert restored['blocks'][0]['attrs']['alignment'] == 'center'


@pytest.mark.parametrize(
    'style', ['title', 'subtitle', 'body', 'heading1', 'heading6', 'quotation', 'caption', 'code', 'list']
)
def test_common_styles_apply_without_changing_content(style):
    doc = new_document(text='Same text')
    changed, _ = edit(doc, {'type': 'apply_style', 'target': doc['blocks'][0]['id'], 'style': style})
    assert changed['blocks'][0]['text'] == 'Same text' and changed['blocks'][0]['style'] == style


PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
    '0000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082'
)


def test_linked_images_are_inspectable_refreshed_with_authorization_and_embedded(tmp_path):
    image = tmp_path / 'chart.png'
    image.write_bytes(PNG)
    doc = new_document(text='Report')
    doc, result = edit(
        doc, {'type': 'link_asset', 'uri': 'chart.png', 'id': 'dep_chart', 'mime': 'image/png'}
    )
    doc, result = edit(doc, {'type': 'place_linked_image', 'link': 'dep_chart', 'alt': 'Chart'})
    block = result['changed_ids'][0]
    findings = run_checks(doc, ['structure'])['findings']
    assert [(f['rule'], f['affected_ids']) for f in findings] == [('dependency.unresolved', [block])]
    report = engine.dependency_report(doc)
    assert report['dependencies'][0]['used_by'] == [block] and report['findings'][0]['target'] == block
    with pytest.raises(ByaxError) as error:
        refresh_operations(doc, base_dir=tmp_path)
    assert error.value.code == 'review_required'
    ops, outcomes = refresh_operations(doc, base_dir=tmp_path, authorize=True)
    assert outcomes[0]['status'] == 'retrieved'
    doc, result = edit(doc, *ops)
    dependency = doc['dependencies']['dep_chart']
    digest = dependency['sha256']
    assert dependency['status'] == 'current' and doc['assets'][digest]['dependency'] == 'dep_chart'
    assert doc['blocks'][1]['attrs']['asset'] == digest and block in result['changed_ids']
    assert run_checks(doc, ['structure'])['findings'] == []
    # A changed source updates hashes, drops the stale cache and invalidates findings.
    doc['review']['findings'] = [{'id': 'f1', 'status': 'open', 'affected_ids': [block]}]
    image.write_bytes(PNG + b'\x00')
    doc, _ = edit(doc, *refresh_operations(doc, base_dir=tmp_path, authorize=True)[0])
    dependency = doc['dependencies']['dep_chart']
    assert dependency['previous_sha256'] == digest and dependency['content_changed']
    assert digest not in doc['assets'] and doc['review']['findings'][0]['status'] == 'stale'
    # Missing sources keep the cached copy and report a located warning.
    image.unlink()
    ops, outcomes = refresh_operations(doc, base_dir=tmp_path, authorize=True)
    assert ops[0]['type'] == 'mark_dependency_missing' and outcomes[0]['used_by'] == [block]
    missing, _ = edit(doc, *ops)
    finding = run_checks(missing, ['structure'])['findings'][0]
    assert finding['rule'] == 'dependency.missing' and finding['affected_ids'] == [block]
    # Linked policy survives save/reopen and clean publication.
    from byax.publication import export_clean

    for policy_doc, policy in (
        (doc, 'link'),
        (edit(doc, {'type': 'embed_dependency', 'target': 'dep_chart'})[0], 'embedded'),
    ):
        path = tmp_path / f'{policy}.byax'
        save_document(policy_doc, path)
        assert load_document(path)['dependencies']['dep_chart']['policy'] == policy
        clean = tmp_path / f'{policy}-clean.byax'
        export_clean(policy_doc, clean)
        published = load_document(clean)
        assert published['dependencies']['dep_chart']['policy'] == policy
        assert published['blocks'][1]['attrs']['asset'] in published['assets']
        assert ('link' in published['blocks'][1]['attrs']) == (policy == 'link')
    embedded = load_document(tmp_path / 'embedded.byax')
    with pytest.raises(ByaxError) as error:
        edit(
            embedded,
            {'type': 'refresh_dependency', 'target': 'dep_chart', 'data': base64.b64encode(PNG).decode()},
        )
    assert error.value.code == 'conflict'


def test_network_and_unsafe_dependencies_require_explicit_permission():
    doc = new_document()
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'link_asset', 'uri': 'https://user:secret@example.com/a.png'})
    assert error.value.code == 'invalid_input'
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'link_asset', 'uri': 'javascript:alert(1)'})
    assert error.value.code == 'schema_error'
    doc, _ = edit(doc, {'type': 'link_asset', 'uri': 'https://example.com/a.png', 'id': 'remote'})
    dependency = doc['dependencies']['remote']
    with pytest.raises(ByaxError) as error:
        retrieve(dependency, authorize=True)
    assert error.value.code == 'review_required' and 'network' in error.value.message
    with pytest.raises(ByaxError) as error:
        edit(doc, {'type': 'embed_dependency', 'target': 'remote'})
    assert error.value.code == 'conflict'
    assert engine.inspect(doc, section='dependencies')['items'][0]['status'] == 'unresolved'


def test_html_import_records_external_images_as_located_dependencies(tmp_path):
    from byax.exchange import import_document

    source = tmp_path / 'page.html'
    source.write_text('<p>Intro</p><img src="https://example.com/remote.png" alt="Remote">', encoding='utf-8')
    doc, _ = import_document(source)
    image = next(b for b in doc['blocks'] if b['type'] == 'image')
    dependency = doc['dependencies'][image['attrs']['link']]
    assert dependency['uri'] == 'https://example.com/remote.png' and dependency['status'] == 'unresolved'
    rules = [(f['rule'], f['affected_ids']) for f in run_checks(doc, ['structure'])['findings']]
    assert ('dependency.unresolved', [image['id']]) in rules
