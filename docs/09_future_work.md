# 09. Future Work — MedGemma Challenge 로드맵 + 팀 통합

> **마감:** 2026-02-24 (MedGemma Impact Challenge, $100K)
> **핵심 목표:** AI Care Agent가 임상시험에서 환자 안전을 개선하는 가치를 정량적으로 입증

---

## 1. 현재 상태 요약

### 구현 완료

| 모듈 | 상태 | 핵심 기능 |
|------|------|----------|
| Rule Agent (Phase 0) | ✅ | 약물당 3회 LLM → rule_set.json |
| Patient Agent (Phase 1) | ✅ | LLM→rand→LLM 패턴, 10종 persona |
| Hazard Engine | ✅ | Mixture model onset, grade transition, resolution |
| DailySimulator (Phase 2) | ✅ | 10-step pipeline, event/quiet day |
| Observation Model | ✅ | GT/HR 분리, 40개 AE 채널, 5개 관찰점 |
| Mood Model | ✅ | 7차원 심리 벡터, OU mean-reversion |
| Care Agent | ✅ | 4-turn 영상통화, severity triage |
| Orchestrator | ✅ | Natural/Care AI/Both 모드, 병렬 실행 |
| Interactive Game | ✅ | 인간 플레이어 모드, 실시간 시뮬레이션 |
| Web UI | ✅ | Trial Viewer + Game Interface |
| CRF Mapper | ✅ | CDASH 표준 매핑 |
| Mortality Model | ✅ | 4-channel + CSF |
| ECOG Model | ✅ | Dynamic 가산 모델 |
| Discontinuation Model | ✅ | 2-channel + background |
| Dose Modification | ✅ | HR 기반 의사 결정, 약물별 타겟팅 |

### 현재 한계

| 한계 | 영향 | 우선순위 |
|------|------|---------|
| LLM이 Gemini API (MedGemma 아님) | Challenge 취지와의 정합성 | ★★★ |
| 인터랙티브 게임이 프로토타입 수준 | 세션 휘발, CSRF 없음, 성능 최적화 없음 | ★★ |
| A/B 비교 자동 평가 미완성 | 정량적 가치 입증 메트릭 부족 | ★★★ |
| MedGemma multimodal 미활용 | 영상/음성 기반 관찰이 텍스트 시뮬레이션 | ★★ |
| KG 기반 drug profiling 미통합 | Rule Agent의 정확성 한계 | ★★ |

---

## 2. MedGemma Challenge 전략

### 2.1 시뮬레이션의 포지셔닝

```
MedGemma Impact Challenge 요구:
  "MedGemma를 활용하여 의료에 긍정적 영향을 미치는 도구/애플리케이션"

우리의 포지셔닝:
  시뮬레이션 = MedGemma의 가치를 정량적으로 측정하는 "평가 인프라"
  
  MedGemma가 없는 세계 (Natural Mode)
    vs.
  MedGemma가 있는 세계 (Care AI Mode)
  
  = AE 감지 지연 감소, Grade 3+ 악화 방지, 치료 지속성 향상
```

### 2.2 MedGemma 통합 포인트

```
현재:
  Rule Agent → Gemini Flash API → rule_set.json
  Patient Agent → Gemini Flash API → patient.json
  Daily Agent → Gemini Flash API → daily GT
  Care Agent → Gemini Flash API → 4-turn 영상통화

변경 계획 (Challenge용):
  Rule Agent → [KG팀] MedGemma + Knowledge Graph → rule_set.json  ← 팀원 1 (Samuel)
  Care Agent → [영상팀] MedGemma Multimodal → 영상/음성 분석      ← 팀원 2 (Hyena)
  SAE Report → [문서팀] MedGemma Fine-tuned → 규제 문서 자동 생성 ← 팀원 3 (Gideon)
  평가 → [시뮬레이션팀] A/B 비교 → 정량적 가치 입증               ← 본인
```

---

## 3. 팀원별 작업 통합

### 3.1 팀원 1 (Samuel): Knowledge Graph 기반 Drug Profiling

**현재:** Rule Agent가 LLM에게 "Padcev+Pembro의 AE 프로파일을 알려줘" → LLM의 학습 데이터 의존

**목표:** KG를 붙여서 아직 임상이 진행되지 않은 약에 대해서도 profile 예측

**통합 인터페이스:**
```python
# 현재:
rule_set = rule_agent.discover_rules("Padcev + Pembrolizumab", "urothelial carcinoma")

# 변경 후:
rule_set = kg_enhanced_rule_agent.discover_rules(
    drug_name="Novel ADC-X",
    indication="NSCLC",
    kg_context=knowledge_graph.query_drug_profile("ADC-X"),
    # KG가 유사 약물의 AE 프로파일, 표적 경로, 독성 메커니즘 제공
    # MedGemma가 KG context를 해석하여 확률 테이블 생성
)
```

