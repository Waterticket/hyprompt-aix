from datetime import date, datetime, timezone
from io import BytesIO
import json
import pytest
from pypdf import PdfReader
from sqlalchemy.exc import OperationalError
from app import create_app
from app.models import db, Scooter, Inspection, BatteryReplacement, HistoryAnnotation, ScheduleChange
from app.services.core import next_due, aggregate, today, three_months_before, load_fixtures
from conftest import register, csrf, inspection_data


def test_empty_pages_and_csrf(client):
    for path in ['/','/scooters','/scooters/new','/inspections/new','/records','/repair-centers']:
        assert client.get(path).status_code==200
    assert '첫 킥보드를 등록해보세요' in client.get('/').text
    assert client.post('/scooters',data={'nickname':'x'}).status_code==400
    assert client.post('/scooters',data={'csrf_token':'한글'}).status_code==400
    for path in ['/?scooter_id=bad','/?scooter_id=999','/records?period=custom&start=x','/records?type=evil','/records?page=-1','/records/evil/1']:
        assert client.get(path).status_code in (400,404)


def test_registration_validation_persistence_and_catalog(app, client):
    r=register(client,nickname='',purchased_on='2026-11-26')
    assert r.status_code==422 and '미래 날짜' in r.text
    with app.app_context(): assert Scooter.query.count()==0
    assert register(client,manufacturer='Other').status_code==422
    assert register(client,purchased_on='2026-11-01',initial_replaced_on='2026-11-02').status_code==303
    with app.app_context():
        s=db.session.get(Scooter,1)
        assert s.nickname=='나의 출퇴근 킥보드' and s.schedule.battery_next_due_on is None
        assert len(s.replacements)==1
        uri=app.config['SQLALCHEMY_DATABASE_URI']
    restarted=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':uri})
    assert '나의 출퇴근 킥보드' in restarted.test_client().get('/scooters/1').text
    result=app.test_cli_runner().invoke(args=['init-db'])
    assert result.exit_code==0
    with app.app_context(): assert Scooter.query.count()==1
    assert '7,800' in client.get('/scooters/1').text
    page=client.get('/scooters/1').text
    assert 'FR' in page and '2018-10-27' in page and '개별 대상 미확인' in page


def test_multidevice_and_snapshot(app,client):
    register(client)
    register(client,nickname='두번째 기기',model='Custom',manufacturer='Other',battery_catalog_id='')
    data=inspection_data(client,1)
    assert client.post('/inspections',data=data).status_code==303
    assert '나의 출퇴근 킥보드' in client.get('/records').text
    client.get('/?scooter_id=2')
    assert '선택한 조건의 기록이 없습니다.' in client.get('/records').text
    assert '등록된 공지가 없습니다.' in client.get('/scooters/2').text
    assert client.post('/scooters/1/edit',data=dict(csrf_token=csrf(client),nickname='이름 변경',manufacturer='Other',model='Changed',weekday='6')).status_code==303
    assert '나의 출퇴근 킥보드' in client.get('/records/inspection/1').text
    with app.app_context():
        assert db.session.get(Inspection,1).scooter_snapshot_json['model']=='M365'


@pytest.mark.parametrize(('values','impact','expected'),[
    (['ok']*4,'no','ok'),(['unknown','ok','ok','ok'],'no','unknown'),
    (['unknown','issue','ok','ok'],'no','issue'),(['ok']*4,'yes','issue'),(['ok']*4,'unknown','unknown')])
def test_aggregation(values,impact,expected):
    assert aggregate(values,impact)==expected


def test_inspection_required_results_and_idempotency(app,client):
    register(client)
    data=inspection_data(client,tire='',impact_water_result='')
    assert client.post('/inspections',data=data).status_code==422
    data.update(tire='issue',impact_water_result='no')
    assert client.post('/inspections',data=data).status_code==422
    data['notes']='<script>test</script>'
    r=client.post('/inspections',data=data)
    assert r.status_code==303
    assert client.post('/inspections',data=data).location==r.location
    with app.app_context():
        r=db.session.get(Inspection,1)
        assert Inspection.query.count()==1 and len(r.items)==4 and r.overall_result=='issue'
    detail=client.get('/records/inspection/1').text
    assert '&lt;script&gt;' in detail and '<script>test</script>' not in detail
    data=inspection_data(client,tire='unknown')
    client.post('/inspections',data=data)
    with app.app_context(): assert db.session.get(Inspection,2).overall_result=='unknown'
    assert client.post('/inspections',data=inspection_data(client,inspected_on='2026-11-26')).status_code==422
    foreign=client.get('/inspections/new?scooter_id=1').text
    data['submission_token']='tampered'
    assert client.post('/inspections',data=data).status_code==400


