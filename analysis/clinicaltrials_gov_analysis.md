# ClinicalTrials.gov 공개 데이터 분석 및 활용 방안

## 1. 데이터 소스 개요

ClinicalTrials.gov에서 활용 가능한 공개 데이터는 크게 **3개 레이어**로 나뉜다.

---

### Layer A: ClinicalTrials.gov API v2 (시험 등록 + 요약 결과)

**접근 방법**: REST API, 무료, API 키 불필요, ~50 req/min  
**URL**: `https://clinicaltrials.gov/api/v2/studies/{NCT_ID}`

#### 데이터 구조 (2개 대섹션)

**① protocolSection (시험 등록 정보)**

| 모듈 | 주요 필드 | 우리 활용 |
|------|----------|---------|
| identificationModule | NCT ID, 시험 제목, 스폰서 | 시험 메타데이터 |
| statusModule | 전체 상태, 시작일, 완료일 | 시험 타임라인 |
| designModule | Phase, 등록 수, 무작위화, 눈가림, arms/groups | **시험 설계 파라미터 → SoA 캘린더 설정에 직접 활용** |
| armsInterventionsModule | Arm 설명, 약물명, 용량, 투여 경로 | **Drug A/B 설정 자동화** |
| outcomesModule | 1차/2차 평가변수 정의, 측정 시점 | **SoA의 outcome 평가 스케줄 도출** |
| eligibilityModule | 적격 기준 (나이, 성별, ECOG, 포함/제외) | **환자 인구통계 생성 규칙** |
| conditionsModule | 질환명 (MeSH term 포함) | 질환 매핑 |
| contactsLocationsModule | 시험 기관, 국가 | (참고용) |

**② resultsSection (시험 결과 — 완료된 시험만)**

| 모듈 | 주요 필드 | 우리 활용 |
|------|----------|---------|
| participantFlowModule | 각 단계별 참여자 수, 탈락 사유 | **시뮬레이션 탈락 모델** |
| baselineCharacteristicsModule | 연령(중위/분포), 성별 비율, 인종, 기저 특성 | **★ God Engine 환자 생성의 핵심 입력** |
| outcomeMeasuresModule | PFS, OS, ORR 등 실제 결과값, 통계 분석 | **종양 반응 모델 캘리브레이션** |
| adverseEventsModule | **SAE 테이블 + Other AE 테이블** | **★★★ AE 프로파일의 최고 우선 소스** |

#### AE 데이터 상세 구조

ClinicalTrials.gov의 AE 보고는 3개 테이블로 구성:

**① All-Cause Mortality**
- Arm별 사망자 수

**② Serious Adverse Events (SAE)**
- organ_system (MedDRA SOC): "Skin and subcutaneous tissue disorders"
- event_term (MedDRA PT): "Stevens-Johnson syndrome"
- arm별: participants_at_risk, participants_affected, events_count
- 즉, **빈도 데이터를 Arm별로 제공**

**③ Other (Non-Serious) Adverse Events**
- 5% 이상 빈도 AE만 보고 (threshold 명시)
- 구조는 SAE와 동일

→ **핵심 한계**: Grade(등급) 정보가 없다. "Serious"와 "Other"의 2분류만 있고, CTCAE Grade 1-5 구분은 논문에서만 확인 가능.

---

### Layer B: AACT Database (ClinicalTrials.gov의 구조화된 관계형 DB)

**접근 방법**: PostgreSQL 직접 접속 또는 CSV/pg_dump 다운로드  
**URL**: https://aact.ctti-clinicaltrials.org  
**운영**: Duke CTTI (Clinical Trials Transformation Initiative)  
**업데이트**: 매일 자정 ClinicalTrials.gov에서 동기화

#### AACT의 핵심 테이블 (54개 중 우리에게 관련된 것)

**프로토콜 테이블**

| 테이블명 | 내용 | 우리 활용 |
|---------|------|---------|
| studies | 전체 시험 메타 (nct_id, phase, enrollment, status) | 마스터 테이블 |
| design_groups | 예상 참여자 그룹 (Arm A, Arm B) | 약물 arm 매핑 |
| design_outcomes | 계획된 평가변수 | SoA 설계 |
| interventions | 약물/시술 상세 | 약물 설정 |
| eligibilities | 포함/제외 기준 전문 | 환자 생성 규칙 |
| conditions | 질환명 | 질환 매핑 |
| browse_conditions | NLM이 부여한 MeSH term | 표준 용어 |
| browse_interventions | NLM이 부여한 MeSH term | 약물 표준화 |
| facilities | 시험 기관 | (참고용) |

**결과 테이블**

