# HANDOFF

## Goal
CDISC CRF Tables 뷰어와 SAE Reports 페이지의 데이터 완전성 검증 및 UI 개선.

## Current Progress

### 1. Repository Setup
- Branch: `feature/doc-agent-enhancement` (git@github.com:newmes/vitals.git)
- SSH 클론 완료 (`/data2/workspace/vital`)

### 2. Docker Dev Server
- **Docker 컨테이너로 실행 중**: `vital-crf` (포트 `19001`)
- 접속 URL: `http://49.254.130.90:19001/`
- CRF Tables: `http://49.254.130.90:19001/doc/<run_id>/crf/`
- 이미지: `vital-dev` (Dockerfile.dev)
- 데이터 볼륨: `-v "/data2/workspace/vital/data:/app/data"`
- 전체 pip 의존성 컨테이너 내부 설치 완료 (venv freeze 기반)

### 3. CDISC 데이터 검증 완료
- `cdisc/` 디렉토리에 18개 JSON 스키마 (AE, CM, DA, DD, DM, DS, DV, EC, EG, LB, MH, MI, PE, PR, RP, RS, TU, VS)
- CRF Table에 구현된 도메인: 13개 (DA, DV, MI, PR, RP는 데이터 없어서 의도적 제외)
- **GT(Ground Truth) 소스**: 전체 CDISC 필드 포함 (AE에 AESER, AESDTH 등 모든 boolean 필드 확인됨)
- **HR(Hospital Record) 소스**: 간소화된 필드만 (ae, grade, onset_day, detected_day, detection_delay, channel, status)
- 기본 소스가 `hr`이라 CRF Table에서 AE가 8컬럼만 보임 -> 유저 인지 완료, HR 데이터 구조 변경은 나중에 할 예정

### 4. 실제 데이터에 있으나 CRF Table에 누락된 필드
- **RS**: `RSPERF`, `RSREASND` (CDISC 스키마 필드, 데이터에 존재)
- **RS**: `_tumor_change_pct`, `_nadir_pct`, `_description` (computed 필드)
- **AE**: `_visual` (computed 필드)
- -> 아직 추가 안 함 (유저가 먼저 테스트 확인 후 진행하기로)

### 5. UI 변경 완료
- **doc_hub.html**: `max-width: 960px` -> `1200px` (CRF Tables와 동일하게)
- **sae_report.html**:
  - B5(Narrative), C7(Dechallenge), C8(Rechallenge) 필드에 주황색 하이라이트 + "Review Required" 태그 추가
  - 상단 헤더: Re-generate AI + 워크플로우 버튼만 남김 (PDF/XML/Save 제거)
  - 하단 status bar의 PDF/XML/Save 버튼 사이즈 업 (`font-size: 0.88em, padding: 8px 18px`)

## What Worked
- **Docker로 서버 실행**: 호스트에서 직접 `runserver`하면 외부 접근 불가 (방화벽). Docker 포트 매핑(`-p 19001:9001`)으로 해결
- **SSH 클론**: HTTPS는 인증 실패, `git@github.com:` SSH URL로 성공
- **볼륨 마운트 주의**: `data` 디렉토리 이름에 공백 없음 확인 필요. 초기에 `"data "` (공백 포함)으로 마운트해서 데이터 못 찾는 이슈 발생

## What Didn't Work
- **호스트 포트 직접 접근**: `0.0.0.0:9001`, `0.0.0.0:9002`, `0.0.0.0:8300` 모두 외부 접근 불가. Docker 포트 매핑만 외부 접근 가능
- **Cloudflare 터널**: `cloudflared`가 `localhost:8200`(helix 프로젝트)만 터널링 중. 새 터널 생성은 시도 안 함
- **nick의 9000 포트**: `/data2/workspace/ClinicalTrialEngine/` 경로에서 다른 유저가 같은 프로젝트 돌리는 중. 건드리지 않음

## Next Steps
1. **HR 데이터 구조 변경**: 유저가 나중에 지시할 예정. HR active_aes에 CDISC 필드(AESER, AESDTH, AEREL 등) 추가
2. **RS 도메인 누락 필드 추가**: `RSPERF`, `RSREASND`를 RS_COLUMNS + aggregate_rs()에 추가
3. **RS computed 필드 추가 검토**: `_tumor_change_pct`, `_nadir_pct`, `_description`
4. **GT/HR 소스 전환 UI**: CRF Tables에 소스 토글 버튼 추가 (현재 코드에 없음)
5. **Dockerfile.dev 개선**: 현재 컨테이너 내부에 pip install로 의존성 설치했으나, Dockerfile에 requirements.txt 포함하면 재빌드 시 편리

## Key Files
- `frontend/viewer/crf_aggregator.py` — CRF 도메인 컬럼 정의 + 데이터 집계
- `frontend/templates/doc/crf_tables.html` — CRF Tables 프론트엔드
- `frontend/templates/doc/sae_report.html` — SAE Report 폼
- `frontend/templates/doc/doc_hub.html` — Document Hub (SAE 목록)
- `frontend/viewer/views.py` — Django views (crf_tables, api_crf_domain_data 등)
- `frontend/trial_server/settings.py` — DATA_DIR = `BASE_DIR.parent / 'data'`
- `cdisc/*.json` — CDISC 도메인 스키마 정의
- `Dockerfile.dev` — 개발용 Docker 이미지

## Running Dev Server
```bash
# 현재 실행 중인 컨테이너
docker ps | grep vital-crf

# 재시작 필요시
docker stop vital-crf && docker rm vital-crf
docker run -d --name vital-crf \
  -p 19001:9001 \
  -v "/data2/workspace/vital/data:/app/data" \
  vital-dev

# 의존성 설치 (컨테이너 재생성 시 필요)
pip freeze > /tmp/req.txt
docker cp /tmp/req.txt vital-crf:/tmp/
docker exec vital-crf pip install -r /tmp/req.txt

# 파일 수정 후 컨테이너 반영
docker cp <local_path> vital-crf:/app/<path>
```
