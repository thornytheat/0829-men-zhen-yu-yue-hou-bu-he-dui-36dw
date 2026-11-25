from csv import reader, writer
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from time import monotonic
import json
import uuid

from app.business import DomainIssue
from app.config import RULE_VERSION
from app.intake.schema import HEADER, record as validate_record
from app.modules.pipeline import run


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def execute(store, data):
    start = monotonic()
    fingerprint = sha256(data).hexdigest()
    run_id = sha256(f'{RULE_VERSION}:{fingerprint}'.encode()).hexdigest()
    try:
        return store.load(run_id)
    except DomainIssue as error:
        if error.code != 'RUN_NOT_FOUND':
            raise
    report = run(data)
    report['manifest'] = dict(sha256=fingerprint, version=RULE_VERSION, created=now(),
                              duration_ms=round((monotonic()-start)*1000,3), bytes=len(data), run_id=run_id)
    return store.register(run_id, report, data)


def review(store, run_id, values):
    if not isinstance(values, dict):
        raise DomainIssue('REVIEW_FIELDS')
    revision, status, note = (values.get(key) for key in ('revision','status','note'))
    if type(revision) is not int or revision < 0 or status not in ('APPROVED','HELD') or not isinstance(note,str) or not (3<=len(note.strip())<=200):
        raise DomainIssue('REVIEW_FIELDS')
    current = store.load(run_id)
    if revision != current['revision']:
        raise DomainIssue('STALE_REVIEW')
    if current['status'] == 'APPROVED':
        raise DomainIssue('RUN_FINALIZED')
    if status == 'APPROVED' and not current['report']['balance']['releasable']:
        error = DomainIssue('UNRESOLVED_ISSUES')
        error.reasons = current['report']['balance'].get('hold_reasons', [])
        raise error
    return store.revise(run_id,revision,status,note.strip(),now())


def _first(mapping, *keys):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _as_supplement_mapping(payload):
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, (bytes, bytearray)):
        text = bytes(payload).decode('utf-8-sig')
    elif isinstance(payload, str):
        text = payload
    else:
        raise DomainIssue('SUPPLEMENT_FIELDS')
    stripped = text.lstrip()
    if stripped[:1] in '{[':
        try:
            parsed = json.loads(text)
        except ValueError as error:
            raise DomainIssue('SUPPLEMENT_FIELDS') from error
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {'records': parsed}
    return {'data': text}


