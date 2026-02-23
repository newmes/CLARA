# 5. Simulation Engine

## Agent 명

| Agent | 역할 | 호출 빈도 |
|-------|------|-----------|
| **Rule Set Generation Pipeline** | 10개 이상의 생물의학 DB에서 evidence를 수집하고 LLM이 시뮬레이션 규칙(AE 프로파일, 인구통계 분포, 효능 파라미터)을 합성 | 약물당 1회 |
| **Patient Agent** | Rule set 기반으로 내적 일관성 있는 가상 환자(EMR·페르소나) 생성 | 환자당 1회 |
| **Daily Agent** | Hazard function 기반 일별 환자 상태(Labs, Vitals, AE, 투약, 종양 반응) 시뮬레이션 | 환자 × 일수 |
| **Care Agent** *(선택)* | AI 간호사-환자 영상통화를 시뮬레이션하여 조기 개입 효과 평가 | 병원방문일 |

## 사용 모델

- **LLM**: Google Gemini 2.0 Flash (`gemini-2.0-flash`)
  - Rule Set Generation: Evidence → Rule set 합성 (OpenAI-compatible endpoint)
  - Patient Agent: 동반질환 확률 보정, 기저값 생성, 페르소나 생성
  - Daily Agent: 환자별 AE 위험도 초기 보정 (인스턴스 생성 시 1회)
- **Hazard Engine** (코드 기반): Daily Agent의 매일 이벤트 결정은 LLM 호출 없이 hazard function + 난수 샘플링으로 수행

## 파이프라인 (흐름도)

```
┌─────────────────────────────────────────────────────────────────────┐
│  INPUT: Drug Name + Indication                                      │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Phase 0: Rule Set Generation Pipeline                               │
│                                                                      │
│  Evidence Collection (10+ DB 병렬 수집):                             │
│  ─ OpenFDA, DailyMed, ChEMBL, PubChem, DrugBank (약물별)            │
│  ─ ClinicalTrials.gov, PubMed, PrimeKG (적응증별)                   │
│  ─ ONSIDES, MeSH, Project Data Sphere                                │
│                     ↓                                                │
│  LLM Synthesis: Evidence → Structured Rule Set (JSON)                │
│  ─ AE 프로파일 (발생률, Grade 분포, onset/duration 분포)             │
│  ─ 인구통계 분포 (나이, 성별, 인종, ECOG)                            │
│  ─ 효능 파라미터 (ORR, CR율, PFS/OS 중앙값)                         │
│  ─ 투약 스케줄, 용량 변경 규칙, 보조 요법 규칙                       │
│                                                                      │
│  OUTPUT: Rule Set (JSON)                                             │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Phase 1: Patient Agent  ×  N명 (병렬)                               │
│                                                                      │
│  Step 1  인구통계 샘플링 (코드) ─→ age, sex, race, ECOG              │
│  Step 2  동반질환 (LLM 확률 보정 + 난수) ─→ 기저 질환 목록            │
│  Step 3  기저값 생성 (LLM) ─→ baseline labs, vitals, 종양 정보        │
│  Step 4  페르소나 생성 (LLM) ─→ 증상 보고 성향, 언어 장벽 등          │
│                                                                      │
│  OUTPUT: 환자 JSON (EMR + 페르소나 + 초기 상태)                       │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Phase 2: Daily Agent  ×  Day 0 ~ Day T (순차)                       │
│                                                                      │
│  Day 0  ─ Pre-treatment baseline (EMR 값 그대로, LBBLFL=Y)           │
│                                                                      │
│  Day 1 ~ T  ─ 10-Step Pipeline (매일 반복):                          │
│   ┌─ Step 1: AE onset (hazard function → 확률적 발생)                │
│   ├─ Step 2: AE grade 변화 / 해소                                    │
│   ├─ Step 3: 종양 크기 변화 + RECIST 평가                            │
│   ├─ Step 4: 투약 처리 (dose hold/reduction/discontinuation)         │
│   ├─ Step 5: Labs 생성 (OU process + AE·CM 인과 반영)                │
│   ├─ Step 6: Vitals 생성 (OU process + AE 반영)                     │
│   ├─ Step 7: CRF 도메인 매핑 (CDASH 표준)                           │
│   ├─ Step 8: AE cascade 업데이트                                     │
│   ├─ Step 9: ECOG PS 동적 계산                                       │
│   └─ Step 10: 사망 확률 계산 + 판정                                  │
│                                                                      │
│  [병원 방문일] 관찰 모델 → Dose modification → 병용약물 처방          │
│  [Care AI 모드] Care Agent 영상통화 → 개입 → 다음 날 반영            │
│                                                                      │
│  OUTPUT: 일별 JSONL (환자당 1파일, Day 0~T 레코드)                    │
└──────────────────────────────────────────────────────────────────────┘
```

## 간략 설명

약물명과 적응증만 입력하면, **evidence 기반 rule set 생성부터 일별 환자 데이터 출력까지** 전 과정을 자동으로 수행한다.

