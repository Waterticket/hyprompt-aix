import secrets
from datetime import date
from urllib.parse import urlencode, quote
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for, send_file
from itsdangerous import URLSafeSerializer, BadSignature
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import IntegrityError
from .models import db, Scooter, MaintenanceSchedule, Inspection, InspectionItem, BatteryReplacement, HistoryAnnotation, ScheduleChange, utcnow
from .services.core import today, next_due, aggregate, battery_for, recalls_for, records_for, parse_filters, annotations, RESULTS, WEEKDAYS, ITEMS

bp = Blueprint('web', __name__)

def go(endpoint, **kwargs):
    return redirect(url_for('web.' + endpoint, **kwargs), code=303)

def get_scooter(scooter_id, active=False):
    s = db.get_or_404(Scooter, scooter_id)
    if active and s.archived_at:
        abort(400, description='보관된 기기입니다. 과거 기록은 점검기록에서 확인할 수 있습니다.')
    return s

def selected_scooter():
    explicit = request.args.get('scooter_id')
    if explicit:
        try:
            s = get_scooter(int(explicit), active=request.endpoint not in ('web.records', 'web.export_pdf'))
        except ValueError:
            abort(404)
        if not s.archived_at:
            session['selected_id'] = s.id
        return s
    s = db.session.get(Scooter, session.get('selected_id')) if session.get('selected_id') else None
    if not s or s.archived_at:
        s = Scooter.query.filter_by(archived_at=None).order_by(Scooter.id).first()
        session['selected_id'] = s.id if s else None
    return s

@bp.app_context_processor
def common():
    try:
        s = selected_scooter()
    except HTTPException:
        s = None
    return dict(active_scooters=Scooter.query.filter_by(archived_at=None).order_by(Scooter.id).all(), selected=s,
                today=today(), weekdays=WEEKDAYS, results=RESULTS, items=ITEMS,
                csrf_token=session.get('csrf_token', ''), fixture_errors=current_app.config['FIXTURE_ERRORS'],
                next_due=next_due, battery_for=battery_for, recalls_for=recalls_for)

@bp.get('/select-scooter')
def select_scooter():
    s = selected_scooter()
    dest = request.args.get('destination', 'dashboard')
    if dest not in ('dashboard', 'scooters', 'inspection_new', 'records', 'centers'):
        dest = 'dashboard'
    return go(dest, scooter_id=s.id if s else None)

@bp.get('/')
def dashboard():
    s = selected_scooter()
    records = records_for(dict(scooter_id=s.id if s else None, type='', start=None, end=None, include_archived=False))
    month = records_for(dict(scooter_id=None, type='', start=today().replace(day=1), end=today(), include_archived=True))
    latest = max(s.inspections, key=lambda x: (x.inspected_on, x.created_at, x.id)) if s and s.inspections else None
    return render_template('dashboard.html', title='대시보드', nav='dashboard', records=records[:3], month_count=len(month), latest=latest)

@bp.get('/scooters')
def scooters():
    return render_template('scooters.html', title='내 킥보드', nav='scooters')

@bp.get('/scooters/<int:scooter_id>')
def scooter_detail(scooter_id):
    s = get_scooter(scooter_id)
    if not s.archived_at:
        session['selected_id'] = s.id
    changes = ScheduleChange.query.filter_by(scooter_id=s.id).order_by(ScheduleChange.id.desc()).all()
    return render_template('scooter_detail.html', title='내 킥보드', nav='scooters', scooter=s, changes=changes, errors={}, form={})

def input_text(form, key, limit, errors, required=False):
    value = form.get(key, '').strip()
    if required and not value:
        errors[key] = '필수 항목을 입력해주세요.'
    elif len(value) > limit:
        errors[key] = f'{limit}자 이하로 입력해주세요.'
    return value

def input_date(form, key, errors, required=False, future=False):
    raw = form.get(key, '')
    if not raw:
        if required:
            errors[key] = '날짜를 입력해주세요.'
        return None
    try:
        value = date.fromisoformat(raw)
        if value.isoformat() != raw:
            raise ValueError()
    except ValueError:
        errors[key] = '올바른 날짜를 입력해주세요.'
        return None
    if future and value <= today():
        errors[key] = '오늘 이후 날짜를 입력해주세요.'
    if not future and value > today():
        errors[key] = '미래 날짜는 입력할 수 없습니다.'
    return value

def schedule_data(form, errors, existing=None):
    try:
        weekday = int(form.get('weekday', ''))
        if weekday not in range(7):
            raise ValueError()
    except ValueError:
        errors['weekday'] = '점검 요일을 선택해주세요.'
        weekday = 6
    raw = form.get('battery_next_due_on', '')
    # A previously set recommendation can pass its due date without blocking unrelated edits.
    unchanged = existing and raw == str(existing.battery_next_due_on or '')
    due = existing.battery_next_due_on if unchanged else input_date(form, 'battery_next_due_on', errors, future=True)
    return weekday, due