| 테이블명 | 내용 | 우리 활용 |
|---------|------|---------|
| result_groups | 실제 참여자 그룹 (BG/OG/EG/FG 코드) | Arm-결과 연결 키 |
| baseline_measurements | **기저 특성 수치** (연령, 성별, ECOG 등) | **★ 환자 인구통계 정밀화** |
| outcomes | 실제 결과 수치 (PFS, OS, ORR) | 종양 반응 캘리브레이션 |
| outcome_counts | 분석 대상 인원 | 통계 검증 |
| reported_events | **★★★ AE 빈도 데이터** | AE 프로파일 |
| milestones | 참여자 흐름 단계별 수치 | 탈락 모델 |
| drop_withdrawals | 탈락 사유별 인원 | 탈락 모델 정밀화 |

#### reported_events 테이블 상세

```
nct_id              → NCT04223856
result_group_id     → (EG000 = Padcev+Pembro arm)
ctgov_group_code    → EG000
event_type          → serious / other
organ_system        → "Skin and subcutaneous tissue disorders"  (MedDRA SOC)
adverse_event_term  → "Rash maculo-papular"  (MedDRA PT)
subjects_at_risk    → 440
subjects_affected   → 114  (→ 빈도 = 114/440 = 25.9%)
event_count         → 114
```

→ **이것이 우리 AE 프로파일의 1차 소스가 될 수 있다.**

---

### Layer C: Individual Patient Data (IPD) 공유 플랫폼

ClinicalTrials.gov는 요약 데이터만 제공한다. 개별 환자 수준 데이터(IPD)는 별도 플랫폼에서 신청해야 한다.

| 플랫폼 | 특징 | EV-302 가능성 |
|--------|------|-------------|
| **Vivli** | 5,400+ 시험, 연구 계획서 제출 필요 | Pfizer/Astellas 데이터 있을 가능성 (Pfizer는 회원) |
| **Project Data Sphere** | 종양학 전문, 대조군 중심, 무료 | 대조군(항암화학요법)은 있을 수 있으나 실험군은 없을 가능성 |
| **YODA Project** (Yale) | 연구 계획서 심사, 고품질 | J&J/BMS 중심, Pfizer 일부 |
| **ClinicalStudyDataRequest.com** | Astellas 참여 | **EV-302의 Astellas 데이터 신청 가능성 있음** |

**IPD에 포함되는 필드** (CDISC/SDTM 표준):
- Demographics (DM): 연령, 성별, 인종, BMI
- Adverse Events (AE): **ae_term, ae_grade(CTCAE), onset_day, resolution_day, serious, action_taken, outcome**
- Labs (LB): 검사명, 결과값, 단위, 기준범위, 수집일
- Vital Signs (VS): SBP, DBP, HR, BT, 체중
- Tumor Assessment (TU/TR/RS): 병변 크기, RECIST 반응
- Concomitant Meds (CM): 약물명, 시작/종료일, 적응증
- Exposure (EX): 투여 용량, 투여일, 용량 변경 사유

→ **IPD를 확보하면 우리 시뮬레이션의 ground truth로 사용할 수 있다.** 하지만 신청-승인에 2-6개월 소요되므로 MedGemma 챌린지 마감(2주)에는 사용 불가.

---

## 2. EV-302 (NCT04223856) 데이터 현황

### 현재 확인 가능한 것

| 항목 | 상태 | 소스 |
|------|------|------|
| 프로토콜 상세 | ✅ 공개 | ClinicalTrials.gov protocolSection |
| 적격 기준 | ✅ 공개 | ClinicalTrials.gov eligibility |
| 기저 특성 (연령, 성별, ECOG, PD-L1) | ✅ NEJM 논문 | Powles et al. NEJM 2024 |
| AE 빈도 (Serious + Other) | ⚠️ 부분 공개 | resultsSection (아직 미등록일 가능성 있음) |
| AE Grade 분포 | ✅ NEJM 논문 Table + Supplement | 논문 Supplementary Table S6-S7 |
| PFS/OS 곡선 | ✅ 논문 | Kaplan-Meier 곡선 |
| 개별 환자 데이터 | ❌ 비공개 | Vivli/CSDR 신청 필요 |

### 중요: EV-302 resultsSection 등록 여부

ClinicalTrials.gov에 결과 데이터를 등록하는 데는 primary completion date 이후 12개월이 법적 기한이다. EV-302는 2023년 8월이 primary completion이므로, 결과가 등록되어 있을 가능성이 높다. 이것을 AACT에서 직접 쿼리하면 SAE/Other AE 빈도를 Arm별로 구할 수 있다.

---

## 3. 우리 연구에의 활용 방안

### 방안 1: AE 프로파일 자동 추출 (즉시 가능, 고가치)

