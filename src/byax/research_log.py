"""Research log for multi-step retrieval (route R3) and conversation export (context engine 8.4, L1).

The log is PRISMA-style: every query the loop issued, how many candidates each returned, which
candidates were reranked and kept, which were dropped and why, and, once the reply is in, which kept
items the reply cited. It is plain data, deterministic for equal input (no clock), and exportable
with the conversation as Markdown or JSON.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .errors import ByaxError

FORMATS = ('markdown', 'json')


class ResearchLog:
    """Steps of one routed request. ``route`` is the route the log belongs to (R2 or R3)."""

    def __init__(self, route: str, request: str):
        self.route = route
        self.request = request
        self.steps: list[dict[str, Any]] = []
        self.reranked: dict[str, Any] = {}
        self.kept: list[str] = []
        self.dropped: list[dict[str, Any]] = []
        self.cited: list[str] = []
        self.outcome: str | None = None

    def query(self, tool: str, arguments: dict[str, Any], candidates: int, *, notice: str | None = None):
        step = {
            'step': len(self.steps) + 1,
            'tool': tool,
            'arguments': copy.deepcopy(arguments),
            'candidates': candidates,
        }
        if notice:
            step['notice'] = notice
        self.steps.append(step)
        return step

    def rerank(self, method: str, candidates: int, order: list[str]):
        self.reranked = {'method': method, 'candidates': candidates, 'order': list(order)}

    def keep(self, identities: list[str], dropped: list[dict[str, Any]] | None = None):
        self.kept = list(identities)
        self.dropped = copy.deepcopy(dropped or [])

    def cite(self, identities: list[str]):
        """Record which kept items the reply cited (grounding record chunk IDs)."""
        kept = set(self.kept)
        self.cited = [i for i in dict.fromkeys(identities) if i in kept]

    def to_dict(self) -> dict[str, Any]:
        total = sum(step['candidates'] for step in self.steps)
        return {
            'route': self.route,
            'request': self.request,
            'steps': copy.deepcopy(self.steps),
            'flow': {
                'queries': len(self.steps),
                'candidates': total,
                'reranked': self.reranked.get('candidates', 0),
                'kept': len(self.kept),
                'cited': len(self.cited),
            },
            'reranked': copy.deepcopy(self.reranked),
            'kept': list(self.kept),
            'dropped': copy.deepcopy(self.dropped),
            'cited': list(self.cited),
            'outcome': self.outcome,
        }


def cite_from_grounding(log: dict[str, Any], grounding: dict[str, Any] | None) -> dict[str, Any]:
    """A log dict updated with the chunk IDs a reply's grounding records cite (kept items only)."""
    result = copy.deepcopy(log)
    records = (grounding or {}).get('records') or []
    cited = [r.get('chunk_id') or r.get('citation_id') for r in records if r.get('valid_id', True)]
    kept = set(result.get('kept', []))
    result['cited'] = [i for i in dict.fromkeys(cited) if i in kept]
    result.setdefault('flow', {})['cited'] = len(result['cited'])
    return result


def log_markdown(log: dict[str, Any]) -> str:
    """One research log as a Markdown section."""
    flow = log.get('flow', {})
    lines = [
        f'### Research log ({log.get("route")})',
        '',
        f'Request: {log.get("request", "")}',
        '',
        (
            f'Queries {flow.get("queries", 0)} → candidates {flow.get("candidates", 0)} → reranked '
            f'{flow.get("reranked", 0)} → kept {flow.get("kept", 0)} → cited {flow.get("cited", 0)}'
        ),
        '',
        '| Step | Tool | Arguments | Candidates |',
        '| --- | --- | --- | --- |',
    ]
    for step in log.get('steps', []):
        arguments = json.dumps(step.get('arguments', {}), ensure_ascii=False, sort_keys=True)
        note = f' ({step["notice"]})' if step.get('notice') else ''
        lines.append(f'| {step["step"]} | {step["tool"]} | `{arguments}` | {step["candidates"]}{note} |')
    if log.get('reranked'):
        lines += [
            '',
            f'Reranked with {log["reranked"].get("method")}: {log["reranked"].get("candidates")} candidates.',
        ]
    if log.get('kept'):
        lines += ['', 'Kept: ' + ', '.join(log['kept'])]
    if log.get('dropped'):
        lines += ['', 'Dropped:']
        lines += [f'- {d.get("id", d.get("chunk_id", "?"))}: {d.get("reason", "")}' for d in log['dropped']]
    lines += ['', 'Cited: ' + (', '.join(log['cited']) if log.get('cited') else 'none')]
    if log.get('outcome'):
        lines += ['', f'Outcome: {log["outcome"]}']
    return '\n'.join(lines)


def export_conversation(
    messages: list[dict[str, Any]], turns: list[dict[str, Any]], fmt: str = 'markdown'
) -> str:
    """The conversation with each turn's route, inspector summary and research log.

    ``messages`` are the user/assistant turns; ``turns`` are per-request records
    ``{'request', 'route', 'inspector', 'research_log'}`` in order. Deterministic for equal input.
    """
    if fmt not in FORMATS:
        raise ByaxError('invalid_input', 'Export format must be markdown or json', supported=list(FORMATS))
    if fmt == 'json':
        return json.dumps(
            {'format': 'byax-conversation', 'version': 1, 'messages': messages, 'turns': turns},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    lines = ['# Assistant conversation', '']
    for message in messages:
        who = 'You' if message.get('role') == 'user' else 'Assistant'
        lines += [f'**{who}:** {message.get("content", "")}', '']
    if turns:
        lines += ['## What the AI saw', '']
    for number, turn in enumerate(turns, 1):
        route = turn.get('route') or {}
        lines += [
            f'### Request {number}: {route.get("route", "?")} {route.get("name", "")}'.rstrip(),
            '',
            f'Request: {turn.get("request", "")}',
            '',
            f'Reason: {route.get("reason", "")}',
            '',
        ]
        inspector = turn.get('inspector') or {}
        for item in inspector.get('items', []):
            lines.append(
                f'- [{item.get("tier")}] {item.get("label")} · {item.get("tokens")} tokens · '
                f'{item.get("score_source") or "—"} · {item.get("where", "")}'
            )
        for dropped in inspector.get('dropped', []):
            lines.append(
                f'- [dropped] {dropped.get("label", dropped.get("id", ""))}: {dropped.get("reason")}'
            )
        lines.append('')
        if turn.get('research_log'):
            lines += [log_markdown(turn['research_log']), '']
    return '\n'.join(lines).rstrip() + '\n'