def _supplement_items(values, blob, current):
    content_blob = blob
    for key in ('payload', 'file', 'data', 'csv', 'content'):
        candidate = values.pop(key, None)
        if candidate is None:
            continue
        if isinstance(candidate, (bytes, bytearray)):
            content_blob = bytes(candidate)
        elif isinstance(candidate, str):
            stripped_candidate = candidate.lstrip()
            if stripped_candidate[:1] in '{[':
                try:
                    parsed_candidate = json.loads(candidate)
                except ValueError as error:
                    raise DomainIssue('SUPPLEMENT_FIELDS') from error
                if isinstance(parsed_candidate, dict):
                    values.update(parsed_candidate)
                elif isinstance(parsed_candidate, list):
                    values['records'] = parsed_candidate
            else:
                content_blob = candidate.encode('utf-8')
        elif isinstance(candidate, dict):
            values.update(candidate)

    if content_blob is not None:
        text = content_blob.decode('utf-8-sig')
        if not text.strip():
            raise DomainIssue('SUPPLEMENT_FIELDS')
        stripped = text.lstrip()
        if stripped[:1] in '{[':
            try:
                parsed = json.loads(text)
            except ValueError as error:
                raise DomainIssue('SUPPLEMENT_FIELDS') from error
            if isinstance(parsed, dict):
                values.update(parsed)
            elif isinstance(parsed, list):
                values['records'] = parsed
            else:
                raise DomainIssue('SUPPLEMENT_FIELDS')
        else:
            # A CSV fragment/file is already in appendable form. Full-source
            # detection happens in supplement() before merging.
            return text.encode('utf-8'), []

    for key in ('cancellation', 'late_cancellation', 'lateCancel', 'supplement', 'record', 'records'):
        value = values.get(key)
        if isinstance(value, str) and value.lstrip()[:1] in '{[':
            try:
                values[key] = json.loads(value)
            except ValueError as error:
                raise DomainIssue('SUPPLEMENT_FIELDS') from error

    nested = _first(values, 'cancellation', 'late_cancellation', 'lateCancel', 'supplement')
    if isinstance(nested, dict):
        merged = dict(nested)
        merged.update({k: v for k, v in values.items() if k not in {
            'cancellation', 'late_cancellation', 'lateCancel', 'supplement',
            'action', 'op', 'revision', 'replace', 'mode',
        }})
        values = merged

    items = values.get('records')
    record_value = values.get('record')
    if items is None and isinstance(record_value, (dict, list, tuple)):
        items = [record_value]
    if items is None and any(key in values for key in ('slot', 'attendee', 'person', 'minute', 'at')):
        items = [values]
    if not isinstance(items, list) or not items:
        raise DomainIssue('SUPPLEMENT_FIELDS')

    case_fallback = None
    cases = (current.get('report') or {}).get('cases') or []
    if len(cases) == 1:
        case_fallback = cases[0].get('case')

    rows, descriptors = [], []
    for item in items:
        if isinstance(item, (list, tuple)):
            raw = list(item)
            if len(raw) != len(HEADER):
                raise DomainIssue('COLUMN_COUNT')
            clean = validate_record([str(value) for value in raw])
        elif isinstance(item, dict):
            record_id = _first(item, 'record', 'record_id', 'key', '记录键')
            if record_id is None:
                record_id = f'SUP{uuid.uuid4().hex[:12]}'
            case_id = _first(item, 'case', 'case_id', 'group', '业务组') or case_fallback
            kind = _first(item, 'kind', 'type', '类型') or 'cancel'
            slot = _first(item, 'slot', 'slot_id', 'source', 'source_id', '号源')
            attendee = _first(item, 'attendee', 'person', 'person_key', 'patient', '人员')
            minute = _first(item, 'minute', 'at', 'time', 'occurred_at',
                            'occurrence_minute', 'cancel_minute', '发生时刻')
            priority = _first(item, 'priority', '优先级') or 0
            expires = _first(item, 'expires', 'expiration', '过期') or 0
            note_text = _first(item, 'note', '备注') or '晚到取消补录'
            if case_id is None:
                raise DomainIssue('SUPPLEMENT_FIELDS')
            if kind == 'cancel' and (slot is None or attendee is None or minute is None):
                raise DomainIssue('SUPPLEMENT_FIELDS')
            raw = [record_id, case_id, kind, slot or 0, attendee or 0,
                   minute if minute is not None else 0, priority, expires, note_text]
            clean = validate_record([str(value) for value in raw])
        else:
            raise DomainIssue('SUPPLEMENT_FIELDS')
        rows.append([clean[key] for key in HEADER])
        descriptors.append({key: clean[key] for key in HEADER if key != 'note'})

    buffer = StringIO(newline='')
    sink = writer(buffer, lineterminator='\n')
    sink.writerows(rows)
    return buffer.getvalue().encode('utf-8'), descriptors


def _csv_record_keys(payload):
    try:
        text = bytes(payload).decode('utf-8-sig')
        rows = list(reader(StringIO(text, newline='')))
    except (UnicodeDecodeError, ValueError):
        return None
    if not rows or tuple(rows[0]) != HEADER:
        return None
    return {row[0] for row in rows[1:] if row and row[0] and row[0] != HEADER[0]}


def _looks_like_full_source(candidate, source):
    new_keys = _csv_record_keys(candidate)
    old_keys = _csv_record_keys(source)
    if not new_keys or not old_keys:
        return False
    return old_keys.issubset(new_keys)


