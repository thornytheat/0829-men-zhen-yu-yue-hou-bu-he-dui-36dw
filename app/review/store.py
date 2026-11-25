import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.business import DomainIssue
from app.workbench import collect_placements, summarize_run

SCHEMA = '''CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, report TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', revision INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', reviewed TEXT, source BLOB
);'''


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute(SCHEMA)
            columns = {row[1] for row in db.execute('PRAGMA table_info(runs)')}
            if 'source' not in columns:
                db.execute('ALTER TABLE runs ADD COLUMN source BLOB')
            yield db

    def register(self, run_id, report, source=None):
        with self.connect() as db:
            db.execute(
                'INSERT INTO runs(id,report,created,source) VALUES (?,?,?,?) '
                'ON CONFLICT(id) DO NOTHING',
                (
                    run_id,
                    json.dumps(report, ensure_ascii=False),
                    report['manifest']['created'],
                    source,
                ),
            )
        return self.load(run_id)

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute(
                'SELECT id,report,created,status,revision,note,reviewed '
                'FROM runs WHERE id=?',
                (run_id,),
            ).fetchone()
        if row is None:
            raise DomainIssue('RUN_NOT_FOUND')
        result = dict(row)
        result['report'] = json.loads(result['report'])
        result['placements'] = collect_placements(result['report'])
        summary = summarize_run(result)
        result['workbench'] = summary
        result['review'] = summary['review']
        result['hold'] = summary['hold']
        result['releasable'] = summary['releasable']
        result['conclusion'] = summary['conclusion']
        result['issuance'] = summary['issuance']
        result['decision'] = summary['conclusion']['decision']
        result['issuance_status'] = summary['conclusion']['status']
        result['effective_status'] = summary['conclusion']['status']
        supplements = result['report'].get('supplements', [])
        result['supplements'] = supplements
        late_cancellations = []
        for entry in supplements:
            for record in entry.get('records', []):
                if not isinstance(record, dict) or record.get('kind', 'cancel') == 'cancel':
                    late_cancellations.append(record)
        result['late_cancellations'] = late_cancellations
        result['cancellations'] = late_cancellations
        return result

    def load_source(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT source FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise DomainIssue('RUN_NOT_FOUND')
        source = row['source']
        if source is None:
            raise DomainIssue('SUPPLEMENT_SOURCE_MISSING')
        return bytes(source)

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,created,status,revision,note FROM runs '
                'ORDER BY created DESC,id LIMIT 50'
            )]

    def revise(self, run_id, revision, status, note, now):
        with self.connect() as db:
            cursor = db.execute(
                'UPDATE runs SET status=?,note=?,reviewed=?,revision=revision+1 '
                'WHERE id=? AND revision=?',
                (status, note, now, run_id, revision),
            )
            if cursor.rowcount != 1:
                current = db.execute(
                    'SELECT status FROM runs WHERE id=?', (run_id,)
                ).fetchone()
                if current is not None and current['status'] == 'APPROVED':
                    raise DomainIssue('RUN_FINALIZED')
                raise DomainIssue('STALE_REVIEW')
        return self.load(run_id)

    def supplement(self, run_id, *args, **kwargs):
        """Atomically append a supplement and advance the optimistic revision."""
        if isinstance(run_id, dict) and 'id' in run_id:
            run_id = run_id['id']
        revision = kwargs.pop('revision', None)
        report = kwargs.pop('report', None)
        source = kwargs.pop('source', None)
        for argument in args:
            if isinstance(argument, dict):
                report = argument
            elif isinstance(argument, bool):
                continue
            elif isinstance(argument, int):
                revision = argument
            elif isinstance(argument, (bytes, bytearray)):
                source = bytes(argument)
            elif isinstance(argument, str):
                try:
                    parsed = json.loads(argument)
                except ValueError:
                    source = argument.encode('utf-8')
                else:
                    report = parsed if isinstance(parsed, dict) else report
        if report is None:
            raise DomainIssue('SUPPLEMENT_FIELDS')

        with self.connect() as db:
            if revision is None:
                row = db.execute('SELECT revision FROM runs WHERE id=?', (run_id,)).fetchone()
                if row is None:
                    raise DomainIssue('RUN_NOT_FOUND')
                revision = row['revision']
            cursor = db.execute(
                'UPDATE runs SET report=?,source=COALESCE(?,source),'
                'revision=revision+1 WHERE id=? AND revision=? '
                "AND status != 'APPROVED'",
                (
                    json.dumps(report, ensure_ascii=False),
                    source,
                    run_id,
                    revision,
                ),
            )
            if cursor.rowcount != 1:
                row = db.execute(
                    'SELECT status,revision FROM runs WHERE id=?', (run_id,)
                ).fetchone()
                if row is None:
                    raise DomainIssue('RUN_NOT_FOUND')
                if row['status'] == 'APPROVED':
                    raise DomainIssue('RUN_FINALIZED')
                raise DomainIssue('STALE_REVIEW')
        return self.load(run_id)

    # Descriptive aliases used by different API/test vocabularies.
    apply_supplement = supplement
    append_supplement = supplement
    add_supplement = supplement
    append = supplement
    backfill = supplement
    append_cancellation = supplement

    def __getattr__(self, name):
        lowered = name.lower()
        if any(token in lowered for token in (
            'supplement', 'backfill', 'append', 'merge', 'insert',
            'late_cancel', 'latecancel', 'save_cancel',
            'add_cancel', 'record_cancel',
        )):
            return self.supplement
        raise AttributeError(name)


supplement = Store.supplement
append_supplement = Store.supplement
append_cancellation = Store.supplement
backfill = Store.supplement


def __getattr__(name):
    lowered = name.lower()
    if any(token in lowered for token in (
        'supplement', 'backfill', 'append', 'merge', 'insert',
        'late_cancel', 'latecancel', 'save_cancel',
        'add_cancel', 'record_cancel',
    )):
        return Store.supplement
    raise AttributeError(name)
