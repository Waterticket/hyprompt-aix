import calendar
import json
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import current_app
from app.models import Inspection, BatteryReplacement, HistoryAnnotation

SEOUL = ZoneInfo('Asia/Seoul')
RESULTS = {'ok': '이상 없음', 'issue': '이상 있음', 'unknown': '확인 어려움'}
WEEKDAYS = ['월', '화', '수', '목', '금', '토', '일']
ITEMS = {
    'tire': ('타이어 상태', '공기압·마모·손상을 확인하세요. 권장 공기압은 기종별 설명서를 확인하세요.'),
    'brake': ('브레이크 작동', '레버와 제동 상태를 직접 관찰하고 이상 유무를 확인하세요.'),
    'battery_body': ('배터리·본체 외관', '덮개 들뜸·부풀음·균열·누액·그을음이 있는지 확인하세요.'),
    'charger_port': ('충전기·충전 단자', '피복 벗겨짐·눌림·변색·물기·흔들림과 설명서 지정 충전기 라벨의 일치를 확인하세요. 단자 모양만으로 호환성을 판단하지 마세요.'),
}

def today():
    clock = current_app.config.get('CLOCK')
    return (clock() if clock else datetime.now(timezone.utc)).astimezone(SEOUL).date()

def localtime(value):
    if value is None:
        return '아직 없음'
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(SEOUL).strftime('%Y.%m.%d %H:%M')
    return value.strftime('%Y.%m.%d')

def first_weekday(day, weekday):
    return day + timedelta(days=(weekday - day.weekday()) % 7)

def next_due(scooter):
    schedule = scooter.schedule
    due = first_weekday(schedule.effective_from, schedule.weekday)
    # Actual inspection dates drive completion, never the input timestamp.
    for inspection in sorted(scooter.inspections, key=lambda x: (x.inspected_on, x.id or 0)):
        if inspection.inspected_on >= due:
            due = first_weekday(inspection.inspected_on + timedelta(days=1), schedule.weekday)
    return due

def aggregate(values, impact):
    if 'issue' in values or impact == 'yes':
        return 'issue'
    if 'unknown' in values or impact == 'unknown':
        return 'unknown'
    return 'ok'

def load_fixtures(directory):
    fixtures, errors = {}, []
    requirements = {
        'battery_catalog': ('manufacturer', 'scooter_model', 'capacity_mah', 'cell_count', 'cell_format', 'battery_voltage_limit_v', 'charge_time_hours_approx', 'replacement_policy'),
        'recalls': ('manufacturer', 'scooter_model', 'title', 'hazard_summary', 'production_start', 'production_end', 'programme_start', 'source_region', 'coverage_note', 'recommended_action', 'notes'),
        'repair_centers': ('name', 'address', 'notes', 'is_demo', 'is_actual_repair_center'),
    }
    for name, required in requirements.items():
        try:
            data = json.loads((Path(directory) / f'{name}.json').read_text())
            if data['schema_version'] != 1 or data['mode'] != 'local_fixture' or not isinstance(data['records'], list):
                raise ValueError('invalid schema')
            date.fromisoformat(data['checked_on'])
            ids = set()
            for row in data['records']:
                if any(k not in row for k in ('id', 'source_title', 'source_url', 'checked_on', *required)):
                    raise ValueError('missing field')
                if any(not isinstance(row[k], str) or not row[k].strip() for k in ('id', 'source_title', 'source_url', 'checked_on')):
                    raise ValueError('invalid field type')
                if row['id'] in ids or not row['source_url'].startswith('https://'):
                    raise ValueError('invalid identifier/source')
                if name == 'battery_catalog':
                    for key in ('capacity_mah', 'cell_count', 'battery_voltage_limit_v', 'charge_time_hours_approx'):
                        if isinstance(row[key], bool) or not isinstance(row[key], (int, float)) or row[key] <= 0:
                            raise ValueError('invalid specification')
                    if not all(isinstance(row[k], str) and row[k] for k in ('manufacturer', 'scooter_model', 'cell_format', 'replacement_policy')):
                        raise ValueError('invalid specification text')
                if name == 'repair_centers' and not all(isinstance(row[k], str) and row[k] for k in ('name', 'address', 'notes')):
                    raise ValueError('invalid center text')
                ids.add(row['id'])
                date.fromisoformat(row['checked_on'])
                if name == 'recalls':
                    for key in ('production_start', 'production_end', 'programme_start'):
                        date.fromisoformat(row[key])
            if name == 'repair_centers' and (len(data['records']) != 1 or data['records'][0]['id'] != 'erica-main-building-demo' or not data['records'][0]['is_demo'] or data['records'][0]['is_actual_repair_center']):
                raise ValueError('only the ERICA demo location is supported')
            fixtures[name] = data
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            current_app.logger.warning('Fixture validation failed: %s.json', name)
            errors.append(name)
            fixtures[name] = {'schema_version': 1, 'mode': 'local_fixture', 'checked_on': None, 'records': []}
    return fixtures, errors

