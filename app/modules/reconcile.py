from collections import Counter


def balance(intake, cases):
    accounted = intake['accepted'] + intake['rejected'] + intake['blank'] + intake['repeated']
    status = Counter(case['status'] for case in cases)
    ready = status.pop('READY', 0)
    observed = sum(len(case['records']) for case in cases)

    measured_cases = [case for case in cases if isinstance(case.get('measure'), dict)]
    occupancy_actual = sum(case['measure'].get('value') or 0 for case in measured_cases)
    occupancy_registered = sum(case['measure'].get('expected') or 0 for case in measured_cases)
    occupancy_matched = all(
        case['measure'].get('value') == case['measure'].get('expected')
        for case in measured_cases
    )
    all_ready = bool(cases) and ready == len(cases)
    base_balanced = accounted == intake['input'] and observed == intake['accepted']
    hold_reasons = []
    if intake['rejected']:
        hold_reasons.append('ISOLATED_ROWS')
    if not base_balanced:
        hold_reasons.append('RECONCILIATION_MISMATCH')
    if not occupancy_matched:
        hold_reasons.append('OCCUPANCY_MISMATCH')
    if not all_ready:
        hold_reasons.append('BUSINESS_ISSUES')
    releasable = all_ready and occupancy_matched and not intake['rejected'] and base_balanced
    return dict(input=intake['input'], accepted=intake['accepted'], isolated=intake['rejected'],
                ignored=intake['blank'] + intake['repeated'], accounted=accounted,
                balanced=base_balanced,
                occupancy=dict(actual=occupancy_actual, registered=occupancy_registered,
                               expected=occupancy_registered, matched=occupancy_matched),
                occupied=occupancy_actual, occupancy_count=occupancy_actual,
                registered=occupancy_registered, registered_count=occupancy_registered,
                expected_occupancy=occupancy_registered, audit_count=occupancy_registered,
                occupancy_matched=occupancy_matched, occupancy_balanced=occupancy_matched,
                hold=not releasable, held=not releasable,
                status='READY' if releasable else 'HELD',
                decision='RELEASE' if releasable else 'HOLD',
                hold_reasons=hold_reasons, reasons=hold_reasons,
                release_decision='RELEASE' if releasable else 'HOLD',
                ready=ready, pending=len(cases)-ready, issues=dict(sorted(status.items())),
                releasable=releasable)
