from datetime import datetime, timezone
import pytest
from app import create_app
from app.models import db

@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING':True, 'SQLALCHEMY_DATABASE_URI':'sqlite:///' + str(tmp_path/'test.db'), 'CLOCK':lambda:datetime(2026,11,25,3,tzinfo=timezone.utc)})
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()

@pytest.fixture
def client(app):
    return app.test_client()

def csrf(client):
    client.get('/')
    with client.session_transaction() as session:
        return session['csrf_token']

def register(client, **changes):
    data = dict(csrf_token=csrf(client), nickname='나의 출퇴근 킥보드', manufacturer='Xiaomi', model='M365', battery_catalog_id='xiaomi-m365-standard', weekday='6')
    data.update(changes)
    return client.post('/scooters', data=data)

def inspection_data(client, scooter_id=1, **changes):
    from html.parser import HTMLParser
    class Tokens(HTMLParser):
        values={}
        def handle_starttag(self, tag, attrs):
            a=dict(attrs)
            if tag=='input' and a.get('name')=='submission_token': self.values['token']=a['value']
    p=Tokens();p.feed(client.get(f'/inspections/new?scooter_id={scooter_id}').text)
    data=dict(csrf_token=csrf(client), scooter_id=str(scooter_id), submission_token=p.values['token'], inspected_on='2026-11-25', tire='ok', brake='ok', battery_body='ok', charger_port='ok', impact_water_result='no', notes='')
    data.update(changes)
    return data
