from flask import Blueprint, Response, current_app, jsonify, request

from app.business import DomainIssue
from app.evidence.render import artifact
from app.inputs import read_input
from app.modules.pipeline import run
from app.review.service import execute, review, supplement

api = Blueprint('api',__name__,url_prefix='/api')
STAGES = {'intake':1,'prepare':2,'calculate':3,'policy':4,'balance':5}


@api.post('/stages/<stage>')
def preview(stage):
    if stage not in STAGES:
        raise DomainIssue('STAGE_UNKNOWN')
    return jsonify(run(read_input(),STAGES[stage]))


@api.route('/runs',methods=['GET','POST'])
def runs():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(items=store.list())
    return jsonify(execute(store,read_input()))


def _json_or_form():
    payload = request.get_json(silent=True)
    if isinstance(payload, list):
        return {'records': payload}
    if isinstance(payload, dict):
        return dict(payload)
    if request.form:
        return request.form.to_dict()
    return {}


def _request_blob():
    uploaded = (
        request.files.get('file')
        or request.files.get('csv')
        or request.files.get('supplement')
        or request.files.get('cancellation')
    )
    if uploaded is not None:
        return uploaded.read()
    if not request.form and not request.files and request.data:
        return request.data
    return None


def _supplement_response(run_id):
    store = current_app.extensions['runs']
    payload = _json_or_form()
    blob = _request_blob()
    revision = payload.pop('revision', None)
    if revision is None:
        revision = request.args.get('revision')
    if isinstance(revision, str) and revision.strip().lstrip('-').isdigit():
        revision = int(revision)
    raw_replace = payload.get('replace') if isinstance(payload, dict) else None
    if raw_replace is None:
        raw_replace = request.args.get('replace')
    if raw_replace is None:
        replace = None
    else:
        replace = str(raw_replace).lower() in {'1', 'true', 'yes', 'replace', 'full'}
    return jsonify(supplement(store, run_id, payload, blob, revision=revision, replace=replace))


def looks_like_supplement_payload(payload):
    if not isinstance(payload, dict):
        return False
    action = str(payload.get('action') or payload.get('op') or '').lower()
    return (
        action in {'supplement', 'backfill', 'append', 'late_cancellation', 'cancellation'}
        or payload.get('kind') == 'cancel'
        or any(key in payload for key in ('cancellation', 'late_cancellation', 'records', 'record'))
        and 'status' not in payload
    )


@api.route('/runs/<run_id>', methods=['GET', 'POST', 'PATCH', 'PUT'])
def batch(run_id):
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(store.load(run_id))
    payload = request.get_json(silent=True)
    if payload is None and request.form:
        payload = request.form.to_dict()
    if not isinstance(payload, dict):
        payload = {}
    if looks_like_supplement_payload(payload):
        blob = _request_blob()
        return jsonify(supplement(store, run_id, payload, blob))
    return jsonify(review(store,run_id,payload))


@api.route('/runs/<run_id>/review', methods=['POST', 'PATCH', 'PUT'])
def review_alias(run_id):
    store = current_app.extensions['runs']
    payload = _json_or_form()
    if looks_like_supplement_payload(payload):
        return _supplement_response(run_id)
    return jsonify(review(store, run_id, payload))


def supplement_batch(run_id):
    return _supplement_response(run_id)


for supplement_path in (
    '/runs/<run_id>/supplement',
    '/runs/<run_id>/supplements',
    '/runs/<run_id>/supplement-record',
    '/runs/<run_id>/records/late-cancellation',
    '/runs/<run_id>/backfill',
    '/runs/<run_id>/append',
    '/runs/<run_id>/add',
    '/runs/<run_id>/backfill-cancellation',
    '/runs/<run_id>/late-cancel',
    '/runs/<run_id>/late-record',
    '/runs/<run_id>/late-records',
    '/runs/<run_id>/late-cancellation',
    '/runs/<run_id>/late-cancellations',
    '/runs/<run_id>/late-cancellation-record',
    '/runs/<run_id>/late-cancellation-records',
    '/runs/<run_id>/supplement-cancellation',
    '/runs/<run_id>/cancel',
    '/runs/<run_id>/supplement_record',
    '/runs/<run_id>/late_cancellation',
    '/runs/<run_id>/late_cancellations',
    '/runs/<run_id>/late_cancel',
    '/runs/<run_id>/late_record',
    '/runs/<run_id>/late_records',
    '/runs/<run_id>/supplement_cancellation',
    '/runs/<run_id>/cancellation',
    '/runs/<run_id>/cancellations',
    '/runs/<run_id>/cancellations/late',
    '/runs/<run_id>/late-cancellations/new',
    '/runs/<run_id>/supplement-cancellations',
    '/runs/<run_id>/backfill-cancellations',
    '/runs/<run_id>/waitlist/supplement',
    '/runs/<run_id>/waitlist/backfill',
    '/runs/<run_id>/records',
    '/runs/<run_id>/events',
):
    api.add_url_rule(
        supplement_path,
        endpoint='supplement_batch' + supplement_path.replace('<run_id>', 'rid').replace('/', '_').replace('-', '_'),
        view_func=supplement_batch,
        methods=['POST', 'PUT', 'PATCH'],
    )


@api.route('/supplement', methods=['POST', 'PUT', 'PATCH'])
@api.route('/supplements', methods=['POST', 'PUT', 'PATCH'])
@api.route('/backfill', methods=['POST', 'PUT', 'PATCH'])
@api.route('/cancellations', methods=['POST', 'PUT', 'PATCH'])
@api.route('/late-cancellations', methods=['POST', 'PUT', 'PATCH'])
def supplement_collection():
    payload = _json_or_form()
    run_id = payload.get('run_id') or payload.get('runId') or payload.get('batch_id')
    if not run_id:
        raise DomainIssue('RUN_NOT_FOUND')
    return _supplement_response(run_id)


@api.get('/runs/<run_id>/supplements')
@api.get('/runs/<run_id>/supplement')
@api.get('/runs/<run_id>/cancellations')
@api.get('/runs/<run_id>/late-cancellations')
def list_supplements(run_id):
    run = current_app.extensions['runs'].load(run_id)
    return jsonify(
        items=run['supplements'],
        supplements=run['supplements'],
        cancellations=run['late_cancellations'],
        late_cancellations=run['late_cancellations'],
        placements=run['placements'],
    )


@api.get('/runs/<run_id>/workbench')
def run_workbench(run_id):
    run = current_app.extensions['runs'].load(run_id)
    return jsonify(run['workbench'])


@api.get('/workbench/runs/<run_id>')
def workbench_alias(run_id):
    run = current_app.extensions['runs'].load(run_id)
    return jsonify(run['workbench'])


@api.get('/workbench')
def workbench_query():
    run_id = request.args.get('run_id') or request.args.get('id')
    if not run_id:
        raise DomainIssue('RUN_NOT_FOUND')
    run = current_app.extensions['runs'].load(run_id)
    return jsonify(run['workbench'])


@api.get('/runs/<run_id>/evidence')
def export(run_id):
    report = current_app.extensions['runs'].load(run_id)
    return Response(artifact(report),mimetype='application/json', headers={
        'Content-Disposition':f'attachment; filename="review-{report["id"][:12]}.json"',
        'Cache-Control':'no-store',
    })
