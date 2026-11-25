# 킥로그

개인 전동킥보드의 기기 정보, 주간 점검, 배터리 교체 이력을 관리하는 로컬 웹앱입니다. `docs/kicklog-design.html`과 정적 시안을 기준으로 Flask·Jinja2·SQLite·SQLAlchemy로 구현했습니다. 로그인 없이 하나의 관리 공간에서 여러 기기를 관리합니다.

## 실행

Python 3.11 이상을 사용합니다. 기존 `.venv`에는 실행과 테스트 의존성을 설치했습니다.

```sh
cd /Users/waterticket/aixweb
source .venv/bin/activate
pip install -r requirements.txt
flask --app app init-db
flask --app app run --host 127.0.0.1 --port 5000
```

브라우저에서 <http://127.0.0.1:5000>을 엽니다. 첫 실행은 기기 0대이며, 앱이 시연용 소유 기기나 점검 이력을 만들지 않습니다. DB와 세션 서명 키는 `instance/`에 생성됩니다. `init-db`는 누락 테이블 생성과 fixture 검증만 수행하므로 재실행해도 사용자 기록이 유지됩니다. 서버 재시작 때도 저장된 DB를 그대로 사용합니다. 로컬 단일 사용자 운영을 대상으로 합니다.

## 사용할 수 있는 기능

- 대시보드: 전체 활성 기기 수, 선택 기기의 최근 점검·다음 일정, 전체 기기의 이번 달 관리 기록 수.
- 내 킥보드: 등록·수정·선택·보관, 제조일·시리얼번호, 공식 배터리 사양 연결, 교체 이벤트·일정 관리.
- 정기점검: 4개 항목의 정상·이상·미확인 결과와 충격·침수 이력, 실제 점검일·메모 저장. 이상 결과도 저장하며 폼 토큰으로 중복 전송을 방지합니다.
- 점검기록: 점검·배터리 교체 통합 조회, 기기·유형·기간·보관 여부 필터, 20건 단위 페이지, 당시 기기 정보 스냅샷, 정정 메모, 필터 전체 결과 한글 PDF 다운로드.
- 수리센터: ERICA 본관 테스트 장소 1건, 주소 복사·카카오맵 링크, Kakao 지도 SDK 연동과 실패 안내.
- 공통: 모바일 하단 메뉴, 키보드 포커스, 입력 오류 안내·입력값 유지, 미저장 이탈 안내, CSRF 보호, 저장 중 중복 클릭 차단.

정기점검 일정은 서울 날짜로 계산합니다. 조기 점검은 미래 일정을 완료하지 않으며, 지연 점검은 실제 점검일 이후의 첫 설정 요일로 다음 일정을 이동합니다. 과거 점검을 나중에 입력해도 최신 완료일이 뒤로 돌아가지 않습니다. 요일 변경은 이전 예정일을 변경 이력에 남깁니다.

저장 후 점검·교체 기록은 정정 메모로 보완합니다. 등록 당시의 초기 교체일은 기기 수정 폼에서 동일한 초기 이벤트를 수정하고 정정 이력을 남깁니다. 기기 보관은 과거 기록과 PDF를 유지합니다.

## 환경 설정

`.env.example`을 참고해 `.env`를 설정합니다. `.env`와 `instance/`는 버전관리에서 제외되어 있습니다.

```dotenv
KAKAO_MAP_JAVASCRIPT_KEY=
DATA_MODE=local_fixture
APP_TIMEZONE=Asia/Seoul
```

시간대와 자료 모드는 이번 설계에 따라 `Asia/Seoul`, `local_fixture`로 고정되어 있습니다. 선택적으로 `SECRET_KEY`를 지정할 수 있으며, 미지정 시 로컬 세션 서명 키를 `instance/.secret-key`에 안전한 파일 권한으로 생성합니다.

### 카카오 지도

1. `.env`의 `KAKAO_MAP_JAVASCRIPT_KEY`에 Kakao Developers의 JavaScript 키를 입력합니다.
2. 해당 키의 SDK 도메인에 `http://127.0.0.1:5000`과 필요 시 `http://localhost:5000`을 등록하고 지도 서비스 사용 설정을 확인합니다.
3. 서버를 재시작하고 수리센터 화면에서 확인합니다.

