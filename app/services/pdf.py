from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from flask import current_app
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, KeepTogether
from app.models import utcnow
from .core import today, localtime, annotations, ITEMS, RESULTS


def font_name():
    configured = current_app.config.get('PDF_FONT_PATH')
    paths = [configured] if configured else [
        '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
        '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
        '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
    ]
    for path in paths:
        if path and Path(path).is_file():
            name = 'KicklogKorean-' + str(abs(hash(str(Path(path).resolve()))))
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path))
            return name
    raise FileNotFoundError('Korean TrueType font is required')


def export_records(rows, filters, include_notes=False):
    font = font_name()
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=44, leftMargin=44, topMargin=48, bottomMargin=50, title='킥로그 관리 기록', author='KICKLOG')
    styles = {
        'title': ParagraphStyle('title', fontName=font, fontSize=22, leading=32, textColor=colors.HexColor('#006c5b'), spaceAfter=12, wordWrap='CJK'),
        'heading': ParagraphStyle('heading', fontName=font, fontSize=13, leading=21, textColor=colors.HexColor('#143c35'), spaceBefore=18, spaceAfter=8, keepWithNext=True, wordWrap='CJK'),
        'body': ParagraphStyle('body', fontName=font, fontSize=10, leading=17, spaceAfter=5, wordWrap='CJK', splitLongWords=True),
        'small': ParagraphStyle('small', fontName=font, fontSize=8, leading=13, textColor=colors.HexColor('#586a60'), spaceAfter=5, wordWrap='CJK'),
    }
    def p(text, style='body'):
        return Paragraph(escape(str(text)).replace('\n','<br/>'), styles[style])
    story = [p('킥로그 · 관리 기록', 'title'), p(f'생성시각 {localtime(utcnow())} · 총 {len(rows)}건 · 표시 시간대 Asia/Seoul', 'small')]
    scope = f"기간 {filters['start'] or '전체'} ~ {filters['end'] or '전체'} / 유형 { {'inspection':'정기점검','battery':'배터리 교체'}.get(filters['type'],'전체')}"
    story += [p(scope, 'small'), p('기기 범위: ' + ('선택 기기' if filters['scooter_id'] else '전체 기기') + (' · 보관 기기 포함' if filters['include_archived'] else ''), 'small'), p('데이터 출처: 사용자가 직접 입력한 점검·교체 이력. 공식 안전 인증 자료가 아닙니다.', 'small'), p('발견 사항·관리 메모: ' + ('포함' if include_notes else '제외') + ' · 정정 메모는 원본과 함께 포함', 'small'), p('사용자가 입력한 관리 기록입니다. 전문 점검 확인서나 보험 보상 확정 문서가 아닙니다.', 'small'), HRFlowable(width='100%', color=colors.HexColor('#cedbd2')), Spacer(1,10)]
    for row in rows:
        r, kind, snapshot = row['record'], row['type'], row['snapshot']
        story.append(p(f"{localtime(row['date'])} · {'정기점검' if kind == 'inspection' else '배터리 교체'} · {snapshot['nickname']}", 'heading'))
        story.append(p(f"기기 당시 정보: {snapshot['manufacturer']} / {snapshot['model']} · 시리얼 {snapshot.get('serial_number') or '미입력'}"))
        story.append(p(f"실제 {'점검' if kind == 'inspection' else '교체'}일: {localtime(row['date'])} / 입력시각: {localtime(r.created_at)} (서울)", 'small'))
        if kind == 'inspection':
            story.append(p(f'종합 결과: {RESULTS[r.overall_result]} / 정책 버전: {r.policy_version}'))
            for item in r.items:
                story += [p(f'{ITEMS[item.item_key][0]}: {RESULTS[item.result]}'), p(item.criteria_snapshot, 'small')]
            story.append(p('충격·침수 이력: ' + {'no':'없음','yes':'있음','unknown':'확인 어려움'}[r.impact_water_result]))
            if r.overall_result == 'issue':
                story.append(p('이상 기록: 사용·충전을 중단하고 제조사 또는 수리센터에 점검을 요청하세요.'))
        else:
            story += [p(f'배터리 모델: {r.battery_model or "미입력"} / 센터명: {r.center_name or "미입력"}')]
        if include_notes:
            story.append(p('메모: ' + (r.notes or '없음')))
        for a in annotations(kind, r.id):
            story.append(p(f'정정 메모 ({localtime(a.created_at)}): {a.text}'))
        story += [Spacer(1,8), HRFlowable(width='100%', color=colors.HexColor('#dde5df'))]
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor('#586a60'))
        canvas.drawString(44, 28, 'KICKLOG · 사용자가 입력한 관리 기록 · 전문 점검 확인서가 아닙니다')
        canvas.drawRightString(A4[0]-44, 28, f'{doc.page}')
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    output.seek(0)
    return output
