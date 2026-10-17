"""Cascade stage 2: does the cited text support the claim? A local entailment model, advisory only.

Context engine plan 9. Stage 1 (:mod:`byax.grounding`) settles quotations and near-verbatim claims; a
paraphrase it cannot judge stays ``not_checkable``. This stage reads the claim and the cited text and
answers in three categories, never as a percentage:

- ``supported``: the cited text states what the claim says;
- ``partial``: part of the claim is stated and part is not (or the evidence is only middling);
- ``unsupported``: the cited text does not state the claim.

How it works
------------

The claim is split into sentences and clauses (:func:`byax.grounding.split_claims`). Each piece is
scored against the cited text by the on-device entailment model from the model catalogue (HHEM-2.1-Open,
:class:`byax.local_inference.EntailmentScorer`). A piece scoring at or above ``supported_at`` is
supported and one at or below ``unsupported_at`` is unsupported; anything between is partial. The claim
as a whole is supported only when every piece is, unsupported only when none is, and partial otherwise.
The thresholds are the ones chosen on the development half of the labelled set in
``tests/fixtures/entailment`` and are stored with the model in the catalogue. How often the result agrees
with human labels, including how often it calls an unsupported claim supported, is published in
``docs/benchmarks/0.4-entailment.json`` and in the library guide; the numbers are the measure of how far
to trust an underline.

What it never does
------------------

- It is local: no request is made, nothing is uploaded and no consent is needed. It runs only when asked
  (``byax check --citations --entailment``, or the AI tab's support check).
- It does not decide: the result is an advisory underline. A quotation is settled by stage 1 (this stage
  abstains when a quote check ran), and the optional LLM judge (:mod:`byax.grounding_judge`) may still
  be asked about the cases this stage calls partial or unsupported.
- It is honest when absent: without the optional runtime or the installed, verified model the check is
  recorded as ``not_run`` with the reason, and the command-line audit refuses with the remedy.
- The model's score never leaves this module: results, records and reports carry the category only.
"""

from __future__ import annotations

from . import local_models
from .errors import ByaxError
from .grounding import GroundingRecord, register_stage, split_claims, verify

VERDICTS = ('supported', 'partial', 'unsupported')
MIN_WORDS = 3
MAX_PREMISE_CHARS = 3500
SUPPORTED_AT = 0.8
UNSUPPORTED_AT = 0.65
BASIS = (
    'Checked on this computer by an entailment model against the cited text; advisory, and only as good as '
    'the agreement published in docs/benchmarks/0.4-entailment.json.'
)
SYMBOLS = {'supported': '✓', 'partial': '~', 'unsupported': '!'}
MEANINGS = {
    'supported': 'Supported: the cited text states this',
    'partial': 'Partly supported: only part of this is in the cited text',
    'unsupported': 'Not supported: the cited text does not state this',
}


def classify(score, supported_at=SUPPORTED_AT, unsupported_at=UNSUPPORTED_AT):
    """One piece of a claim: supported, partial or unsupported, from the model's consistency score."""
    if score >= supported_at:
        return 'supported'
    return 'unsupported' if score <= unsupported_at else 'partial'


def aggregate(verdicts):
    """The whole claim: supported only if every piece is, unsupported only if none is, else partial."""
    if all(v == 'supported' for v in verdicts):
        return 'supported'
    if all(v == 'unsupported' for v in verdicts):
        return 'unsupported'
    return 'partial'