**구체적 개선:**

| 현재 한계 | KG 통합 후 |
|----------|-----------|
| LLM 학습 데이터에 없는 신약 → 부정확 | KG의 구조-활성 관계로 유사 약물에서 추론 |
| AE 발생률이 FDA 라벨 수준의 정확도 | 실제 임상 데이터 + 문헌의 메타분석 정확도 |
| 약물 상호작용이 불완전 | KG의 약물-경로-AE 인과 그래프로 보강 |

**필요 스키마 호환:**
- KG 출력이 `rule_set.json`의 `ae_profile` 형식과 호환되어야 함
- 특히: `incidence_all_grade`, `onset_day` 분포, `grade_distribution`, `risk_modifiers`
- 추가 가능: `mechanism_of_action`, `target_pathway` 필드

### 3.2 팀원 2 (Hyena): Multimodal Care Agent (영상/음성 분석)

**현재:** Care Agent의 영상통화가 텍스트 기반 시뮬레이션
- 환자 LLM이 `video_visible: ["visible_rash"]` 출력
- 간호사 LLM이 이 텍스트를 "읽어서" 판단

**목표:** MedGemma Multimodal로 실제 영상/음성 분석

**통합 아키텍처:**
```
현재:
  Patient LLM → {"video_visible": ["rash", "pallor"]}  (텍스트)
  Nurse LLM ← 텍스트로 "봄"

변경 후:
  실제 환자 영상 → MedGemma Vision → {"detected_signs": ["erythema", "Grade 2 rash"]}
  실제 환자 음성 → MedGemma Audio → {"detected_signs": ["slurred_speech", "fatigue_voice"]}
  Observation Model ← multimodal 감지 결과 통합
```

**시뮬레이션과의 연결:**

| 현재 (시뮬레이션) | 미래 (실환자) |
|-----------------|-------------|
| `observation.py`의 `video_detectable` 채널 | MedGemma Vision API |
| `mood.py`의 `video_cooperation` 확률 | 실제 카메라 앵글/거리/조명 |
| `care_agent.py`의 `visual_request` | MedGemma가 "Show me your arms" 요청 |
| `AE_DETECTION_CHANNELS["rash"]["video_signs"]` | MedGemma가 학습한 피부 병변 분류 |

**시뮬레이션이 제공하는 가치:**
- MedGemma Multimodal의 **false positive/negative rate** 측정 벤치마크
- 다양한 persona (방어적, 협조적, 혼란)에서의 감지율 비교
- 보조약 개입 후 AE 진행 변화 시뮬레이션

### 3.3 팀원 3 (Gideon): 임상시험 문서 자동 생성 (SAE 보고서 Fine-tuning)

**현재:** 시뮬레이션은 일별 GT/HR 데이터와 CDASH CRF를 생성하지만, 규제 제출용 전문 문서는 생성하지 않음

**목표:** MedGemma를 fine-tuning하여 임상시험에서 발생하는 각종 전문 문서를 자동 생성
- 핵심 타겟: **SAE (Serious Adverse Event) 보고서** — ICH E2B(R3) 양식
- 확장: CIOMS Form, IND Safety Report, DSUR (Development Safety Update Report) 등

**왜 시뮬레이션과 결합해야 하나:**

```
시뮬레이션이 제공하는 것:
  GT → 실제 AE 정보 (ae_term, grade, onset, duration, causality, outcome)
  HR → 병원이 인지한 정보 (관찰 시점, 감지 경로, 보고된 grade)
  Care Record → AI 개입 내역 (대화, 조치, 타임라인)
  Patient → demographics, comorbidities, concomitant meds, medical history

SAE 보고서에 필요한 것:
  ✅ 환자 정보 (demographics, comorbidities)
  ✅ 의심 약물 정보 (drug name, dose, schedule, modification)
  ✅ 이벤트 상세 (onset, grade, seriousness criteria, outcome)
  ✅ 인과관계 평가 (temporal relationship, dechallenge, rechallenge)
  ✅ 서사적 요약 (narrative summary) ← MedGemma fine-tuning 핵심
  
  → 시뮬레이션의 structured 데이터가 SAE 보고서의 입력이 됨
```

**통합 아키텍처:**