def test_schedule_examples_and_seoul_boundary(app,client):
    register(client)
    with app.app_context(): assert next_due(db.session.get(Scooter,1))==date(2026,11,29)
    client.post('/inspections',data=inspection_data(client))
    with app.app_context(): assert next_due(db.session.get(Scooter,1))==date(2026,11,29)
    app.config['CLOCK']=lambda:datetime(2026,11,29,3,tzinfo=timezone.utc)
    client.post('/inspections',data=inspection_data(client,inspected_on='2026-11-29'))
    with app.app_context(): assert next_due(db.session.get(Scooter,1))==date(2026,12,6)
    client.post('/inspections',data=inspection_data(client,inspected_on='2026-11-22'))
    with app.app_context(): assert next_due(db.session.get(Scooter,1))==date(2026,12,6)
    register(client,nickname='지연 점검')
    # A device registered November 29 has its first due date on the same day.
    app.config['CLOCK']=lambda:datetime(2026,12,2,3,tzinfo=timezone.utc)
    assert '3일 지났어요' in client.get('/?scooter_id=2').text
    client.post('/inspections',data=inspection_data(client,2,inspected_on='2026-12-02',tire='issue',notes='타이어 손상'))
    with app.app_context(): assert next_due(db.session.get(Scooter,2))==date(2026,12,6)
    client.post('/scooters/2/schedule',data=dict(csrf_token=csrf(client),weekday='2'))
    with app.app_context():
        change=ScheduleChange.query.one()
        assert change.previous_due_on==date(2026,12,6) and change.changed_on==date(2026,12,2)
    app.config['CLOCK']=lambda:datetime(2026,11,25,14,59,tzinfo=timezone.utc)
    with app.app_context(): assert today()==date(2026,11,25)
    app.config['CLOCK']=lambda:datetime(2026,11,25,15,0,tzinfo=timezone.utc)
    with app.app_context(): assert today()==date(2026,11,26)
    assert three_months_before(date(2026,5,31))==date(2026,2,28)


def test_battery_events_archive_filters_and_annotations(app,client):
    register(client,initial_replaced_on='2026-11-01')
    token=csrf(client)
    assert client.post('/scooters/1/battery-replacements',data=dict(csrf_token=token,replaced_on='2026-11-26')).status_code==422
    assert client.post('/scooters/1/battery-replacements',data=dict(csrf_token=token,replaced_on='2026-11-25',battery_model='교체용 팩')).status_code==303
    # Earlier event entered late must not replace the latest actual date.
    client.post('/scooters/1/battery-replacements',data=dict(csrf_token=token,replaced_on='2026-11-10'))
    with app.app_context(): assert db.session.get(Scooter,1).replacements[0].replaced_on==date(2026,11,25)
    edit=dict(csrf_token=token,nickname='이름 변경',manufacturer='Xiaomi',model='M365',battery_catalog_id='xiaomi-m365-standard',weekday='6',initial_replaced_on='2026-11-02')
    assert client.post('/scooters/1/edit',data=edit).status_code==303
    with app.app_context():
        assert BatteryReplacement.query.count()==3 and HistoryAnnotation.query.count()==1
    assert client.post('/records/battery/1/annotations',data=dict(csrf_token=token,text='수리센터명 정정')).status_code==303
    assert '수리센터명 정정' in client.get('/records/battery/1').text
    client.post('/inspections',data=inspection_data(client))
    url='/records?period=custom&start=2026-11-25&end=2026-11-25&type=battery'
    page=client.get(url).text
    assert '교체용 팩' not in page # detail-only field
    assert '2026.11.25' in page and '2026.11.10' not in page
    assert client.get('/records?period=custom&start=2026-11-25&end=2026-11-01').status_code==400
    assert client.post('/scooters/1/archive',data=dict(csrf_token=token)).status_code==400
    assert client.post('/scooters/1/archive',data=dict(csrf_token=token,confirm_archive='yes')).status_code==303
    assert client.get('/records/inspection/1').status_code==200
    assert '선택한 조건의 기록이 없습니다.' in client.get('/records?period=all&scooter_id=').text
    assert '2026.11.25' in client.get('/records?period=all&scooter_id=&include_archived=1').text
    assert client.get('/records/export.pdf?period=all&scooter_id=1&include_archived=1').status_code==200


