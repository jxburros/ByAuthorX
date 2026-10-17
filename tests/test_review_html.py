"""The HTML review copy in a real browser (Chromium through Playwright): offline, phone-sized, accessible.

These tests drive the page the way a reader does and feed what it exports back into the importer. They are
skipped when the ``playwright`` package (a dev dependency) or a Chromium build is not available.
Set ``BYAX_TEST_CHROMIUM`` to a Chromium executable to choose the browser.
"""

import glob
import json
import os
from pathlib import Path

import pytest
from docx_word import PARAGRAPHS, sample_document

from byax import beta_readers, engine

playwright_sync = pytest.importorskip('playwright.sync_api')


def _chromium_candidates():
    explicit = os.environ.get('BYAX_TEST_CHROMIUM')
    root = os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
    found = sorted(glob.glob(os.path.join(root, 'chromium-*', 'chrome-linux*', 'chrome')), reverse=True)
    return ([explicit] if explicit else []) + found


@pytest.fixture(scope='module')
def browser():
    with playwright_sync.sync_playwright() as playwright:
        args = ['--no-sandbox'] if hasattr(os, 'geteuid') and os.geteuid() == 0 else []
        launched = None
        for candidate in [None, *_chromium_candidates()]:
            try:
                launched = playwright.chromium.launch(executable_path=candidate, args=args)
                break
            except Exception:  # noqa: BLE001, S112 - any launch failure means this browser is unusable here
                continue
        if launched is None:
            pytest.skip('no Chromium available for Playwright')
        yield launched
        launched.close()


@pytest.fixture
def copy(tmp_path):
    """``(document, review_id, path)`` of a sent HTML copy; a paragraph with an astral character is added."""
    doc = sample_document()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'insert_paragraph',
                    'text': '\U0001f600 She smiled, then left.',
                    'after': 'p_7',
                    'id': 'p_8',
                },
            ]
        },
    )
    data, operations, info = beta_readers.prepare_beta_copy(
        doc, 'html', reviewer='Sam Reader', note='Read it kindly.'
    )
    doc, _ = engine.apply(doc, {'operations': operations})
    path = tmp_path / 'copy.html'
    path.write_bytes(data)
    return doc, info['review_id'], path


def reader_page(browser, path, *, width=1100, height=800, **options):
    context = browser.new_context(
        viewport={'width': width, 'height': height}, accept_downloads=True, **options
    )
    page = context.new_page()
    page.requests, page.failed, page.errors = [], [], []
    page.on('requestfinished', lambda request: page.requests.append(request.url))
    page.on('requestfailed', lambda request: page.failed.append((request.url, request.failure)))
    page.on('pageerror', lambda error: page.errors.append(str(error)))
    page.goto(Path(path).as_uri())
    return page


def select(page, block, start, end):
    page.evaluate(
        """([block, start, end]) => {
            const el = document.querySelector(`[data-b="${block}"]`);
            const range = document.createRange();
            range.setStart(el.firstChild, start);
            range.setEnd(el.firstChild, end);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }""",
        [block, start, end],
    )


def comment_on_selection(page, text, *, name='Sam Reader', kind=None, replacement=None):
    page.click('#add')
    if page.evaluate("document.getElementById('who').open"):
        page.fill('#who-input', name)
        page.click('#who-form button[type=submit]')
    if kind:
        page.check(f'#kinds input[value="{kind}"]')
    if replacement is not None:
        page.fill('#c-repl', replacement)
    page.fill('#c-text', text)
    page.click('#c-save')


def download(page, tmp_path, button='#export'):
    with page.expect_download() as info:
        page.click(button)
    target = tmp_path / 'download.json'
    info.value.save_as(target)
    return info.value.suggested_filename, target.read_bytes()


def test_the_page_loads_with_no_network_request_and_the_policy_blocks_every_fetch(browser, copy):
    _, _, path = copy
    page = reader_page(browser, path)
    assert page.errors == []
    assert all(url.startswith('file://') for url in page.requests), page.requests
    violations = page.evaluate(
        """async () => {
            const seen = [];
            document.addEventListener('securitypolicyviolation', (e) => seen.push(e.effectiveDirective));
            for (const url of ['http://example.invalid/x', 'https://example.invalid/y', 'data:text/plain,hi']) {
                try { await fetch(url); } catch (e) { /* refused */ }
            }
            await new Promise((done) => {
                const socket = new WebSocket('wss://example.invalid/');
                socket.onerror = socket.onclose = () => done();
            });
            const image = new Image();
            image.src = 'https://example.invalid/pixel.png';
            document.body.appendChild(Object.assign(document.createElement('iframe'), { src: 'https://example.invalid/' }));
            await new Promise((done) => setTimeout(done, 300));
            return seen;
        }"""
    )
    assert violations.count('connect-src') == 4 and 'img-src' in violations and 'frame-src' in violations
    # Every probe was stopped by the policy before it reached the network.
    assert all(url.startswith('file://') for url in page.requests), page.requests
    assert page.failed and all('csp' in str(failure).lower() for _, failure in page.failed), page.failed
    assert page.title() == 'The Lighthouse' and page.get_attribute('html', 'lang') == 'en'
    assert 'Read it kindly.' in page.inner_text('.note')
    page.context.close()