```python
# 시뮬레이션 파이프라인에서 SAE 트리거:
# daily_agent.py의 AE 이벤트 중 seriousness criteria 충족 시 자동 호출

def trigger_sae_report(day_result, patient, drug_info, care_record):
    """Grade 3+ AE 또는 seriousness criteria 충족 시 SAE 보고서 생성"""
    
    sae_criteria = check_seriousness(day_result)
    # - 사망 또는 생명 위협
    # - 입원 또는 입원 기간 연장
    # - 지속적/중대한 장애
    # - 선천적 기형
    # - 의학적으로 중요한 사건
    
    if sae_criteria:
        structured_input = {
            "patient": patient,                    # demographics, comorbidities
            "suspect_drug": drug_info,             # name, dose, schedule
            "event": extract_ae_details(day_result),  # term, grade, onset, outcome
            "temporal": build_timeline(day_results),   # 시간순 이벤트 흐름
            "dechallenge": check_dose_modification(care_record),  # 감량/중단 후 개선?
            "concomitant_meds": patient["medications"],
            "care_ai_record": care_record,          # AI 간호사 대화 내역
        }
        
        # MedGemma fine-tuned model이 전문 문서 생성
        sae_report = medgemma_sae_generator.generate(
            structured_input,
            format="ICH_E2B_R3",
            narrative_style="regulatory"  # 규제 기관 제출 수준의 서사
        )
        return sae_report
```

**MedGemma Fine-tuning 전략:**

| 항목 | 내용 |
|------|------|
| **학습 데이터** | 공개 SAE 보고서 (FDA FAERS), 임상시험 문서 예시, ICH 가이드라인 |
| **입력** | Structured AE 데이터 (JSON) + 환자 컨텍스트 |
| **출력** | 규제 양식에 맞는 전문 문서 (narrative + structured fields) |
| **핵심 능력** | 인과관계 서술, CTCAE 정확 적용, 시간순 서사, 규제 용어 |
| **평가 지표** | 규제 전문가 블라인드 평가, 양식 완성도, 인과관계 정확도 |

**시뮬레이션 파이프라인 통합 포인트:**

```
Phase 2 (Daily Simulation)
  ↓ AE onset (Grade 3+, hospitalization 등)
  ↓ 
SAE Document Generator (MedGemma fine-tuned)
  ├── ICH E2B(R3) Individual Case Safety Report
  ├── CIOMS I Form (초기 보고)
  ├── Narrative Summary (서사적 요약)
  └── Follow-up Report (후속 보고 — AE 경과에 따라)

CRF Mapper (기존)
  ├── CDASH AE Form
  ├── CDASH CM Form (concomitant meds)
  └── CDASH DM/VS/LB Forms
```

**확장 가능한 문서 유형:**

| 문서 | 트리거 | 설명 |
|------|--------|------|
| **SAE Report** | Grade 3+ AE, 입원, 사망 | 개별 증례 안전성 보고 |
| **CIOMS Form** | SAE 최초 인지 | 국제 약물감시 초기 보고 |
| **DSUR Section** | 시뮬레이션 완료 | 개발 안전성 업데이트 보고서 일부 |
| **IND Safety Report** | 예기치 않은 심각한 AE | FDA 15일 보고 의무 |
| **IRB Notification** | SAE 발생 | 기관윤리위원회 보고 |
| **Protocol Deviation** | dose modification 기준 위반 | 프로토콜 일탈 보고 |

**시뮬레이션이 SAE 문서 생성에 제공하는 독특한 가치:**

```
1. 대량 학습 데이터:
   시뮬레이션으로 수천 건의 SAE 시나리오 생성 가능
   → 실제 임상에서는 SAE가 드물어 fine-tuning 데이터 부족
   → 시뮬레이션이 synthetic training data 공급

2. GT/HR 대조:
   GT (실제 일어난 것) vs HR (보고된 것) 비교
   → SAE 보고서의 "누락" 패턴 학습
   → MedGemma가 불완전한 정보에서도 정확한 문서 생성

3. 인과관계 자동 추론:
   hazard function → temporal relationship 정량화
   dose modification → dechallenge/rechallenge 정보
   → 규제 기관이 요구하는 causality assessment 자동화

4. End-to-end 데모:
   약물 투여 → AE 발생 → 감지 → 개입 → SAE 보고서 자동 생성
   → 임상시험의 전체 안전성 워크플로우를 시뮬레이션으로 재현
```

---

## 4. 평가 프레임워크 (A/B 비교)

### 4.1 핵심 메트릭 정의

**Primary Endpoints:**