def test_pdf_pagination_korean_and_filter(app,client,tmp_path):
    register(client)
    data=inspection_data(client,notes='긴 메모 테스트. ' * 150)
    client.post('/inspections',data=data)
    for _ in range(21):
        client.post('/inspections',data=inspection_data(client))
    client.post('/records/inspection/1/annotations',data=dict(csrf_token=csrf(client),text='추가 정정 내용'))
    assert '다음 →' in client.get('/records?period=all').text
    assert '이전' in client.get('/records?period=all&page=2').text
    response=client.get('/records/export.pdf?period=all&include_notes=1')
    assert response.status_code==200 and response.data.startswith(b'%PDF')
    assert 'attachment' in response.headers['Content-Disposition']
    pdf=PdfReader(BytesIO(response.data))
    text='\n'.join(page.extract_text() for page in pdf.pages)
    assert '킥로그' in text and '추가 정정 내용' in text and '긴 메모 테스트' in text
    assert '총 22건' in text and text.count('종합 결과:')==22
    default_pdf=client.get('/records/export.pdf?period=all')
    default_text=''.join(p.extract_text() for p in PdfReader(BytesIO(default_pdf.data)).pages)
    assert '긴 메모 테스트' not in default_text and '추가 정정 내용' in default_text
    assert len(pdf.pages)>1
    assert '입력시각' in text and '사용자가 입력한 관리 기록' in text
    assert client.get('/records/export.pdf?type=battery&period=all').status_code==303


def test_fixture_failure_isolated_and_no_keys_exposed(app,client,tmp_path):
    fixtures=app.config['FIXTURES']
    assert [len(f['records']) for f in fixtures.values()]==[1,1,1]
    assert fixtures['battery_catalog']['records'][0]['battery_pack_model'] is None
    assert fixtures['repair_centers']['records'][0]['latitude'] is None
    app.config['KAKAO_MAP_JAVASCRIPT_KEY']='javascript-only-test'
    page=client.get('/repair-centers').text
    assert 'javascript-only-test' in page and 'REST_API_KEY' not in page
    assert '실제 수리센터나 제휴 업체가 아닙니다' in page
    for name, payload in fixtures.items():
        (tmp_path/f'{name}.json').write_text(json.dumps(payload))
    (tmp_path/'battery_catalog.json').write_text('{broken')
    with app.app_context():
        data, errors=load_fixtures(tmp_path)
        assert errors==['battery_catalog'] and len(data['recalls']['records'])==1
    assert client.get('/catalog/batteries').json['mode']=='local_fixture'


def test_database_failure_keeps_input(app,client,monkeypatch):
    register(client)
    original=db.session.commit
    def broken(): raise OperationalError('test',None,Exception('database unavailable'))
    with monkeypatch.context() as patch:
        patch.setattr(db.session,'commit',broken)
        data=dict(csrf_token=csrf(client),nickname='입력값 유지',manufacturer='Other',model='X',weekday='6')
        response=client.post('/scooters',data=data)
        assert response.status_code==503 and '입력값 유지' in response.text and '저장하지 못했습니다' in response.text
    with app.app_context(): assert Scooter.query.count()==1


def test_pdf_failure_preserves_query(app, client, monkeypatch):
    register(client)
    client.post('/inspections',data=inspection_data(client))
    app.config['PDF_FONT_PATH']='/nonexistent/font.ttf'
    response=client.get('/records/export.pdf?period=all&type=inspection')
    assert response.status_code==503 and '같은 조건으로 다시 시도' in response.text
    assert 'type=inspection' in response.text
    app.config['PDF_FONT_PATH']=''
    from app.services import pdf
    def broken(*args,**kwargs): raise RuntimeError('renderer failed')
    monkeypatch.setattr(pdf,'export_records',broken)
    response=client.get('/records/export.pdf?period=all&type=inspection')
    assert response.status_code==503 and '조회 조건은 유지됩니다' in response.text
