"""Read models used by the workbench and JSON clients."""


def collect_placements(report):
    placements = []
    for case in (report or {}).get('cases', []):
        case_id = case.get('case')
        measure = case.get('measure') or {}
        logs = measure.get('placements') or measure.get('assignment_log')
        if logs:
            for item in logs:
                placement = dict(item)
                placement.setdefault('case', case_id)
                placements.append(placement)
            continue
        promoted = (measure.get('evidence') or {}).get('promoted') or {}
        for slot, attendee in promoted.items():
            placements.append({
                'case': case_id,
                'slot': int(slot) if isinstance(slot, str) and slot.isdigit() else slot,
                'attendee': attendee,
                'person': attendee,
            })
    return placements


def summarize_run(run):
    report = run.get('report') or {}
    placements = collect_placements(report)
    balance = report.get('balance') or {}
    raw_status = run.get('status')
    releasable = balance.get('releasable')
    if raw_status == 'APPROVED':
        effective_status = 'APPROVED'
    elif raw_status == 'HELD':
        effective_status = 'HELD'
    elif balance and releasable is False:
        effective_status = 'HELD'
    else:
        effective_status = raw_status or 'PENDING'
    review = {
        'status': raw_status,
        'effective_status': effective_status,
        'revision': run.get('revision'),
        'note': run.get('note'),
        'reviewed': run.get('reviewed'),
        'decision': raw_status,
        'approved': raw_status == 'APPROVED',
        'held': raw_status == 'HELD',
    }
    conclusion = dict(review)
    conclusion.update({
        'status': effective_status,
        'decision': effective_status,
        'persisted_status': raw_status,
        'system_hold': bool(balance and releasable is False and raw_status == 'PENDING'),
    })
    summary = {
        'placements': placements,
        'fills': placements,
        'review': review,
        'issuance': conclusion,
        'conclusion': conclusion,
        'releasable': releasable,
        'hold': balance.get('hold', not releasable if releasable is not None else False),
        'hold_reasons': balance.get('hold_reasons', []),
        'occupancy_matched': balance.get('occupancy_matched'),
        'balance': balance,
    }
    return summary