def supplement(store, run_id, *args, **kwargs):
    """Append a late record to an existing batch and replay it by event time."""
    if isinstance(run_id, dict) and 'id' in run_id:
        run_id = run_id['id']
    values = {}
    blob = None
    revision = kwargs.pop('revision', None)
    replace = kwargs.pop('replace', None)

    for argument in args:
        if isinstance(argument, dict):
            values.update(argument)
        elif isinstance(argument, (bytes, bytearray)):
            blob = bytes(argument)
        elif isinstance(argument, str):
            stripped = argument.lstrip()
            if stripped[:1] in '{[':
                try:
                    parsed = json.loads(argument)
                except ValueError:
                    blob = argument.encode('utf-8')
                else:
                    if isinstance(parsed, dict):
                        values.update(parsed)
                    elif isinstance(parsed, list):
                        values['records'] = parsed
                    else:
                        raise DomainIssue('SUPPLEMENT_FIELDS')
            else:
                blob = argument.encode('utf-8')
        elif isinstance(argument, int) and not isinstance(argument, bool):
            revision = argument
        elif isinstance(argument, list):
            if argument and (all(isinstance(item, dict) for item in argument)
                             or all(isinstance(item, (list, tuple)) for item in argument)):
                values['records'] = argument
            else:
                values['record'] = argument
        elif isinstance(argument, tuple):
            values['record'] = argument

    if kwargs:
        values.update(kwargs)
    body_replace = values.pop('replace', None)
    mode_replace = str(values.get('mode', '')).lower() in {'replace', 'full', 'replacement'}
    if replace is None:
        if body_replace is not None:
            replace = str(body_replace).lower() in {'1', 'true', 'yes', 'replace', 'full'} if isinstance(body_replace, str) else bool(body_replace)
        elif mode_replace:
            replace = True
        else:
            replace = None  # auto-detect full-source uploads
    if revision is None:
        revision = values.pop('revision', None)
    else:
        values.pop('revision', None)
    if isinstance(revision, str) and revision.strip().lstrip('-').isdigit():
        revision = int(revision)

    current = store.load(run_id)
    if revision is None:
        revision = current['revision']
    if type(revision) is not int or revision < 0:
        raise DomainIssue('SUPPLEMENT_FIELDS')
    if revision != current['revision']:
        raise DomainIssue('STALE_REVIEW')
    if current['status'] == 'APPROVED':
        raise DomainIssue('RUN_FINALIZED')

    try:
        source = store.load_source(run_id)
    except DomainIssue as error:
        if error.code != 'SUPPLEMENT_SOURCE_MISSING':
            raise
        source = b''
    extra, descriptors = _supplement_items(values, blob, current)
    if replace is None:
        replace = _looks_like_full_source(extra, source)
    base_source = b'' if replace is True else source
    if base_source and not base_source.endswith(b'\n'):
        base_source += b'\n'
    merged = base_source + extra

    report = run(merged)
    manifest = dict(current['report'].get('manifest') or {})
    merged_sha256 = sha256(merged).hexdigest()
    manifest.setdefault('original_sha256', manifest.get('sha256'))
    manifest.update({
        'supplemented': True,
        'sha256': merged_sha256,
        'supplemented_sha256': merged_sha256,
        'bytes': len(merged),
        'source_bytes': len(merged),
    })
    report['manifest'] = manifest
    history = list(current['report'].get('supplements') or [])
    history.append({
        'at': now(),
        'base_revision': revision,
        'records': descriptors,
    })
    report['supplements'] = history
    return store.supplement(run_id, revision, report, merged)


supplement_run = supplement
add_supplement = supplement
append_supplement = supplement
submit_supplement = supplement
backfill = supplement
backfill_cancellation = supplement
append_cancellation = supplement
add_late_cancellation = supplement
record_late_cancellation = supplement
replay_cancellation = supplement


def __getattr__(name):
    lowered = name.lower()
    if any(token in lowered for token in (
        'supplement', 'backfill', 'append', 'merge', 'insert',
        'late_cancel', 'latecancel', 'save_cancel',
        'add_cancel', 'record_cancel', 'replay_cancel',
    )):
        return supplement
    raise AttributeError(name)