JavaScript 키만 수리센터 화면에 전달합니다. 브라우저용 키이므로 페이지에서 보일 수 있으며 REST·Admin 키를 입력하면 안 됩니다. 실제 키가 없는 현재 상태에서도 주소·외부 지도는 사용할 수 있습니다. 주소 변환 위치는 ‘캠퍼스 주소 기준’이며 본관의 정확한 위치로 표시하지 않습니다. 내 위치는 버튼 클릭 때만 요청하고 정확한 목적지 좌표가 미확인되어 거리 수치는 표시하지 않습니다.

SDK 구현은 [Kakao 공식 주소 변환 예제](https://apis.map.kakao.com/web/sample/addr2coord/)를 따릅니다. 실제 키·도메인 인증은 사용자의 설정 후 확인해야 합니다.

### 한글 PDF

ReportLab으로 PDF를 생성하며 한글 TrueType 글꼴을 포함합니다. 현재 macOS에서는 기본 설치된 AppleGothic을 사용합니다. 다른 환경에서는 한글 TTF 글꼴을 설치하고 `.env`에 경로를 지정하세요.

```dotenv
PDF_FONT_PATH=/absolute/path/to/NanumGothic.ttf
```

글꼴을 찾지 못하면 PDF 다운로드 화면에서 설정 안내를 표시합니다. PDF에는 필터 범위 전체 기록, 실제 발생일과 서울 입력시각, 항목별 결과·확인 기준·정책 버전, 선택한 경우 발견 사항·관리 메모가 포함되며, 정정 메모는 항상 원본과 함께 포함됩니다.

## 공식 자료

`data/`의 JSON 3종은 읽기 전용 fixture입니다. 앱은 외부 API로 배터리·리콜을 실시간 조회하지 않습니다.

- `battery_catalog.json`: Xiaomi M365 제조사 공개 표준 사양 1건. 사용자가 정확한 카탈로그 항목을 선택했을 때 연결합니다.
- `recalls.json`: M365 일부 생산분의 접이식 구조 나사 결함에 대한 과거 프랑스 공식 리콜 공지 1건. 모델 일치는 후보 안내이며 개별 대상 여부나 한국 적용을 확정하지 않습니다.
- `repair_centers.json`: 한양대학교 ERICA 본관 주소 1건. 실제 수리센터·제휴 업체가 아닌 테스트 장소입니다. 전화·영업시간·정확한 좌표는 미확인입니다.

각 항목의 공식 출처와 확인일을 화면에 표시합니다. fixture가 손상되면 해당 자료만 이용 불가로 안내하고 기기 등록·점검은 유지합니다. 사용자 이력은 별도 SQLite에 저장합니다.

## 검증

```sh
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

테스트는 격리된 임시 DB를 사용합니다. 서버 재시작·fixture 재검증 후 기록 유지, 다기기 선택·스냅샷, 결과 집계·중복 저장, 서울 자정·지연·과거 입력 일정, 교체·보관·필터, 다중 페이지 한글 PDF, CSRF·저장 실패 복구를 검증합니다.

브라우저 검수를 재실행하려면 Playwright와 Chrome이 설치된 환경에서 별도 터미널로 실행합니다. 전용 검수 서버는 `tmp/browser-qa.db`를 초기화하며 사용자 DB는 변경하지 않습니다. 기본 앱 서버도 5000번 포트에 실행한 상태에서 진행하세요.

```sh
.venv/bin/python tests/run_browser_server.py
# 다른 터미널에서; 프로젝트 루트 기준
node tests/browser.cjs
# Playwright가 별도 위치에 설치되어 있으면 PLAYWRIGHT_MODULE_PATH로 모듈 경로 지정
```

브라우저 검수 증거는 `output/qa/`에 있습니다. 데스크톱 1440px, 태블릿 768px, 모바일 390px에서 8개 화면씩 확인했습니다. 지도 성공 응답과 SDK 실패는 모의 SDK로 검증했으며 실제 유효 키로 인증하는 검증은 남아 있습니다. 자세한 완료 기준별 결과는 `docs/verification.html`을 확인하세요.

## 코드 구조

```text
app/
  __init__.py       앱 팩토리·환경 설정·CSRF·DB 초기화·오류 처리
  models.py         기기·일정·점검·교체·정정·일정변경 모델
  routes.py         화면과 저장·필터·PDF 라우트
  services/core.py  서울 날짜·일정·결과 집계·자료 검증·기록 조회
  services/pdf.py   한글 PDF 생성
  templates/       Jinja2 화면
  static/          시안 기반 CSS·브라우저 동작·Kakao 연동
```

Flask 앱 구성은 [Flask 공식 앱 팩토리 패턴](https://flask.palletsprojects.com/en/stable/patterns/appfactories/)을 따릅니다.
