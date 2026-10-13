# 네이버 블로그 실제 검색화면 수집기 | 클라우드 웹 V1.0

기존 Windows 프로그램 V2.2의 **화면 DOM 추출 JavaScript, 검색 탭 탐색, 블로그명 보완, 엑셀 저장** 로직을 재사용한 웹 버전입니다.

## 주요 기능
- 네이버 검색 API, 키 발급 **불필요**
- Playwright Chromium 실제 검색화면(모바일 웹 재현 또는 PC 웹)을 브라우저에서 열어 수집
- 키워드 최대 8개, 키워드별 최대 100건 / 스크롤 대기 조절
- 제목, 블로그명, 화면 날짜, 블로그 URL, 화면 수집 순서 수집
- 블로그명 누락 시 공개 블로그 화면 추가 확인 옵션
- 웹에서 작업상태/로그/결과 목록 조회, 수집 엑셀 다운로드
- 네이버 접근제한 발생 시 차단 우회 없이 중지하며 실패 상태와 기록 제공
- 하나의 서버 프로세스에서 동시 실행 1건 제한 (서버 과부하 방지)

## Railway 배포 (GitHub 저장소 필요)
1. 이 프로젝트 파일을 GitHub의 **별도 저장소**에 업로드합니다 (`Dockerfile`은 루트 경로).
2. Railway → `New Project` → `Deploy from GitHub repo` → 저장소 연결.
3. 설정 → Variables에서 `APP_ACCESS_KEY`에 직접 만든 충분히 긴 랜덤 문자열을 설정합니다. 클라우드에선 이 값이 없으면 수집 API가 비활성화됩니다. 저장소에는 인증코드를 넣지 마세요.
4. `PORT`는 Railway가 자동 공급합니다. `Dockerfile`은 공식 Playwright Python 이미지 + Chromium을 사용합니다.
5. Railway → Settings → Networking → Generate Domain 으로 주소를 발급받습니다.
6. 서비스가 실행된 후 `/health`의 `ok: true` 및 `authConfigured: true`를 확인합니다.
7. 웹페이지에서 사이트 접근코드 입력 → 키워드 `아기첫과일`, 10건, 스크롤 5회로 먼저 실제 수집합니다.
8. 수집 기록, 웹 게시글 목록, 엑셀 시트 `화면 수집 결과`와 `키워드별 현황`에서 제목/블로그명/실제 게시글 URL이 일치하는지 확인합니다.

**중요:** Docker 이미지 실행/건강 상태(`health`)는 네이버에서 실제 데이터 수집에 성공했다는 증거가 아닙니다. 클라우드 IP가 검색 페이지에서 제한될 수 있습니다. 성공 여부는 *배포된 서버에서 실제로 수집한 게시글 목록과 엑셀*로 검증해야 합니다.

## 로컬 테스트
```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```
브라우저에서 http://127.0.0.1:8080/ 접속. 로컬에서 `APP_ACCESS_KEY`를 지정하지 않았다면 접근코드를 생략합니다.

## 검증
```bash
python -m pip install pytest httpx
python -m pytest -q tests/test_cloud.py
```
- 로컬 Chromium 테스트: 실제 브라우저에 넣은 **모의 HTML** DOM에서 제목, 블로그명, URL, 순서 추출 검증
- HTTP 통합 테스트: 작업 생성 → 로그/상태 조회 → 엑셀 생성 → 다운로드/접근코드 인증 검증
- 실패 시 `오류` 상태 및 빈 수집 결과 엑셀 검증
- **실제 네이버 페이지 접속과 클라우드 IP에서의 수집은 배포 후 실검증이 별도로 필요**

## 운영 유의사항
- `RAILWAY_ENVIRONMENT` 값이 있는 환경에선 `APP_ACCESS_KEY` 설정이 필수입니다.
- 데이터/작업 상태는 서비스의 임시 파일과 메모리에 보관합니다. 2시간 뒤 또는 재배포/재시작 시 사라질 수 있으므로 바로 내려받으세요.
- 앱은 1 프로세스, 1 인스턴스 기준입니다. 수평 확장(다중 replica) 환경은 별도 작업 큐와 공유 저장소 도입 필요.
- 서버 운영 비용이 발생할 수 있으며 Chromium 메모리를 고려한 서버 요금제가 필요합니다.
- 과도한 자동 수집을 자제하고, 네이버의 정책, 접근 제한 안내를 준수하세요. 캡차/접근 제한을 우회하는 기능은 없습니다.
- 순번은 수집된 네이버 **블로그 글 기준**이며 네이버 앱/개인화/광고가 포함된 전체 검색의 절대순위를 의미하지 않습니다.

## 클라우드 서버 자체 브라우저 진단
배포 후 다음 API를 호출하면 **서버가 실제 Chromium을 실행해서 모의 검색화면 DOM을 읽을 수 있는지** 확인할 수 있습니다.
```bash
curl -H 'X-Access-Key: 실제접근코드' 'https://배포도메인/api/browser-check'
```
정상 응답: `{"browserReady":true,"domExtractionReady":true,"liveNaverVerified":false}`.
서버 브라우저 구동 검증과 실제 네이버 페이지 수집 성공 여부를 절대 혼동하지 마세요.

## 클라우드 서버 자체 브라우저 진단
배포 후 다음 API를 호출하면 **서버가 실제 Chromium을 실행해서 모의 검색화면 DOM을 읽을 수 있는지** 확인할 수 있습니다.
```bash
curl -H 'X-Access-Key: 실제접근코드' 'https://배포도메인/api/browser-check'
```
정상 응답: `{"browserReady":true,"domExtractionReady":true,"liveNaverVerified":false}`.
서버 브라우저 구동 검증과 실제 네이버 페이지 수집 성공 여부를 혼동하지 마세요.
