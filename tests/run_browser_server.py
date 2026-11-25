import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app import create_app
db_path = Path('tmp/browser-qa.db').resolve()
db_path.parent.mkdir(parents=True, exist_ok=True)
db_path.unlink(missing_ok=True)  # Only the dedicated browser-test database.
app=create_app({'SQLALCHEMY_DATABASE_URI':'sqlite:///' + str(Path('tmp/browser-qa.db').resolve()), 'CLOCK':lambda:datetime(2026,11,25,3,tzinfo=timezone.utc), 'KAKAO_MAP_JAVASCRIPT_KEY':''})
app.run(host='127.0.0.1',port=5001)