def scooter_data(form, errors, existing=None):
    data = {key: input_text(form, key, size, errors, key in ('nickname', 'manufacturer', 'model')) for key, size in [('nickname',40),('manufacturer',80),('model',80),('battery_model',100),('serial_number',100),('notes',1000)]}
    for key in ('purchased_on', 'manufactured_on'):
        data[key] = input_date(form, key, errors)
    catalog_id = form.get('battery_catalog_id', '')
    catalog = next((b for b in current_app.config['FIXTURES']['battery_catalog']['records'] if b['id'] == catalog_id), None)
    if catalog_id and not catalog:
        errors['battery_catalog_id'] = '선택한 공식 사양 자료를 사용할 수 없습니다.'
    elif catalog and (data['manufacturer'] != catalog['manufacturer'] or data['model'] != catalog['scooter_model']):
        errors['battery_catalog_id'] = '제조사와 모델이 공식 사양의 Xiaomi / M365와 일치해야 합니다.'
    data['battery_catalog_id'] = catalog_id or None
    return data

def change_schedule(s, weekday, due):
    sch = s.schedule
    if sch.weekday != weekday:
        db.session.add(ScheduleChange(scooter_id=s.id, old_weekday=sch.weekday, new_weekday=weekday, changed_on=today(), previous_due_on=next_due(s)))
        sch.weekday, sch.effective_from = weekday, today()
    sch.battery_next_due_on = due

def scooter_form(s=None, errors=None, form=None, status=200):
    if form is None:
        form = {k: str(getattr(s,k) or '') for k in ('nickname','manufacturer','model','battery_model','serial_number','notes','purchased_on','manufactured_on','battery_catalog_id')} if s else {'weekday':'6'}
        if s:
            form.update(weekday=str(s.schedule.weekday), battery_next_due_on=str(s.schedule.battery_next_due_on or ''))
            initial = BatteryReplacement.query.filter_by(scooter_id=s.id, origin='initial').first()
            form['initial_replaced_on'] = str(initial.replaced_on) if initial else ''
    return render_template('scooter_form.html', title='기기 정보 수정' if s else '킥보드 등록', nav='scooters', scooter=s, form=form, errors=errors or {}), status

@bp.get('/scooters/new')
def scooter_new():
    return scooter_form()

@bp.post('/scooters')
def scooter_create():
    f, errors = request.form, {}
    data = scooter_data(f, errors)
    weekday, due = schedule_data(f, errors)
    replaced = input_date(f, 'initial_replaced_on', errors)
    if errors:
        return scooter_form(errors=errors, form=f, status=422)
    s = Scooter(**data)
    db.session.add(s)
    db.session.flush()
    s.schedule = MaintenanceSchedule(weekday=weekday, effective_from=today(), battery_next_due_on=due)
    if replaced:
        db.session.add(BatteryReplacement(scooter_id=s.id, replaced_on=replaced, battery_model=s.battery_model, scooter_snapshot_json=s.snapshot(), origin='initial'))
    db.session.commit()
    session['selected_id'] = s.id
    flash('킥보드를 등록했습니다.')
    return go('scooter_detail', scooter_id=s.id)

@bp.route('/scooters/<int:scooter_id>/edit', methods=['GET','POST'])
def scooter_edit(scooter_id):
    s = get_scooter(scooter_id, active=True)
    if request.method == 'GET':
        return scooter_form(s)
    f, errors = request.form, {}
    data = scooter_data(f, errors, s)
    weekday, due = schedule_data(f, errors, s.schedule)
    initial_date = input_date(f, 'initial_replaced_on', errors)
    initial = BatteryReplacement.query.filter_by(scooter_id=s.id, origin='initial').first()
    # Initial event corrections share this one event, never create a second ledger.
    if initial and not initial_date:
        errors['initial_replaced_on'] = '원본 교체일은 삭제할 수 없습니다. 날짜 수정 또는 기록 정정 메모를 이용해주세요.'
    if errors:
        return scooter_form(s, errors, f, 422)
    for key, value in data.items():
        setattr(s, key, value)
    change_schedule(s, weekday, due)
    if initial:
        if initial.replaced_on != initial_date:
            db.session.add(HistoryAnnotation(target_type='battery', target_id=initial.id, text=f'등록 당시 교체일 정정: {initial.replaced_on} → {initial_date}'))
            initial.replaced_on = initial_date
    elif initial_date:
        db.session.add(BatteryReplacement(scooter_id=s.id, replaced_on=initial_date, battery_model=s.battery_model, scooter_snapshot_json=s.snapshot(), origin='initial'))
    db.session.commit()
    flash('기기 정보를 저장했습니다.')
    return go('scooter_detail', scooter_id=s.id)