@pytest.mark.parametrize('width', [320, 375, 414])
def test_there_is_no_horizontal_scroll_on_a_phone_and_touch_targets_are_large(browser, copy, width):
    _, _, path = copy
    page = reader_page(browser, path, width=width, height=700, is_mobile=True, has_touch=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    for selector in ('#add', '#export2', '#jump'):
        box = page.locator(selector).bounding_box()
        assert box['height'] >= 44 and box['width'] >= 44
        assert box['x'] >= 0 and box['x'] + box['width'] <= width
    select(page, 'p_2', 7, 20)
    comment_on_selection(page, 'A long comment ' * 30)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.context.close()


def test_selecting_text_and_commenting_exports_a_valid_comment_file(browser, copy, tmp_path):
    doc, review_id, path = copy
    page = reader_page(browser, path)
    select(page, 'p_2', 7, 21)
    comment_on_selection(page, 'Lovely <b>image</b>')
    assert page.inner_text('#count') == '1' and page.errors == []
    assert 'the sea worked' in page.inner_text('#list')
    assert page.get_attribute('[data-b="p_2"]', 'data-n') == '1'
    name, raw = download(page, tmp_path)
    assert name == 'The Lighthouse.byaxcomments.json'
    parsed = beta_readers.validate_comment_file(raw)
    assert parsed['review_id'] == review_id and parsed['reader'] == 'Sam Reader'
    [comment] = parsed['comments']
    source = PARAGRAPHS[1]
    assert (comment['block'], comment['quote'], comment['text']) == (
        'p_2',
        'the sea worked',
        'Lovely <b>image</b>',
    )
    assert comment['position'] == {'start': 7, 'end': 21} and source[7:21] == comment['quote']
    assert comment['prefix'] == source[:7] and comment['suffix'].startswith(' at the rocks')
    data = json.loads(raw)
    assert (
        data['document_id'] == doc['id']
        and data['snapshot_hash'] == (doc['metadata']['review_packages'][review_id]['snapshot_hash'])
    )
    page.context.close()


def test_positions_are_scalar_offsets_even_after_an_emoji(browser, copy, tmp_path):
    _, _, path = copy
    page = reader_page(browser, path)
    text = '\U0001f600 She smiled, then left.'
    select(page, 'p_8', 3, 10)  # UTF-16 offsets: the emoji is two units, then a space
    comment_on_selection(page, 'Nice')
    _, raw = download(page, tmp_path)
    [comment] = beta_readers.validate_comment_file(raw)['comments']
    assert comment['quote'] == 'She smi'
    assert comment['position'] == {'start': 2, 'end': 9} and text[2:9] == comment['quote']
    page.context.close()


def test_comments_survive_a_reload_and_can_be_edited_and_deleted(browser, copy, tmp_path):
    _, _, path = copy
    page = reader_page(browser, path)
    select(page, 'p_3', 0, 3)
    comment_on_selection(page, 'First')
    page.reload()
    assert page.inner_text('#count') == '1' and page.inner_text('#who-name') == 'Sam Reader'
    page.click('#list button:has-text("Edit")')
    assert page.input_value('#c-text') == 'First'
    page.fill('#c-text', 'Second thoughts')
    page.click('#c-save')
    assert 'Second thoughts' in page.inner_text('#list')
    _, raw = download(page, tmp_path)
    assert [c['text'] for c in beta_readers.validate_comment_file(raw)['comments']] == ['Second thoughts']
    page.click('#list button:has-text("Delete")')
    assert page.inner_text('#count') == '0' and page.is_visible('#empty')
    page.reload()
    assert page.inner_text('#count') == '0'
    page.context.close()


def test_a_keyboard_reader_can_comment_on_a_paragraph_without_a_mouse(browser, copy, tmp_path):
    _, _, path = copy
    page = reader_page(browser, path)
    for _ in range(6):  # the skip link and the help come first; the text has a single tab stop
        page.keyboard.press('Tab')
        if page.evaluate("document.activeElement.hasAttribute('data-b')"):
            break
    first = page.evaluate("document.activeElement.getAttribute('data-b')")
    assert first
    page.keyboard.press('ArrowDown')
    second = page.evaluate("document.activeElement.getAttribute('data-b')")
    assert second and second != first
    page.keyboard.press('c')
    assert page.evaluate("document.getElementById('who').open")
    page.keyboard.type('Kim')
    page.keyboard.press('Enter')
    assert page.evaluate("document.getElementById('composer').open")
    assert page.evaluate('document.activeElement.id') == 'c-text'
    page.keyboard.type('Whole paragraph note')
    page.keyboard.press('Tab')
    page.keyboard.press('Enter')  # Save comment
    assert page.inner_text('#count') == '1'
    _, raw = download(page, tmp_path)
    [comment] = beta_readers.validate_comment_file(raw)['comments']
    assert comment['block'] == second and 'quote' not in comment
    page.keyboard.press('Escape')
    page.context.close()


def test_suggestions_round_trip_into_proposals(browser, copy, tmp_path):
    doc, _review_id, path = copy
    page = reader_page(browser, path)
    select(page, 'p_3', PARAGRAPHS[2].index('eleven years'), PARAGRAPHS[2].index('eleven years') + 12)
    comment_on_selection(page, 'Too long?', kind='replace', replacement='ten years')
    select(page, 'p_5', 0, 14)
    comment_on_selection(page, '', kind='delete')
    name, raw = download(page, tmp_path)
    file = tmp_path / name
    file.write_bytes(raw)
    kinds = [(c['kind'], c.get('replacement')) for c in json.loads(raw)['comments']]
    assert kinds == [('suggestion', 'ten years'), ('suggestion', '')]
    # The writer keeps writing while the readers read, then merges.
    doc, _ = engine.apply(
        doc, {'operations': [{'type': 'insert_text', 'target': 'p_3', 'offset': 0, 'text': 'Long ago '}]}
    )
    updated, report = beta_readers.merge_comments(doc, [file])
    proposals = [p for p in updated['review']['proposals'] if p['kind'] == 'beta_reader']
    assert len(proposals) == 2 and report['losses'] == []
    accepted, _ = engine.apply(
        updated, {'operations': [{'type': 'accept_proposal', 'target': proposals[0]['id']}]}
    )
    assert next(b for b in accepted['blocks'] if b['id'] == 'p_3')['text'].startswith('Long ago She had kept')
    assert 'ten years' in next(b for b in accepted['blocks'] if b['id'] == 'p_3')['text']
    page.context.close()


def test_what_the_reader_types_and_what_the_document_says_never_runs_as_markup(browser, tmp_path):
    doc = sample_document()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'insert_paragraph',
                    'text': '</script><script>window.__doc=1</script><img src=x onerror="window.__doc=2">',
                    'after': 'p_7',
                    'id': 'p_9',
                }
            ]
        },
    )
    data, _, _ = beta_readers.prepare_beta_copy(doc, 'html', note='<script>window.__doc=3</script>')
    path = tmp_path / 'hostile.html'
    path.write_bytes(data)
    page = reader_page(browser, path)
    assert page.evaluate('window.__doc') is None and page.errors == []
    assert '</script><script>window.__doc=1' in page.inner_text('[data-b="p_9"]')
    select(page, 'p_2', 0, 5)
    comment_on_selection(page, '<img src=x onerror="window.__reader=1">')
    assert page.evaluate('window.__reader') is None
    assert '<img src=x' in page.inner_text('#list')
    page.context.close()