**Phase 0**에서 Rule Set Generation Pipeline이 OpenFDA, DailyMed, ChEMBL, PubChem, DrugBank, ClinicalTrials.gov, PubMed, PrimeKG, ONSIDES, MeSH, Project Data Sphere 등 10개 이상의 생물의학 데이터베이스에서 근거를 병렬 수집한 후, LLM(Gemini 2.0 Flash)이 수집된 근거를 종합하여 구조화된 시뮬레이션 규칙(JSON)을 합성한다. 이 과정에서 AE 프로파일, 인구통계 분포, 효능 파라미터, 투약 스케줄 등이 실제 임상시험 데이터에 기반하여 결정된다.

**Phase 1**에서 Patient Agent가 rule set의 인구통계·동반질환 분포로부터 N명의 가상 환자를 생성한다. 각 환자는 demographics, baseline labs/vitals, 종양 정보, 동반질환, 기저 약물, 행동 페르소나를 포함하며, 내적 일관성이 보장된다 (예: CKD 환자 → creatinine↑, eGFR↓).

**Phase 2**에서 Daily Agent가 각 환자를 Day 0(screening baseline)부터 Day T까지 일별로 시뮬레이션한다. 매일의 AE 발생은 rule set의 onset 분포에서 계산된 hazard function과 난수로 결정되며, 현재 활성 AE·투약 상태·개입 이력에 따라 확률이 동적으로 조정된다. Labs와 vitals는 Ornstein-Uhlenbeck (OU) process로 생리학적 연속성을 유지하면서, AE-Lab 인과 모델(예: neutropenia → ANC↓)과 병용약물 효과(예: Filgrastim → ANC 교정)를 반영한다.

출력은 CDASH 표준 도메인(AE, LB, VS, EC, CM, DS, RS, TU, DD, PE, EG)으로 매핑되어, 실제 임상시험 CRF와 동일한 구조를 갖는다.

## 예시 데이터 샘플

### Input: Rule Set (발췌)
```json
{
  "drug_name": "Etoposide + Cisplatin",
  "indication": "small cell lung cancer",
  "ae_profile": [
    {
      "ae_term": "neutropenia",
      "incidence_all_grade": 0.82,
      "grade_distribution": {"1": 0.10, "2": 0.20, "3": 0.35, "4": 0.17},
      "onset_day": {"distribution": "lognormal", "params": {"mean": 10, "std": 4}},
      "duration_days": {"distribution": "normal", "params": {"mean": 7, "std": 3}}
    }
  ],
  "demographics": {
    "age": {"distribution": "normal", "params": {"mean": 65, "std": 10, "min": 18, "max": 85}},
    "sex": {"options": {"Male": 0.65, "Female": 0.35}}
  }
}
```

### Output: Patient Profile (발췌)
```json
{
  "patient_id": "PT-001",
  "emr": {
    "demographics": {"age": 72, "sex": "F", "race": "White"},
    "baseline_ecog": 1,
    "medical_history": [
      {"condition": "Coronary Artery Disease", "ongoing": true, "medication": "Aspirin"},
      {"condition": "Diabetes", "ongoing": true, "medication": "Sitagliptin"}
    ],
    "baseline_labs": {
      "hemoglobin": {"value": 9.2, "unit": "g/dL"},
      "ANC": {"value": 3.3, "unit": "x10^9/L"},
      "creatinine": {"value": 0.94, "unit": "mg/dL"}
    }
  },
  "persona": {"type": "language_barrier"}
}
```

### Output: Daily Record — Day 10, Cycle 1 (발췌)
```json
{
  "patient_id": "PT-001",
  "day": 10, "cycle": 1, "cycle_day": 10,
  "AE": [
    {"AETERM": "thrombocytopenia", "AETOXGR": 2, "AESTDAT": 10, "AESER": "N"},
    {"AETERM": "fatigue", "AETOXGR": 1, "AESTDAT": 10, "AESER": "N"},
    {"AETERM": "constipation", "AETOXGR": 1, "AESTDAT": 4, "AESER": "N"}
  ],
  "LB": {
    "results": {
      "ANC": {"LBORRES": 3.0, "LBORRESU": "x10^9/L", "LBNRIND": "NORMAL"},
      "hemoglobin": {"LBORRES": 9.0, "LBORRESU": "g/dL", "LBNRIND": "LOW"},
      "creatinine": {"LBORRES": 1.05, "LBORRESU": "mg/dL", "LBNRIND": "NORMAL"},
      "glucose_fasting": {"LBORRES": 138.0, "LBORRESU": "mg/dL", "LBNRIND": "HIGH"}
    }
  },
  "VS": {
    "SYSBP_VSORRES": 138, "DIABP_VSORRES": 79, "PULSE_VSORRES": 80,
    "TEMP_VSORRES": 36.9, "WEIGHT_VSORRES": 78.0
  },
  "EC": [
    {"ECREFID": "Etoposide", "ECDSTXT": "189.5", "ECDOSU": "mg/m2", "ECROUTE": "INTRAVENOUS"},
    {"ECREFID": "Cisplatin", "ECDSTXT": "151.6", "ECDOSU": "mg/m2", "ECROUTE": "INTRAVENOUS"}
  ],
  "CM": [
    {"CMTRT": "Sitagliptin", "CMINDC": "Diabetes", "CMDOSFRQ": "QD"}
  ]
}
```