def premise_window(text, span=None, limit=MAX_PREMISE_CHARS):
    """The part of the cited text the model reads: all of it, or ``limit`` characters around the span."""
    if len(text) <= limit:
        return text
    centre = (span[0] + span[1]) // 2 if span else limit // 2
    start = max(0, min(centre - limit // 2, len(text) - limit))
    end = start + limit
    # Start and end on a space so the window does not open or close in the middle of a word.
    if start and (cut := text.find(' ', start, start + 80)) != -1:
        start = cut + 1
    if end < len(text) and (cut := text.rfind(' ', end - 80, end)) != -1:
        end = cut
    return text[start:end]


def pieces(claim):
    """The checkable pieces of a claim: sentences and clauses with at least ``MIN_WORDS`` words."""
    return [c for c in split_claims(claim) if len(c['text'].split()) >= MIN_WORDS]


def availability(name=None, *, catalogue=None, directory=None):
    """Whether the stage can run, and if not exactly why. Cheap: reads the catalogue and file sizes only."""
    catalogue = catalogue or local_models.load_catalogue()
    name = name or local_models.load_settings()['entailer']
    runtime = local_models.runtime_status()
    try:
        status = local_models.model_status(name, catalogue=catalogue, directory=directory)
    except ByaxError as exc:
        return {'available': False, 'state': 'unknown_model', 'model': name, 'reason': exc.message}
    result = {
        'available': False,
        'state': status['state'],
        'model': name,
        'title': status['title'],
        'size_bytes': status['size_bytes'],
        'licence': status['licence'],
        'runtime': runtime['installed'],
    }
    if not runtime['installed']:
        result['reason'] = runtime['reason']
    elif status['state'] != 'installed':
        result['reason'] = (
            'Model not installed. Download it (Tools > On-device search…, or '
            f'`byax models --action download --name {name}`); nothing is sent about your writing.'
            if status['state'] == 'not_installed'
            else f'The {name} model is {status["state"]}: remove it and download it again.'
        )
    else:
        result['available'] = True
    return result


def installed_model(*, catalogue=None, directory=None):
    """The configured entailment model if it is installed, else any installed one, else the configured name."""
    catalogue = catalogue or local_models.load_catalogue()
    configured = local_models.load_settings()['entailer']
    names = [configured] + [
        m['name'] for m in catalogue['models'] if m['kind'] == 'entailment' and m['name'] != configured
    ]
    for name in names:
        if availability(name, catalogue=catalogue, directory=directory)['available']:
            return name
    return configured


class EntailmentStage:
    """A cascade stage callable: ``EntailmentStage(scorer)(record, source_text)``.

    ``scorer`` has ``score(pairs) -> [0..1]`` and ``identity`` (:class:`byax.local_inference.EntailmentScorer`,
    or any stand-in in tests). It abstains where stage 1 already decided: a record whose claim quotes the
    source (a quote check ran) or repeats it nearly verbatim.
    """

    def __init__(self, scorer, *, supported_at=SUPPORTED_AT, unsupported_at=UNSUPPORTED_AT):
        self.scorer = scorer
        self.supported_at = supported_at
        self.unsupported_at = unsupported_at
        self.scored = 0

    @property
    def model(self):
        return self.scorer.identity

    def assess(self, claim, premise):
        """``[(piece, score, verdict)]`` for the pieces of ``claim`` read against ``premise``."""
        found = pieces(claim)
        scores = self.scorer.score([(premise, c['text']) for c in found]) if found else []
        self.scored += len(found)
        return [
            (c, score, classify(score, self.supported_at, self.unsupported_at))
            for c, score in zip(found, scores, strict=True)
        ]

    def __call__(self, record, text):
        decided = any(
            (c.get('stage') == 'quote' and c.get('result') in VERDICTS)
            or (c.get('stage') == 'fuzzy' and c.get('result') == 'supported')
            for c in record.checks
        )
        if decided or not (record.claim or '').strip() or not (text or '').strip():
            return None
        assessed = self.assess(record.claim, premise_window(text, record.source_span))
        if not assessed:
            return None
        verdicts = [verdict for _, _, verdict in assessed]
        return {
            'stage': 'entailment',
            'result': aggregate(verdicts),
            'model': self.model,
            'claims': [
                {'text': c['text'], 'start': c['start'], 'end': c['end'], 'result': verdict}
                for c, _, verdict in assessed
            ],
            'basis': BASIS,
        }


def make_stage(*, name=None, catalogue=None, directory=None):
    """The stage for the installed model; raises ``missing_dependency`` or ``model_missing`` with the remedy."""
    from .local_inference import load_entailer

    catalogue = catalogue or local_models.load_catalogue()
    name = name or installed_model(catalogue=catalogue, directory=directory)
    state = availability(name, catalogue=catalogue, directory=directory)
    if not state['available']:
        code = 'missing_dependency' if not state.get('runtime', True) else 'model_missing'
        raise ByaxError(code, state['reason'], name=name, extra='byauthorx[local-models]')
    entry = local_models.get_model(name, catalogue)
    parameters = entry.get('runtime', {})
    return EntailmentStage(
        load_entailer(name, catalogue=catalogue, directory=directory),
        supported_at=float(parameters.get('supported_at', SUPPORTED_AT)),
        unsupported_at=float(parameters.get('unsupported_at', UNSUPPORTED_AT)),
    )


def try_stage(*, name=None, catalogue=None, directory=None):
    """The stage when the model is installed and the runtime present, else ``None`` (never raises for absence)."""
    try:
        return make_stage(name=name, catalogue=catalogue, directory=directory)
    except ByaxError as exc:
        if exc.code in ('missing_dependency', 'model_missing'):
            return None
        raise


def default_stage(record, text):
    """The stage registered in the cascade: loads the installed model on first use, else ``not_run``."""
    try:
        stage = _default()
    except ByaxError as exc:
        return {'stage': 'entailment', 'result': 'not_run', 'model': None, 'reason': exc.message}
    return stage(record, text)


_DEFAULT: dict = {}


def _default():
    name = installed_model()
    if _DEFAULT.get('name') != name or 'stage' not in _DEFAULT:
        _DEFAULT.clear()
        _DEFAULT.update(name=name, stage=make_stage(name=name))
    return _DEFAULT['stage']


def install():
    """Register the stage in the cascade (idempotent; :func:`byax.grounding.verify` calls it on demand)."""
    register_stage('entailment', default_stage)


install()


# --- running the stage over replies and audits -------------------------------------------------------------


def entail_records(records, stage, texts):
    """Run the stage over already verified records; returns updated ``GroundingRecord`` copies."""
    return verify(records, ('entailment',), texts=texts, overrides={'entailment': stage})


def entail_grounding(grounded, *, passages=None, references=None, stage=None):
    """Add support verdicts to a :func:`byax.grounding.ground_reply` result (a new dict).

    Without ``stage`` the installed model is used; when it is absent the result is returned unchanged with
    ``entailment`` saying why, so an assistant reply is never lost to a missing model.
    """
    from .grounding import STAGE_ORDER, source_texts, summarize

    if stage is None:
        state = availability()
        stage = try_stage() if state['available'] else None
        if stage is None:
            return {**grounded, 'entailment': {**state, 'available': False, 'checked': 0}}
    checked = entail_records(grounded['records'], stage, source_texts(passages, references))
    stages = [name for name in STAGE_ORDER if name in {*grounded.get('stages', []), 'entailment'}]
    return {
        **grounded,
        'records': [r.to_dict() for r in checked],
        'summary': summarize(checked),
        'stages': stages,
        'entailment': {'available': True, 'model': stage.model, 'checked': stage.scored},
    }


def entail_proposals(proposals, *, passages=None, references=None, stage=None):
    """Support verdicts for what each proposed edit would add: one grounding dict (or ``None``) per proposal.

    The cited claims of an edit are its ``replacement`` text with its ``[[cite:ID]]`` tokens; an edit that
    cites nothing has nothing to check (``None``). Nothing is attached to the proposal itself, so what is
    registered for Review stays exactly what the model proposed.
    """
    from .grounding import ground_reply

    stage = stage or try_stage()
    if stage is None:
        return [None] * len(proposals)
    results = []
    for proposal in proposals:
        grounded = ground_reply(proposal.get('replacement', ''), passages=passages, references=references)
        results.append(
            entail_grounding(grounded, passages=passages, references=references, stage=stage)
            if grounded['records']
            else None
        )
    return results


def underlines(record, text):
    """Where to underline an answer: ``[{start, end, result}]`` in ``text``, from the record's last check.

    Offsets are positions in the displayed reply (``text``); the claim is located from the record's
    ``answer_span`` and, if the text has changed since, by searching for it. A piece that cannot be
    found is skipped, never guessed.
    """
    record = GroundingRecord.from_dict(record)
    check = next(
        (c for c in reversed(record.checks) if c.get('stage') == 'entailment' and c.get('claims')), None
    )
    claim = (record.claim or '').strip()
    if not check or not claim:
        return []
    base = -1
    if record.answer_span:
        begin, end = record.answer_span
        segment = text[begin:end]
        if segment.strip() == claim:
            base = begin + len(segment) - len(segment.lstrip())
    if base == -1:
        base = text.find(claim)
    if base == -1:
        return []
    marks = []
    for item in check['claims']:
        start, end = base + item['start'], base + item['end']
        if text[start:end] == item['text']:
            marks.append({'start': start, 'end': end, 'result': item['result']})
    return marks


def audit_entailment(report, doc, stage, *, registry=None, cache_dir=None):
    """Check the writer's paraphrase citations in a citation audit (a new report; nothing is applied).

    Quotations are settled by stage 1 and skipped here, as is a paraphrase of a source that changed since
    it was cited (review that first). The cited text is read locally: the indexed chunk(s) the locator
    names, or the reference's own excerpt. Each checked item gains ``entailment`` (the check) and its
    record the check and state; a paraphrase whose text cannot be found gains a ``not_run`` check with the
    reason. No consent is needed because nothing leaves this computer.
    """
    from .grounding import audit_source_text, summarize

    items, checked = [], 0
    for item in report['citations']:
        item = dict(item)
        if not item.get('quote') and item.get('status') == 'not_checkable':
            record = GroundingRecord.from_dict(item['record'])
            text, reason = audit_source_text(item, doc, registry=registry, cache_dir=cache_dir)
            if text:
                record.claim = record.claim or item['text']
                updated = entail_records([record], stage, {record.key or item['source_id']: text})[0]
                check = next((c for c in reversed(updated.checks) if c.get('stage') == 'entailment'), None)
                if check:
                    item['entailment'] = check
                    item['record'] = updated.to_dict()
                    checked += check['result'] in VERDICTS
            else:
                item['entailment'] = {
                    'stage': 'entailment',
                    'result': 'not_run',
                    'model': stage.model,
                    'reason': reason,
                }
        items.append(item)
    records = [GroundingRecord.from_dict(i['record']) for i in items]
    support = dict.fromkeys((*VERDICTS, 'not_run'), 0)
    for item in items:
        if item.get('entailment'):
            support[item['entailment']['result']] += 1
    return {
        **report,
        'citations': items,
        'stages_run': [*report.get('stages_run', []), 'entailment'],
        'entailment': {'available': True, 'model': stage.model, 'checked': checked, 'support': support},
        'record_summary': summarize(records),
        'review': 'required' if support['partial'] or support['unsupported'] else report.get('review'),
        'deterministic': False,
        'advisory': True,
        'note': 'Stage 1 (quote match, fuzzy alignment) plus the on-device entailment check of paraphrases: '
        'advisory categories (supported, partial, unsupported), not proof; agreement with human labels is '
        'in docs/benchmarks/0.4-entailment.json. Proposed operations change nothing until applied.',
    }