**현재 문제**: soa_pipeline.py의 `PADCEV_PEMBRO_AE_PROFILE`이 수동으로 하드코딩되어 있다.

**개선**:
```
ClinicalTrials.gov API → NCT04223856 resultsSection
  → adverseEventsModule → reported_events
  → organ_system별 SAE/Other AE 빈도 자동 추출
  → 우리 AE 프로파일 dict 자동 생성
```

AACT 경유:
```sql
SELECT adverse_event_term, organ_system, event_type,
       subjects_affected, subjects_at_risk,
       ROUND(subjects_affected::numeric / subjects_at_risk * 100, 1) AS frequency_pct
FROM reported_events re
JOIN result_groups rg ON re.result_group_id = rg.id
WHERE re.nct_id = 'NCT04223856'
  AND rg.title LIKE '%enfortumab%pembrolizumab%'
ORDER BY frequency_pct DESC;
```

**가치**: 수동 하드코딩 대신 데이터 기반 프로파일. 논문에서 누락된 저빈도 AE도 포함 가능.

**한계**: Grade 분포는 여전히 논문에서 수동 추출 필요.

---

### 방안 2: 기저 특성 기반 환자 생성기 정밀화 (즉시 가능)

**현재 문제**: `generate_patient_demographics()`가 대략적인 분포를 사용 중.

**개선**:
```
ClinicalTrials.gov API → baselineCharacteristicsModule
  → 연령 분포 (중위, IQR)
  → 성별 비율 (78% 남성)
  → ECOG 0/1/2 비율
  → PD-L1 CPS 분포
  → 기저 CrCl 분포 (~30% <60)
  → 기저 당뇨 유병률
  → 인종/민족 분포
```

→ God Engine의 `generate_patient_demographics()`에 직접 연결.

---

### 방안 3: 유사 시험 대규모 AE 교차 검증 (1-2일 소요)

**목적**: 우리 AE 프로파일의 빈도/심각도가 합리적인지 다른 시험들과 교차 검증.

**방법**: AACT에서 유사 약물/질환 시험의 AE 데이터를 추출:

```sql
-- Enfortumab vedotin 관련 모든 시험의 AE 데이터
SELECT s.nct_id, s.brief_title, re.adverse_event_term, re.organ_system,
       re.subjects_affected, re.subjects_at_risk
FROM studies s
JOIN reported_events re ON s.nct_id = re.nct_id
JOIN browse_interventions bi ON s.nct_id = bi.nct_id
WHERE bi.mesh_term LIKE '%enfortumab%'
  AND re.event_type = 'serious';
```

대상 시험:
- **EV-301** (NCT03474107): Padcev 단독 2차 치료
- **EV-201** (NCT03219333): Padcev Phase 2
- **EV-103** (NCT03288545): Padcev+Pembro Phase 1b/2
- **KEYNOTE-045** (NCT02256436): Pembro 단독 2차 방광암
- **CheckMate 274** (NCT02632409): Nivo adjuvant 방광암

→ 여러 시험의 AE 빈도를 aggregate하면 우리 프로파일의 신뢰도가 크게 올라간다.

---

### 방안 4: 탈락 모델 구축 (선택적)

**현재 문제**: 시뮬레이션에서 환자 탈락 모델이 없다.

**데이터**:
```
participantFlowModule → milestones (각 단계 인원)
                      → drop_withdrawals (사유별: AE, 질병 진행, 동의 철회, 사망)
```

EV-302에서:
- 전체 등록: 442명 (EV+P arm)
- AE로 인한 EV 중단: ~34%
- AE로 인한 Pembro 중단: ~22%
- 가장 흔한 중단 사유: peripheral sensory neuropathy (10.7%)

→ `PatientState.treatment_status`의 상태 전이 확률을 데이터 기반으로 설정 가능.

---

### 방안 5: SoA 자동 추론 (야심적, 중장기)

**현재 문제**: SoA를 이미지에서 수동으로 읽어 코드화했다.

**가능성**: ClinicalTrials.gov의 프로토콜 문서에서 SoA 추출:
- `protocolSection.designModule` → 사이클 길이, 방문 스케줄
- `outcomesModule` → 평가 시점 ("tumor assessment every 6 weeks")
- 프로토콜 PDF (cdn.clinicaltrials.gov) → SoA 테이블 직접 추출

→ 새로운 약물/시험으로 확장할 때 SoA를 자동 생성하는 파이프라인.

---

### 방안 6: MedGemma 데모용 "N=500 시뮬레이션" 캘리브레이션 (고가치)

**목적**: 데모에서 "우리 시뮬레이션이 실제 임상시험 결과를 재현한다"고 보여주기.