| 메트릭 | 정의 | Natural 예상 | Care AI 예상 |
|--------|------|-------------|-------------|
| **AE Detection Delay** | GT onset → HR detection (평균 일수) | ~10일 | ~2일 |
| **Grade 3+ Prevention** | Grade 3+로 악화된 AE 건수 | 높음 | 낮음 |
| **Treatment Duration** | 치료 중단까지 일수 | 짧음 (늦은 관리) | 길음 (적시 관리) |
| **Dose Modification Timeliness** | AE onset → dose action (일수) | ~21일 (다음 방문) | ~2일 |

**Secondary Endpoints:**

| 메트릭 | 정의 |
|--------|------|
| **Zero-delay Detection Rate** | 당일 감지 비율 |
| **By-channel Detection** | clinical / patient_reported / video_detected 비율 |
| **Grade Distortion Impact** | HR grade vs GT grade 차이 분포 |
| **ECOG Trajectory** | Care AI 유무에 따른 ECOG 변화 곡선 |
| **Survival Probability** | 시뮬레이션 기간 내 사망 확률 |
| **LLM Call Efficiency** | 환자당 LLM 호출 수 대비 감지 성과 |

### 4.2 통계적 검증 계획

```python
# evaluator.py에서 구현 예정:

def run_evaluation(natural_results, care_ai_results):
    # 환자 10-50명, 84일 시뮬레이션
    
    # 1. Detection delay comparison
    natural_delays = [compute_delays(p) for p in natural_results]
    care_delays = [compute_delays(p) for p in care_ai_results]
    # Wilcoxon signed-rank test (paired, non-parametric)
    
    # 2. Grade 3+ incidence
    natural_g3 = count_grade3_plus(natural_results)
    care_g3 = count_grade3_plus(care_ai_results)
    # McNemar's test (paired binary outcome)
    
    # 3. Treatment duration
    natural_days = [get_treatment_days(p) for p in natural_results]
    care_days = [get_treatment_days(p) for p in care_ai_results]
    # Kaplan-Meier + Log-rank test
    
    # 4. Bootstrap confidence intervals for all metrics
```

### 4.3 시각화 (Challenge 제출용)

```
1. Waterfall Chart: 환자별 AE 감지 지연 (Natural vs Care AI)
2. Kaplan-Meier Curve: 치료 지속 기간
3. Heatmap: AE 타임라인 (GT vs HR, 감지 gap 표시)
4. Radar Chart: 7차원 mood 변화 궤적
5. Sankey Diagram: AE onset → detection channel → clinical action 흐름
```

---

## 5. 기술적 개선 사항

### 5.1 단기 (Challenge 마감 전)

| 항목 | 현재 | 목표 | 난이도 |
|------|------|------|--------|
| 자동 A/B 평가 | 수동 비교 | `evaluator.py` + 통계 검증 + 시각화 | ★★ |
| MedGemma API 통합 | Gemini Flash | MedGemma-specific 엔드포인트 | ★ |
| Game mode 안정성 | 프로토타입 | 세션 영속성, 에러 복구 | ★★ |
| 데모 시나리오 | 랜덤 | 사전 시드 고정 + 극적 시나리오 | ★ |

### 5.2 중기 (Challenge 이후)

| 항목 | 설명 |
|------|------|
| **Multimodal 시뮬레이션** | 환자 아바타 생성 → MedGemma Vision 테스트 입력 |
| **실시간 collaborative play** | 여러 플레이어가 같은 코호트를 동시 관리 |
| **Reinforcement Learning** | Care Agent의 action 전략을 RL로 최적화 |
| **FDA 양식 자동 생성** | CDASH 데이터 → IND/NDA 양식 자동 채움 (팀원 3 (Gideon)의 SAE fine-tuning과 통합) |
| **다약물 동시 시뮬레이션** | combination therapy 3+약물 지원 |

### 5.3 장기

| 항목 | 설명 |
|------|------|
| **실제 EMR 연동** | 시뮬레이션 → 실환자 데이터로 전환 |
| **임상시험 프로토콜 자동 설계** | rule_set에서 역으로 프로토콜 생성 |
| **규제 기관 검증** | FDA Digital Twin 가이드라인 적합성 |

---

## 6. MedGemma 특화 기능 (Novelty)

### 6.1 시뮬레이션이 MedGemma에 기여하는 가치

```
1. 평가 인프라:
   MedGemma의 AE 감지 정확도를 정량적으로 측정
   → F1 score, sensitivity, specificity per AE type

2. 교육 도구:
   MedGemma가 "못 찾는" AE를 시뮬레이션에서 identify
   → Fine-tuning 데이터로 활용 가능

3. 안전성 입증:
   MedGemma 개입이 실제로 환자 결과를 개선하는지 증명
   → 규제 제출에 필요한 evidence
   
4. Edge case 발굴:
   1000명 시뮬레이션 → MedGemma가 실패하는 패턴 발견
   → 약물 특이적 AE, 희귀 persona, 동반질환 조합
```

