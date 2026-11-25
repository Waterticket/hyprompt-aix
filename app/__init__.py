import hmac
import os
import secrets
from pathlib import Path
import click
from dotenv import load_dotenv
from flask import Flask, abort, render_template, request, session
from sqlalchemy.exc import SQLAlchemyError
from .models import db

ROOT = Path(__file__).resolve().parent.parent

def create_app(config=None):
    load_dotenv(ROOT / '.env')
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    # Persist a local signing key across server restarts without modifying .env.
    secret_file = Path(app.instance_path) / '.secret-key'
    if not secret_file.exists():
        try:
            fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    app.config.from_mapping(
        SECRET_KEY=os.getenv('SECRET_KEY') or secret_file.read_text().strip(),
        SQLALCHEMY_DATABASE_URI='sqlite:///' + str(Path(app.instance_path) / 'kicklog.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        KAKAO_MAP_JAVASCRIPT_KEY=os.getenv('KAKAO_MAP_JAVASCRIPT_KEY', ''),
        DATA_DIR=ROOT / 'data', APP_TIMEZONE='Asia/Seoul',
        PDF_FONT_PATH=os.getenv('PDF_FONT_PATH', ''),
        MAX_CONTENT_LENGTH=128 * 1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
    )
    if config:
        app.config.update(config)
    db.init_app(app)
    from .services.core import load_fixtures, localtime
    with app.app_context():
        db.create_all()
        app.config['FIXTURES'], app.config['FIXTURE_ERRORS'] = load_fixtures(app.config['DATA_DIR'])
    from .routes import bp
    app.register_blueprint(bp)
    app.jinja_env.filters['dateko'] = localtime

    @app.before_request
    def protect_forms():
        session.setdefault('csrf_token', secrets.token_hex(32))
        if request.method == 'POST':
            if not hmac.compare_digest(session['csrf_token'].encode(), request.form.get('csrf_token', '').encode()):
                abort(400, description='요청 확인 정보가 만료되었습니다. 페이지를 새로고침한 뒤 다시 입력해주세요.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        db.session.rollback()
        app.logger.error('Database operation failed: %s', type(error).__name__)
        if request.method == 'POST':
            # The same form remains editable after a failed transaction.
            from .routes import recover_form
            return recover_form('저장하지 못했습니다. 입력 내용을 확인하고 다시 시도해주세요.')
        return render_template('error.html', title='자료를 불러오지 못했습니다', message='잠시 후 다시 시도해주세요.'), 503

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def error_page(error):
        message = error.description if error.code in (400, 404) else '요청을 처리하지 못했습니다. 입력 내용을 확인하고 다시 시도해주세요.'
        return render_template('error.html', title=f'{error.code} · 요청을 확인해주세요', message=message), error.code

    @app.cli.command('init-db')
    def init_db():
        """Create missing tables and validate read-only fixtures; never erase records."""
        db.create_all()
        fixtures, errors = load_fixtures(app.config['DATA_DIR'])
        click.echo('DB 준비 완료. 사용자 기록은 유지됩니다.')
        for name, data in fixtures.items():
            click.echo(f'{name}: {len(data["records"])}건')
        if errors:
            raise click.ClickException('자료 검증 실패: ' + ', '.join(errors))
    return app