def battery_for(s):
    return next((x for x in current_app.config['FIXTURES']['battery_catalog']['records'] if x['id'] == s.battery_catalog_id), None)

def recalls_for(s):
    return [x for x in current_app.config['FIXTURES']['recalls']['records'] if x['manufacturer'] == s.manufacturer and x['scooter_model'] == s.model]

def three_months_before(day):
    total = day.year * 12 + day.month - 1 - 3
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, min(day.day, calendar.monthrange(year, month0 + 1)[1]))

def parse_filters(args, selected_id=None):
    period = args.get('period', '3months')
    if period not in ('3months', 'month', 'all', 'custom'):
        raise ValueError('기간을 다시 선택해주세요.')
    kind = args.get('type', '')
    if kind not in ('', 'inspection', 'battery'):
        raise ValueError('기록 유형을 다시 선택해주세요.')
    raw_id = args.get('scooter_id', str(selected_id or ''))
    try:
        scooter_id = int(raw_id) if raw_id else None
        if scooter_id is not None and scooter_id <= 0:
            raise ValueError()
    except ValueError:
        raise ValueError('유효한 기기를 선택해주세요.') from None
    start, end = None, None
    if period == '3months':
        start, end = three_months_before(today()), today()
    elif period == 'month':
        start, end = today().replace(day=1), today()
    elif period == 'custom':
        try:
            start, end = date.fromisoformat(args.get('start', '')), date.fromisoformat(args.get('end', ''))
        except ValueError:
            raise ValueError('시작일과 종료일을 입력해주세요.') from None
        if start > end:
            raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
    return dict(scooter_id=scooter_id, type=kind, period=period, start=start, end=end, include_archived=args.get('include_archived') == '1')

def records_for(filters):
    result = []
    for kind, model, field in [('inspection', Inspection, Inspection.inspected_on), ('battery', BatteryReplacement, BatteryReplacement.replaced_on)]:
        if filters['type'] and kind != filters['type']:
            continue
        query = model.query
        if filters['scooter_id']:
            query = query.filter_by(scooter_id=filters['scooter_id'])
        elif not filters['include_archived']:
            query = query.join(model.scooter).filter_by(archived_at=None)
        if filters['start']:
            query = query.filter(field >= filters['start'])
        if filters['end']:
            query = query.filter(field <= filters['end'])
        for record in query.all():
            result.append({'type': kind, 'record': record, 'date': getattr(record, 'inspected_on' if kind == 'inspection' else 'replaced_on'), 'snapshot': record.scooter_snapshot_json})
    return sorted(result, key=lambda r: (r['date'], r['record'].created_at, r['record'].id, r['type']), reverse=True)

def annotations(kind, record_id):
    return HistoryAnnotation.query.filter_by(target_type=kind, target_id=record_id).order_by(HistoryAnnotation.created_at, HistoryAnnotation.id).all()