def test_the_companion_copy_is_read_only_and_works_on_a_phone(browser, copy, tmp_path):
    doc, _, _ = copy
    data, operations, _ = beta_readers.prepare_beta_copy(doc, 'html', companion=True)
    path = tmp_path / 'companion.html'
    path.write_bytes(data)
    assert operations == []
    page = reader_page(browser, path, width=375, height=700, is_mobile=True, has_touch=True)
    assert page.errors == [] and all(url.startswith('file://') for url in page.requests)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert page.locator('#add, #composer, #comments, #export').count() == 0
    assert page.locator('script').count() == 0
    assert 'Chapter One' in page.inner_text('main') and 'Read-only copy' in page.text_content('.kicker')
    select(page, 'p_2', 0, 5)
    assert page.locator('dialog').count() == 0
    page.keyboard.press('c')
    assert page.locator('dialog[open]').count() == 0
    page.context.close()


def test_dark_mode_reduced_motion_and_accessible_names(browser, copy):
    _, _, path = copy
    page = reader_page(browser, path, color_scheme='dark', reduced_motion='reduce')
    background = page.evaluate('getComputedStyle(document.body).backgroundColor')
    channels = [int(v) for v in background[background.index('(') + 1 : -1].split(',')[:3]]
    assert max(channels) < 80
    assert page.evaluate('getComputedStyle(document.documentElement).scrollBehavior') == 'auto'
    problems = page.evaluate(
        """() => {
            const missing = [];
            for (const el of document.querySelectorAll('button, a[href], input, textarea')) {
                const label = (el.getAttribute('aria-label') || el.textContent || '').trim()
                    || (el.id && document.querySelector(`label[for="${el.id}"]`))
                    || el.closest('label');
                if (!label) { missing.push(el.outerHTML.slice(0, 80)); }
            }
            for (const dialog of document.querySelectorAll('dialog')) {
                if (!document.getElementById(dialog.getAttribute('aria-labelledby'))) { missing.push(dialog.id); }
            }
            return missing;
        }"""
    )
    assert problems == []
    assert page.get_attribute('#status', 'role') == 'status' and page.locator('a.skip').count() == 1
    assert page.locator('h1').count() == 1
    page.context.close()
    light = reader_page(browser, path, color_scheme='light')
    background = light.evaluate('getComputedStyle(document.body).backgroundColor')
    assert min(int(v) for v in background[background.index('(') + 1 : -1].split(',')[:3]) > 200
    light.context.close()