@bp.post('/scooters/<int:scooter_id>/archive')
def scooter_archive(scooter_id):
    s = get_scooter(scooter_id, active=True)
    if request.form.get('confirm_archive') != 'yes':
        abort(400, description='보관 확인을 선택해주세요.')
    s.archived_at = utcnow()
    db.session.commit()
    session.pop('selected_id', None)
    flash('기기를 보관했습니다. 과거 기록은 계속 확인할 수 있습니다.')
    return go('scooters')

def detail_form_error(s, errors, status=422):
    return render_template('scooter_detail.html', title='내 킥보드', nav='scooters', scooter=s, changes=ScheduleChange.query.filter_by(scooter_id=s.id).all(), errors=errors, form=request.form), status

@bp.post('/scooters/<int:scooter_id>/schedule')
def scooter_schedule(scooter_id):
    s, errors = get_scooter(scooter_id, active=True), {}
    weekday, due = schedule_data(request.form, errors, s.schedule)
    if errors:
        return detail_form_error(s, errors)
    change_schedule(s, weekday, due)
    db.session.commit()
    flash('점검 일정을 저장했습니다.')
    return go('scooter_detail', scooter_id=s.id)

@bp.post('/scooters/<int:scooter_id>/battery-replacements')
def battery_create(scooter_id):
    s, errors = get_scooter(scooter_id, active=True), {}
    day = input_date(request.form, 'replaced_on', errors, required=True)
    data = {k:input_text(request.form, k, n, errors) for k,n in [('battery_model',100),('center_name',100),('notes',2000)]}
    if errors:
        return detail_form_error(s, errors)
    record = BatteryReplacement(scooter_id=s.id, replaced_on=day, scooter_snapshot_json=s.snapshot(), **data)
    db.session.add(record)
    db.session.commit()
    flash('배터리 교체 기록을 추가했습니다.')
    return go('scooter_detail', scooter_id=s.id)

def serializer():
    return URLSafeSerializer(current_app.secret_key, salt='inspection-submission')

def inspection_form(s, form=None, errors=None, status=200):
    f = dict(form) if form is not None else {'inspected_on': today().isoformat()}
    f.setdefault('submission_token', serializer().dumps({'scooter_id':s.id, 'nonce':secrets.token_hex(16), 'session':session['csrf_token']}))
    return render_template('inspection.html', title='정기점검', nav='inspection', scooter=s, form=f, errors=errors or {}), status

@bp.get('/inspections/new')
def inspection_new():
    s = selected_scooter()
    if not s:
        return render_template('empty.html', title='정기점검', nav='inspection')
    return inspection_form(s)

@bp.post('/inspections')
def inspection_create():
    f, errors = request.form, {}
    try:
        s = get_scooter(int(f.get('scooter_id', '')), active=True)
    except ValueError:
        abort(400, description='점검할 기기를 선택해주세요.')
    token = f.get('submission_token', '')
    try:
        payload = serializer().loads(token)
        if payload['scooter_id'] != s.id or payload['session'] != session['csrf_token']:
            raise BadSignature('wrong target')
    except (BadSignature, KeyError):
        abort(400, description='점검 입력 정보가 만료되었습니다. 새 점검을 시작해주세요.')
    existing = Inspection.query.filter_by(submission_token=token).first()
    if existing:
        return go('record_detail', kind='inspection', record_id=existing.id)
    day = input_date(f, 'inspected_on', errors, required=True)
    for key in ITEMS:
        if f.get(key) not in RESULTS:
            errors[key] = '확인 결과를 선택해주세요.'
    impact = f.get('impact_water_result')
    if impact not in ('no','yes','unknown'):
        errors['impact_water_result'] = '충격·침수 이력을 선택해주세요.'
    result = aggregate([f.get(k) for k in ITEMS], impact)
    notes = input_text(f, 'notes', 2000, errors, required=result == 'issue')
    if errors:
        return inspection_form(s, f, errors, 422)
    record = Inspection(scooter_id=s.id, inspected_on=day, overall_result=result, impact_water_result=impact, notes=notes, scooter_snapshot_json=s.snapshot(), submission_token=token)
    record.items = [InspectionItem(item_key=k, result=f[k], criteria_snapshot=v[1]) for k,v in ITEMS.items()]
    db.session.add(record)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = Inspection.query.filter_by(submission_token=token).first()
        if not existing:
            raise
        return go('record_detail', kind='inspection', record_id=existing.id)
    session['selected_id'] = s.id
    flash('점검 결과를 저장했습니다.')
    return go('record_detail', kind='inspection', record_id=record.id)

def filtered_records():
    s = selected_scooter()
    try:
        f = parse_filters(request.args, s.id if s else None)
    except ValueError as e:
        abort(400, description=str(e))
    if f['scooter_id']:
        get_scooter(f['scooter_id'])
    return f, records_for(f)