**방법**:
1. AACT에서 EV-302 기저 특성 + AE 빈도 추출
2. N=500 시뮬레이션 실행
3. 시뮬레이션 결과 vs 실제 EV-302 결과 비교:
   - AE 발생률 (시뮬 vs 실제): maculopapular_rash 26% vs 25.9%
   - Grade 3+ 비율 유사?
   - Median PFS 유사? (12.5개월 타겟)
4. 불일치 시 God Engine 파라미터 조정 (캘리브레이션)

→ **심사위원에게 "데이터 기반 시뮬레이션"임을 입증하는 핵심 근거.**

---

## 4. 기술적 접근 전략

### 즉시 실행 (Day 1-2)

1. **ClinicalTrials.gov API로 NCT04223856 결과 데이터 fetch**
   - web_fetch로 JSON 수신
   - resultsSection이 있으면 AE 테이블 파싱
   - 없으면 → AACT에서 쿼리 (계정 생성 필요) 또는 논문 데이터 유지

2. **baseline_measurements에서 환자 인구통계 추출**
   - 연령 분포, 성별, ECOG 비율 → generate_patient_demographics() 업데이트

3. **유사 시험 AE 교차 검증 스크립트 작성**
   - EV-301, EV-201, EV-103, KEYNOTE-045 AE 데이터 수집
   - 우리 프로파일과 비교 테이블 생성

### 단기 (Day 3-5)

4. **자동 AE 프로파일 생성기**
   - NCT ID 입력 → AE 프로파일 dict 자동 생성
   - 새 약물/시험으로 확장 시 재사용 가능

5. **N=500 캘리브레이션 실행**
   - 실제 데이터와 시뮬레이션 비교 리포트 자동 생성

### 중장기 (챌린지 이후)

6. **SoA 자동 추론 엔진**
7. **IPD 신청** (Vivli/CSDR → EV-302 데이터)
8. **다른 시험으로 확장** (GBM+TMZ, AC-T 등)

---

## 5. 핵심 한계 및 주의사항

### ClinicalTrials.gov 데이터의 근본적 한계

1. **요약 데이터만 제공**: 개별 환자 시계열 없음. "Arm A에서 X명이 rash 발생"만 알 수 있고, "누가, 언제, 어떤 경과로" 발생했는지는 모름.

2. **Grade 정보 부재**: ClinicalTrials.gov는 Serious/Non-serious 이분법만 사용. CTCAE Grade 1-5는 논문 Supplement에서만.

3. **시간 정보 부재**: onset timing, duration, resolution 없음. "Day 14에 발생하여 Day 28에 해소"와 같은 정보는 IPD에서만.

4. **MedDRA 코딩 불일치**: 시험마다 코딩이 다를 수 있음 (MedDRA 버전 차이, Preferred Term vs Lower Level Term).

5. **결과 미등록 시험 존재**: 법적 의무에도 불구하고 ~30%의 시험이 기한 내 결과를 등록하지 않음.

### 우리 시뮬레이션과의 Gap

| 우리가 필요한 것 | ClinicalTrials.gov 제공 | Gap 해소 방법 |
|----------------|----------------------|-------------|
| AE 빈도 (%) | ✅ subjects_affected / at_risk | 직접 계산 가능 |
| AE Grade 분포 | ❌ | 논문 Supplement |
| AE onset timing (일) | ❌ | 논문 or 약물 IB(Investigator's Brochure) |
| AE duration/resolution | ❌ | IPD or 추정 |
| Lab 시계열 | ❌ | IPD or 생리학적 모델링 |
| 환자 인구통계 분포 | ✅ (요약) | 직접 사용 가능 |
| 종양 반응 (RECIST) | ✅ ORR/CR/PR | 직접 사용 가능 |
| 탈락 패턴 | ✅ (요약) | 직접 사용 가능 |

### 결론

ClinicalTrials.gov/AACT는 **AE 빈도**, **환자 인구통계**, **탈락 패턴**의 캘리브레이션 소스로 즉시 활용 가능하다. 그러나 우리 시뮬레이션의 핵심인 **시간적 동태**(onset timing, Lab 시계열, AE 진행 경과)는 논문과 의학적 모델링에 의존해야 한다. 가장 높은 ROI 활용은:

1. ★★★ **AE 빈도 자동 추출** → 하드코딩 제거, 데이터 신뢰도 향상
2. ★★★ **N=500 캘리브레이션** → 데모에서 "실제 데이터 재현" 입증
3. ★★ **환자 인구통계 정밀화** → 더 현실적인 환자 생성
4. ★ **유사 시험 교차 검증** → AE 프로파일 robust화
