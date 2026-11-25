from app.business import measured
from itertools import groupby
from heapq import heappush, heapify
import sys

sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))


def _maximum_matching(ranked, slots):
    """Return (slot -> candidate index, candidates matched in waitlist order)."""
    slot_values = sorted(slots)
    edges = {
        index: [slot for slot in slot_values if entry[4] in (0, slot)]
        for index, entry in enumerate(ranked)
    }
    owner = {}

    def augment(index, seen):
        for slot in edges[index]:
            if slot in seen:
                continue
            seen.add(slot)
            previous = owner.get(slot)
            if previous is None or augment(previous, seen):
                owner[slot] = index
                return True
        return False

    matched_order = []
    for index in range(len(ranked)):
        if augment(index, set()):
            matched_order.append(index)
    return owner, matched_order, edges


def _can_match_all(indices, available_slots, edges):
    available = set(available_slots)
    if len(indices) > len(available):
        return False
    if indices and all(available.issubset(set(edges[index])) for index in indices):
        return True
    owner = {}

    def augment(index, seen):
        for slot in edges[index]:
            if slot not in available or slot in seen:
                continue
            seen.add(slot)
            previous = owner.get(slot)
            if previous is None or augment(previous, seen):
                owner[slot] = index
                return True
        return False

    return all(augment(index, set()) for index in indices)


def _assign_slots(ranked, slots):
    _, matched_order, edges = _maximum_matching(ranked, slots)
    available = sorted(slots)
    assignments = []

    # 已确定最大补位数后，按候补原顺序优先保留入选资格，
    # 并给排在前面的人其仍可使其余入选者全部匹配的最小号源。
    for position, index in enumerate(matched_order):
        chosen = None
        for slot in edges[index]:
            if slot not in available:
                continue
            remaining_slots = [value for value in available if value != slot]
            if _can_match_all(matched_order[position + 1:], remaining_slots, edges):
                chosen = slot
                break
        if chosen is None:  # pragma: no cover - guarded by maximum matching
            continue
        available.remove(chosen)
        assignments.append((ranked[index], chosen))
    return assignments


def compute(data):
    occupied0, events, expected = data
    occupied = dict(occupied0)
    waiting = []
    promoted = {}
    placements = []
    expired = []
    invalid = []

    ordered_events = sorted(
        events,
        key=lambda row: (
            row['minute'],
            row['kind'] == 'cancel',
            row['slot'],
            row['attendee'],
            row['record'],
        ),
    )

    for minute, minute_events in groupby(ordered_events, key=lambda row: row['minute']):
        minute_events = list(minute_events)
        for row in minute_events:
            if row['kind'] == 'wait':
                heappush(
                    waiting,
                    (
                        -row['priority'],
                        row['minute'],
                        row['attendee'],
                        row['expires'],
                        row.get('slot', 0),
                        row['record'],
                    ),
                )

        # 过期时间等于当前观察分钟即离队；必须在同分钟匹配前全部移除。
        if waiting:
            active = []
            for entry in waiting:
                if entry[3] <= minute:
                    expired.append(entry[2])
                else:
                    active.append(entry)
            waiting = active
            heapify(waiting)

        freed = []
        cancel_records = {}
        for row in minute_events:
            if row['kind'] != 'cancel':
                continue
            slot, person = row['slot'], row['attendee']
            if occupied.get(slot) != person:
                invalid.append(row['record'])
                continue
            del occupied[slot]
            # 同一号源同分钟不可能有第二条有效取消；去重可增强手工构造数据的健壮性。
            if slot not in freed:
                freed.append(slot)
            cancel_records[slot] = row['record']

        if not freed:
            continue

        ranked = sorted(waiting)
        assignments = _assign_slots(ranked, freed)
        assigned_records = set()
        for entry, slot in assignments:
            person, wait_record = entry[2], entry[5]
            occupied[slot] = person
            promoted[slot] = person
            assigned_records.add(wait_record)
            placement = {
                'case_minute': minute,
                'minute': minute,
                'slot': slot,
                'attendee': person,
                'person': person,
                'record': wait_record,
                'wait_record': wait_record,
                'cancellation_record': cancel_records.get(slot),
            }
            placements.append(placement)

        if assigned_records:
            waiting = [entry for entry in waiting if entry[5] not in assigned_records]
            heapify(waiting)

    evidence = {
        'occupied': {str(key): value for key, value in occupied.items()},
        'promoted': {str(key): value for key, value in promoted.items()},
        'expired': sorted(expired),
        'invalid_cancels': invalid,
    }
    result = measured(
        len(occupied),
        expected,
        '号',
        evidence,
        bool(invalid),
    )
    # 不改变既有 evidence 精确契约；补位流水作为附加读取字段提供。
    result['placements'] = placements
    result['assignment_log'] = placements
    result['fills'] = placements
    return result
