"""Writing goals, sessions and streaks (CP A3): library, CLI, transports and history isolation."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from byax import engine, goals, project
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.mcp import handle
from byax.model import new_document, text_of
from byax.statistics import counts
from byax.storage import load_document, save_document
from byax.transport_policy import check_provider_admin


def _h(identity, text, level):
    return {
        'id': identity,
        'type': 'heading',
        'text': text,
        'style': f'heading{level}',
        'marks': [],
        'attrs': {'level': level},
    }


def _p(identity, text):
    return {'id': identity, 'type': 'paragraph', 'text': text, 'style': 'body', 'marks': [], 'attrs': {}}


def manuscript():
    doc = new_document('Novel')
    doc['blocks'] = [
        _h('ch1', 'Chapter one', 1),
        _h('s1', 'Arrival', 2),
        _p('s1a', 'one two three four five'),
        _h('s2', 'The keeper', 2),
        _p('s2a', 'six seven eight'),
    ]
    doc, _ = engine.apply(doc, {'operations': [{'type': 'project_init'}]})
    return doc


def at(day, hour=9):
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def finished(store, doc_id, day, written, hour=9, start=100):
    session = goals.Session(doc_id, start, started=at(day, hour))
    session.update(start + written, now=at(day, hour + 1))
    return session.save(store)


@pytest.fixture
def store(tmp_path):
    return goals.GoalStore(tmp_path / 'config')


def test_goals_are_empty_until_set_and_validated(store):
    doc = manuscript()
    status = goals.status(doc, store, today=date(2026, 10, 16))
    assert status['daily'] == {
        'goal': None,
        'date': '2026-10-16',
        'written': 0,
        'remaining': None,
        'progress': None,
        'met': False,
    }
    assert status['document']['target'] is None and status['session']['goal'] is None
    for bad in (0, -1, 10_000_001, 1.5, '5', True):
        with pytest.raises(ByaxError):
            store.set_goal(doc['id'], 'daily_words', bad)
    with pytest.raises(ByaxError):
        store.set_goal(doc['id'], 'other', 5)
    store.set_goal(doc['id'], 'daily_words', 500, title='Novel')
    store.set_goal(doc['id'], 'session_words', 200)
    assert store.document(doc['id'])['daily_words'] == 500
    store.set_goal(doc['id'], 'session_words', None)
    assert store.document(doc['id'])['session_words'] is None


def test_progress_uses_the_shared_authored_counter_and_binder_targets(store):
    doc = manuscript()
    items = {row['title']: row['id'] for row in project.items(doc)}
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'project_set_target', 'words': 80},
                {'type': 'project_set_target', 'target': items['Arrival'], 'words': 5},
                {'type': 'project_set_target', 'target': items['Chapter one'], 'words': 20},
            ]
        },
    )
    status = goals.status(doc, store, today=date(2026, 10, 16))
    assert status['document']['words'] == counts(text_of(doc))['words'] == 13  # heading words count
    assert status['document']['target'] == 80 and status['document']['progress'] == round(13 / 80, 4)
    by_title = {entry['title']: entry for entry in status['items']}
    assert by_title['Arrival']['met'] is True and by_title['Arrival']['remaining'] == 0
    assert by_title['Chapter one']['words'] == 13 and by_title['Chapter one']['remaining'] == 7
    assert 'The keeper' not in by_title
    assert goals.status(doc, store, format='txt')['basis'] == 'txt'


def test_sessions_accumulate_per_day_and_never_go_negative(store):
    doc = manuscript()
    store.set_goal(doc['id'], 'daily_words', 300)
    today = date(2026, 10, 16)
    finished(store, doc['id'], today, 120)
    finished(store, doc['id'], today, 250, hour=14)
    shrink = goals.Session(doc['id'], 500, started=at(today, 18))
    shrink.update(300, now=at(today, 19))
    assert shrink.written == 0 and shrink.save(store) == {}
    status = goals.status(doc, store, today=today)
    assert status['daily']['written'] == 370 and status['daily']['met'] and status['daily']['remaining'] == 0
    live = goals.status(doc, store, today=today, live_written=30)
    assert live['daily']['written'] == 400
    record = store.document(doc['id'])
    assert record['days'][today.isoformat()] == {'written': 370, 'sessions': 2}
    assert len(record['sessions']) == 2


def test_a_session_is_updated_in_place_not_duplicated(store):
    doc = manuscript()
    today = date(2026, 10, 16)
    session = goals.Session(doc['id'], 100, started=at(today))
    assert session.update(150, now=at(today, 10)) is True
    assert session.save(store)['written'] == 50
    assert session.update(150, now=at(today, 11)) is False
    assert session.update(220, now=at(today, 12)) is True
    day = session.save(store)
    assert day['written'] == 120 and day['sessions'] == 1
    assert len(store.document(doc['id'])['sessions']) == 1


def test_streaks_count_consecutive_days_meeting_the_goal(store):
    doc = manuscript()
    today = date(2026, 10, 16)
    store.set_goal(doc['id'], 'daily_words', 100)
    for offset, written in ((6, 150), (5, 100), (4, 90), (2, 200), (1, 120)):
        finished(store, doc['id'], today - timedelta(days=offset), written)
    # Today is not written yet: the streak runs through yesterday and today does not break it.
    status = goals.status(doc, store, today=today)
    assert status['streak'] == {'current': 2, 'best': 2, 'basis': 'goal'}
    finished(store, doc['id'], today, 130)
    assert goals.status(doc, store, today=today)['streak']['current'] == 3
    store.set_goal(doc['id'], 'daily_words', None)
    anyway = goals.status(doc, store, today=today)['streak']
    assert anyway == {'current': 3, 'best': 3, 'basis': 'any_writing'}
    assert goals.streak({}, None, today)['current'] == 0
    assert goals.streak({today.isoformat(): {'written': 1}}, None, today)['current'] == 1


def test_history_lists_days_newest_first_with_goal_flags(store):
    doc = manuscript()
    today = date(2026, 10, 16)
    store.set_goal(doc['id'], 'daily_words', 100)
    finished(store, doc['id'], today - timedelta(days=1), 40)
    finished(store, doc['id'], today, 140)
    report = goals.history(doc, store, today=today)
    assert [d['date'] for d in report['days']] == ['2026-10-16', '2026-10-15']
    assert [d['met'] for d in report['days']] == [True, False]
    assert report['sessions'][0]['written'] == 140 and len(report['sessions']) == 2
    assert goals.history(doc, store, limit=1, today=today)['days'][0]['date'] == '2026-10-16'


def test_session_records_never_touch_the_document_or_its_history(store, tmp_path):
    doc = manuscript()
    path = tmp_path / 'novel.byax'
    save_document(doc, path)
    before_bytes = path.read_bytes()
    before = load_document(path)
    finished(store, doc['id'], date(2026, 10, 16), 400)
    goals.status(before, store)
    goals.history(before, store)
    after = load_document(path)
    assert path.read_bytes() == before_bytes
    assert after['revision'] == before['revision'] and after['history'] == before['history']
    assert 'goals' not in json.dumps(after['history']) and 'sessions' not in json.dumps(after)
    assert store.path.is_file() and store.path.parent != path.parent


def test_the_store_is_keyed_by_document_identity_and_survives_bad_files(store, tmp_path):
    first, second = manuscript(), manuscript()
    assert first['id'] != second['id']
    store.set_goal(first['id'], 'daily_words', 100)
    assert store.document(second['id'])['daily_words'] is None
    store.path.write_text('{not json', encoding='utf-8')
    with pytest.raises(ByaxError) as caught:
        store.document(first['id'])
    assert caught.value.code == 'execution_failure'
    store.path.write_text('[]', encoding='utf-8')
    with pytest.raises(ByaxError):
        store.set_goal(first['id'], 'daily_words', 5)


def test_old_days_and_sessions_are_bounded(store, monkeypatch):
    doc = manuscript()
    monkeypatch.setattr(goals, 'MAX_SESSIONS', 3)
    monkeypatch.setattr(goals, 'MAX_DAYS', 4)
    today = date(2026, 10, 16)
    for offset in range(8):
        finished(store, doc['id'], today - timedelta(days=offset), 10 + offset)
    record = store.document(doc['id'])
    assert len(record['sessions']) == 3 and len(record['days']) == 4
    assert min(record['days']) == (today - timedelta(days=3)).isoformat()


def test_cli_status_history_and_local_goal_setting(tmp_path, capsys):
    doc = manuscript()
    path = tmp_path / 'novel.byax'
    save_document(doc, path)
    config = str(tmp_path / 'config')
    base = {'path': str(path), 'config_dir': config}
    result = execute('goals', {**base, 'action': 'set-daily', 'words': 250})
    assert result['goals']['daily']['goal'] == 250
    result = execute('goals', {**base, 'action': 'set-session', 'words': 100})
    assert result['goals']['session']['goal'] == 100
    finished(goals.GoalStore(config), doc['id'], datetime.now().astimezone().date(), 260)
    status = execute('goals', base)
    assert status['goals']['daily']['met'] and status['goals']['streak']['current'] == 1
    assert execute('goals', {**base, 'action': 'history'})['history']['days'][0]['written'] == 260
    cleared = execute('goals', {**base, 'action': 'set-daily', 'clear': True})
    assert cleared['goals']['daily']['goal'] is None
    with pytest.raises(ByaxError):
        execute('goals', {**base, 'action': 'set-daily'})
    with pytest.raises(ByaxError):
        execute('goals', {**base, 'action': 'nope'})
    assert main(['goals', str(path), '--config-dir', config, '--action', 'status']) == 0
    assert json.loads(capsys.readouterr().out)['goals']['daily']['goal'] is None
    # Reading goals never changes the document.
    assert load_document(path)['revision'] == doc['revision']


def test_transports_may_read_goals_but_not_write_local_preferences(tmp_path):
    for action in ('set-daily', 'set-session'):
        with pytest.raises(ByaxError) as caught:
            check_provider_admin('goals', {'action': action, 'words': 5})
        assert caught.value.code == 'unsupported_feature'
    for action in ('status', 'history'):
        check_provider_admin('goals', {'action': action})
    path = tmp_path / 'n.byax'
    save_document(manuscript(), path)
    call = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {
                'name': 'byax_goals',
                'arguments': {'path': str(path), 'action': 'set-daily', 'words': 9},
            },
        }
    )['result']
    assert call['isError'] is True
    assert call['structuredContent']['error']['code'] == 'unsupported_feature'


def test_rest_reads_goals_from_the_service_owned_folder_and_refuses_writes(tmp_path):
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from byax.service import make_server

    token = 'goals-test-token-with-32-characters'
    path = tmp_path / 'n.byax'
    save_document(manuscript(), path)
    server = make_server([tmp_path], token=token, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(arguments):
        request = Request(
            f'http://127.0.0.1:{server.server_port}/v1/execute',
            data=json.dumps({'command': 'goals', 'arguments': arguments}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + token},
        )
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        status, body = call({'path': str(path)})
        assert status == 200 and body['goals']['daily']['goal'] is None
        status, body = call({'path': str(path), 'action': 'set-daily', 'words': 100})
        assert status >= 400 and body['error']['code'] == 'unsupported_feature'
        # The preferences of the person at this computer were not touched.
        assert not goals.GoalStore().path.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