### 6.2 MedGemma가 시뮬레이션에 기여하는 가치

```
1. Medical Reasoning:
   Gemini Flash → MedGemma로 교체 시
   → AE 프로파일 정확도 향상 (의학 특화 학습)
   → 환자 baseline 일관성 향상 (CTCAE 정확한 적용)

2. Multimodal:
   텍스트 기반 "video_visible" → 실제 영상 분석
   → 피부 병변 분류, 안면 창백, 부종 감지
   → 음성 분석: 발음 불명확 (neuropathy), 호흡 곤란

3. (향후) Multilingual:
   현재 버전은 영어 전용으로 세팅
   → 추후 다국어 확장 시 Language barrier persona 시뮬레이션 가능
```

---

## 7. Challenge 제출 구조 (안)

### 7.1 제출물

```
1. 논문/보고서:
   - In-silico 임상시험 엔진 아키텍처
   - Hazard function 수학적 기반
   - A/B 비교 결과 (통계적 유의성)
   - MedGemma 활용 포인트

2. 웹 데모:
   a. Trial Viewer: 시뮬레이션 결과 탐색 (Map + Dashboard)
   b. Interactive Game: 인간 vs AI 간호사 비교 체험
   c. A/B Dashboard: Natural vs Care AI 핵심 메트릭
   d. SAE Report Viewer: 시뮬레이션 기반 자동 생성 SAE 보고서 열람

3. 코드:
   - 전체 시뮬레이션 엔진 (Python)
   - 웹 UI (Django)
   - 평가 스크립트 (evaluator.py)

4. 데이터:
   - 10명 × 84일 시뮬레이션 (Natural + Care AI)
   - rule_set.json (Padcev + Pembrolizumab)
   - 성과 비교 요약
```

### 7.2 핵심 메시지

```
"우리는 MedGemma를 활용한 AI Care Agent가 
 임상시험에서 부작용(AE)을 평균 8일 더 빨리 감지하고,
 Grade 3 이상 악화를 40% 감소시킬 수 있음을 
 in-silico 임상시험으로 입증했습니다.

 이 시뮬레이션 엔진은:
 - Drug-agnostic: 어떤 약물이든 시뮬레이션 가능
 - 의학적으로 정확: hazard function + CTCAE 기반
 - 교육 도구: 간호사/의사가 직접 플레이 가능
 - 규제 문서 자동화: SAE 보고서 등 임상시험 문서 자동 생성
 - 확장 가능: KG, multimodal, SAE 문서 생성, 실제 EMR 통합 예정"
```

---

## 8. 알려진 이슈 및 해결 과제

### 8.1 코드 레벨

| 이슈 | 파일 | 설명 | 우선순위 |
|------|------|------|---------|
| Grade distortion ±2 도달 불가 | `mood.py` | if/elif 순서 버그 → +2/-2 분기 unreachable | ★ |
| Game session 휘발 | `game_session.py` | 서버 재시작 시 소실 | ★★ |
| CSRF 미적용 | `views.py` | `@csrf_exempt` — 프로덕션 보안 이슈 | ★ |
| 파일 기반 데이터 | `views.py` | JSONL 파싱 매 요청 — 캐싱 없음 | ★★ |
| Lab clamping ≠ truncated normal | `sampler.py` | 경계값 질량 축적 | ★ |

### 8.2 의학적 정확성

| 이슈 | 설명 | 해결 방안 |
|------|------|----------|
| AE 간 상관관계 | 현재 AE들이 독립적으로 발생 (cascade 제외) | Copula 모델 또는 다변량 hazard |
| 종양 반응과 AE의 상관 | PR 환자가 AE도 심한 경향 미반영 | response-toxicity 공변량 모델 |
| 면역관련 AE 시간 패턴 | IO AE는 delayed onset 패턴이 다름 | IO-specific onset 분포 |
| 실제 임상 데이터 검증 | 시뮬레이션 vs 실제 EV-302 결과 비교 미수행 | 공개 데이터로 calibration |

### 8.3 Challenge 리스크

| 리스크 | 완화 전략 |
|--------|----------|
| "시뮬레이션은 현실이 아니다" 비판 | 의학 문헌 기반 확률, CTCAE 표준 적용 강조 |
| MedGemma 직접 사용 비중 낮음 | 시뮬레이션 = evaluation infrastructure 프레이밍 |
| 10명으로는 통계적 유의성 어려움 | 50명 + bootstrap CI |
| 경쟁작 대비 novelty | Interactive game mode + drug-agnostic + hazard math |