@bp.get('/records')
def records():
    filters, rows = filtered_records()
    try:
        page = int(request.args.get('page','1'))
        if page < 1:
            raise ValueError()
    except ValueError:
        abort(400, description='올바른 페이지를 선택해주세요.')
    export_args = {k:v for k,v in filters.items() if v is not None}
    export_args['include_archived'] = '1' if filters['include_archived'] else ''
    export_args['scooter_id'] = filters['scooter_id'] or ''
    return render_template('records.html', title='점검기록', nav='records', filters=filters, rows=rows[(page-1)*20:page*20], count=len(rows), page=page, export_query=urlencode(export_args), all_scooters=Scooter.query.order_by(Scooter.id).all())

def get_record(kind, record_id):
    if kind not in ('inspection','battery'):
        abort(404)
    return db.get_or_404(Inspection if kind == 'inspection' else BatteryReplacement, record_id)

@bp.get('/records/<kind>/<int:record_id>')
def record_detail(kind, record_id):
    r = get_record(kind, record_id)
    return render_template('record_detail.html', title='기록 상세', nav='records', kind=kind, record=r, snapshot=r.scooter_snapshot_json, annotations=annotations(kind, r.id), form={}, errors={})

@bp.post('/records/<kind>/<int:record_id>/annotations')
def annotation_create(kind, record_id):
    r, errors = get_record(kind, record_id), {}
    text = input_text(request.form, 'text', 2000, errors, required=True)
    if errors:
        return render_template('record_detail.html', title='기록 상세', nav='records', kind=kind, record=r, snapshot=r.scooter_snapshot_json, annotations=annotations(kind,r.id), form=request.form, errors=errors), 422
    db.session.add(HistoryAnnotation(target_type=kind, target_id=r.id, text=text))
    db.session.commit()
    flash('정정 메모를 추가했습니다.')
    return go('record_detail', kind=kind, record_id=r.id)

@bp.get('/records/export.pdf')
def export_pdf():
    filters, rows = filtered_records()
    if not rows:
        flash('선택한 조건의 기록이 없습니다. 필터를 변경해주세요.')
        return go('records', **request.args.to_dict())
    from .services.pdf import export_records
    try:
        pdf = export_records(rows, filters, include_notes=request.args.get('include_notes') == '1')
    except FileNotFoundError:
        return render_template('error.html', title='PDF 한글 글꼴을 설정해주세요', message='PDF_FONT_PATH에 한글을 지원하는 TrueType 글꼴 경로를 설정한 뒤 다시 시도해주세요.', retry_url=request.url), 503
    except Exception as error:
        current_app.logger.error('PDF generation failed: %s', type(error).__name__)
        return render_template('error.html', title='PDF를 생성하지 못했습니다', message='조회 조건은 유지됩니다. 잠시 후 다시 시도해주세요.', retry_url=request.url), 503
    return send_file(pdf, mimetype='application/pdf', as_attachment=True, download_name=f'kicklog_records_{today():%Y%m%d}.pdf')

@bp.get('/repair-centers')
def centers():
    rows = current_app.config['FIXTURES']['repair_centers']['records']
    c = rows[0] if rows else None
    link = 'https://map.kakao.com/link/search/' + quote(c['name'] + ' ' + c['address'], safe='') if c else ''
    return render_template('centers.html', title='수리센터', nav='centers', center=c, search_link=link, kakao_key=current_app.config['KAKAO_MAP_JAVASCRIPT_KEY'])

@bp.get('/catalog/<name>')
def catalog(name):
    key = {'batteries':'battery_catalog','recalls':'recalls'}.get(name)
    if not key:
        abort(404)
    return current_app.config['FIXTURES'][key]

def recover_form(message):
    f = request.form
    endpoint = request.endpoint
    errors = {'_save': message}
    if endpoint in ('web.scooter_create','web.scooter_edit'):
        s = db.session.get(Scooter, request.view_args.get('scooter_id')) if endpoint.endswith('edit') else None
        return scooter_form(s, errors, f, 503)
    if endpoint == 'web.inspection_create':
        s = db.session.get(Scooter, int(f.get('scooter_id','0')))
        return inspection_form(s, f, errors, 503)
    if endpoint in ('web.battery_create','web.scooter_schedule','web.scooter_archive'):
        return detail_form_error(get_scooter(request.view_args['scooter_id']), errors, 503)
    if endpoint == 'web.annotation_create':
        kind, rid = request.view_args['kind'], request.view_args['record_id']
        r = get_record(kind, rid)
        return render_template('record_detail.html', title='기록 상세', nav='records', kind=kind, record=r, snapshot=r.scooter_snapshot_json, annotations=annotations(kind,rid), form=f, errors=errors), 503
    return render_template('error.html', title='저장하지 못했습니다', message=message), 503
