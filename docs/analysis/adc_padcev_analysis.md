# ADC 후보 분석: OpenFDA FAERS 데이터 기반

## OpenFDA 데이터에서 보이는 패턴

```
약물별 "지배적 독성 장기" (= 그 약의 시그니처 AE)

Padcev:  Skin 1716 ★★★★★  → SJS/TEN BLACK BOX (FATAL)
Enhertu: Lung 2262 ★★★★★  → ILD (FATAL, CT에서만 감지)
Blenrep: Eye  1018 ★★★★★  → 각막병증 70% (시각적, 그러나 시장 철수)
Adcetris: Neuro 1159 ★★★★  → 말초신경병증 (비가역적)
Kadcyla: 고르게 분포        → 간/심장/신경 모두 중등도
Trodelvy: Blood 1036 + GI 1044 → 호중구감소 + 설사 (표준적)
Mylotarg: Death Rate 31.9%  → 가장 치명적이나 SOS 증후군 특수
```

---

## 🏆 최강 후보: Padcev + Pembrolizumab (EV-302)

### 왜 이 조합인가?

**FDA 2023 승인, 전이성 요로상피암 1차 치료 표준**
= 가장 최신, 가장 핫한 레지멘. 심사위원이 "이걸 지금 안 하면 누가 하나" 느낌.

**두 약의 독성이 겹치면서도 구별이 필요** = AI의 진짜 가치

```
환자에게 피부 발진이 생겼다.

가능성 1: Padcev 피부독성 (on-target, Nectin-4 발현)
→ 조치: Padcev 용량 감량 또는 중단

가능성 2: Pembrolizumab 면역매개 발진 (irAE)  
→ 조치: 스테로이드 투여

가능성 3: SJS/TEN (Padcev BLACK BOX)
→ 조치: 즉시 영구 중단 + 화상센터 전원 + 24시간 내 사망 가능

잘못 판단하면?
- SJS를 단순 발진으로 보고 약 계속 → 사망
- 단순 발진을 SJS로 보고 효과있는 치료 영구 중단 → 암 진행
- Pembro irAE를 Padcev 독성으로 보고 스테로이드 안 줌 → 악화

→ Care AI가 영상통화에서 발진 패턴을 분석하고
  Lab (호산구, CRP, LDH)과 교차검증해서
  3가지를 구별해야 한다
→ 이것이 MedGemma의 진짜 가치
```

---

### Criticality Matrix: Padcev + Pembro

| 모달리티 | Criticality | 구체적 근거 |
|---------|-------------|-----------|
| **Video** | 🔴 FATAL | SJS/TEN 조기 감지 = 생사 결정. 피부발진 70%, G3+ 14.5%. 발진 패턴(maculopapular vs 표적병변 vs 수포)으로 단순발진/irAE/SJS 감별. **첫 번째 사이클에서 주로 발생** → 첫 영상통화가 생사를 가름 |
| **Lab** | 🔴 FATAL | ① 고혈당/DKA: 17% any grade, 7% G3-4, **DKA 사망 보고** (항암제 중 유일). 혈당 >250 → 즉시 중단. ② AKI 5% (방광암 환자 = 이미 신기능 취약). ③ LFT (Pembro 간염). ④ TFT (Pembro 갑상선) |
| **Radiology** | 🔴 FATAL | 폐렴/ILD 4.5% serious, 0.2% 사망. Padcev ILD + Pembro 폐렴 감별 필요. CT 없이 불가능 |
| **EMR** | 🔴 FATAL | ① 기저 당뇨/HbA1c → DKA 위험 층화. ② 기저 신기능(eGFR) → AKI 위험. ③ 자가면역질환 병력 → Pembro irAE 위험. ④ 간기능 → Padcev 중등도 이상 간장애시 사용금기 |
| **Clinical** | 🟠 IRREVERSIBLE | PD-L1 발현, Nectin-4 발현 수준 → 치료 반응/독성 예측. FGFR 변이 → 대안 치료 존재 |
| **Persona** | 🔴 FATAL | 방광암 환자 특성: ① 고령 남성 (중위 69세), ② 비뇨기 증상 수치심 (혈뇨, 빈뇨 = DKA 증상과 겹침!), ③ "화장실 자주 가는 게 약 부작용인지 암 때문인지 나이 때문인지" 구별 불가 |

**6개 모달리티 중 5개 🔴 FATAL** (GBM과 동급!)

---

### Padcev+Pembro의 독보적 강점

#### 1. "Triple Rash Differential" — 영상 AI의 최고 난제

```
MedGemma가 영상통화 피부 사진을 분석:

[이미지 입력] → 
  형태학적 특징 추출:
    - 분포 패턴: 굴곡부(SDRIFE) vs 전신 vs 표적 병변
    - 병변 유형: 반구진 vs 수포 vs 박리
    - 점막 침범: 구강/결막 → SJS 경고 신호
    - 진행 속도: 이전 통화 대비 변화율
    
  + Lab 교차검증:
    - 호산구↑ → 약물과민반응 시사
    - LDH↑ + CRP↑ → SJS/TEN 중증도 지표
    - 간기능 → Pembro 간염 동반 여부
    
  → 판정: "단순 Padcev 발진 (G2)" vs "Pembro irAE" vs "SJS 조기 징후"
  → 각각 다른 액션 권고
```

**다른 어떤 항암제에서도 이런 "3-way 감별진단"은 없다.**

#### 2. "빈뇨의 역설" — 방광암만의 Persona 함정

```
환자: 70세 남성, 전이성 방광암, Padcev+Pembro 1차 치료 중

Week 2: "화장실을 좀 자주 가요"

일반 간호사의 반응: "방광암이니까 당연하죠"
                    혹은 "약을 시작하면 그럴 수 있어요"

Care AI의 반응:
  → 혈당 확인: 마지막 Lab에서 공복혈당 180 (기준치 초과)
  → BMI 28, HbA1c 7.2 (기저 당뇨 없었으나 전당뇨)
  → Padcev 시작 후 0.5개월 (고혈당 중위 발생 시점!)
  → "빈뇨가 방광 증상이 아니라 DKA 전구증상일 가능성"
  → 즉시 혈당/케톤 검사 권고
  
만약 놓치면: DKA → 혼수 → 사망
```

**방광암 + Padcev = "빈뇨"라는 증상이 3가지 원인을 가짐:**
1. 암 자체 (방광 자극)
2. 약 부작용 (DKA로 인한 삼투성 이뇨)  
3. 나이 (전립선비대)

→ AI만이 Lab + EMR + 시간적 패턴을 교차분석하여 구별 가능

#### 3. 기존 Nivolumab 작업 100% 재활용

```
Pembrolizumab ≈ Nivolumab (같은 PD-1 억제제)
→ irAE 프로파일 거의 동일
→ God Engine의 irAE 모듈을 그대로 사용
→ 4-barrier Persona 모델 그대로 사용
→ 위에 Padcev ADC 독성만 추가하면 됨

추가 구현 필요:
① Padcev 피부독성 모듈 (SJS/TEN 진행 모델)
② Padcev 고혈당/DKA 모듈 (혈당 시계열)
③ Padcev 신경병증 모듈 (누적 용량 의존)
④ 방광암 Persona (비뇨기 수치심 + 빈뇨 혼동)
```

---

## 다른 ADC 후보들은?

### Enhertu (T-DXd) — "폐에만 강한 약"

| 장점 | 단점 |
|------|------|
| Lung 2262 (ILD = 가장 치명적 AE) | Skin 179 (약함 → 영상통화 데모 약함) |
| 유방암 = I-SPY 데이터 연결 가능 | ILD는 CT에서만 감지 → 영상통화 가치 낮음 |
| 심독성 (trastuzumab 성분) | 시각적 "와" 모먼트 부재 |

**결론: MRI/CT 분석에는 강하지만 영상통화 데모에 약함**

### Blenrep — "눈에 특화된 약"

| 장점 | 단점 |
|------|------|
| Eye 1018 (각막병증 70%!) | 미국 시장 철수 (2022) |
| 눈 사진 → MedGemma 분석 매우 독특 | 규제 불확실성 → 심사위원 인상 나쁠 수 있음 |
| 다발성골수종 = MMRF CoMMpass 데이터 | 전체 모달리티 커버리지 낮음 |

**결론: 눈 독성은 독특하지만, 약 자체의 시장 상태가 문제**

### Adcetris — "신경에 가장 강한 약"

| 장점 | 단점 |
|------|------|
| Neuro 1159 (최고), Blood 2002 | Skin 719 (Padcev의 절반) |
| 호지킨 림프종 = 젊은 환자 | 오래된 약 (2011 승인) → 덜 핫함 |
| 신경병증 기능평가 via 영상통화 | SJS/TEN BLACK BOX 없음 |

**결론: 좋지만 Padcev가 모든 면에서 우위**

---

## 최종 비교: GBM+TMZ vs Padcev+Pembro

| 기준 | GBM+TMZ | Padcev+Pembro | 승자 |
|------|---------|--------------|------|
| 🔴 FATAL 모달리티 수 | 5/6 | 5/6 | 동률 |
| Video 임팩트 | 인지변화 감지 (미묘) | **SJS 감지 (극적, 시각적)** | Padcev |
| Lab 복잡도 | 림프구+CBC | 혈당+신기능+간기능+CBC | Padcev |
| Imaging 난이도 | **Pseudoprogression (최고 난제)** | ILD/CT (표준적) | GBM |
| AI 감별진단 | 유사진행 vs 진행 (2-way) | **단순발진 vs irAE vs SJS (3-way)** | Padcev |
| Persona 독보성 | **뇌손상 = 말 못함 (신경학적)** | 빈뇨 역설 (임상적) | GBM |
| 기존 작업 재활용 | 새로 구축 필요 | **Nivo ≈ Pembro (90% 재활용)** | Padcev |
| 약물 클래스 핫함 | TMZ (2005, 오래됨) | **ADC+ICI (2023, 최신!)** | Padcev |
| 데모 시각적 "와" | Brain MRI (전문적) | **피부 발진 비교 (누구나 이해)** | Padcev |
| 공개 데이터 | TCGA-GBM+BraTS (풍부) | EV-301/302 + OpenFDA (중간) | GBM |
| 상업적 임팩트 | 희귀암, 짧은 생존 | **방광암 1차 표준치료 (거대 시장)** | Padcev |
| 2주 내 완성 가능성 | 낮음 (새로 구축) | **높음 (기존 90% 재활용)** | Padcev |

### 점수: GBM 5 vs Padcev 7

---

## 🏆 최종 추천: Padcev + Pembrolizumab (EV-302)

### 결정적 이유 3가지:

**1. "SJS를 영상통화에서 잡았다" = 이 대회 최강의 데모 장면**

```
[Demo 영상 시나리오]

Day 8, 첫 번째 영상통화:
환자: "팔에 좀 뭐가 났는데..."
Care AI (MedGemma): [화면 분석] 
  → 양측 전완부 maculopapular rash, 
  → 점막 침범 없음, 표적 병변 없음
  → "Grade 1 Padcev 피부반응. 보습제 + 항히스타민 권고."

Day 12, 두 번째 영상통화:
환자: "입안이 좀 헐었어요, 눈도 충혈되고..."
Care AI (MedGemma): [화면 분석]
  → ⚠️ 구강 점막 미란 + 결막 충혈 = 점막 침범
  → ⚠️ 기존 발진이 체간으로 확산 + 수포 형성 시작
  → ⚠️ Lab: LDH 280→420 (48%), CRP 상승
  → 🚨 "SJS/TEN 조기 징후 감지. PADCEV 즉시 중단.
        피부과 긴급 의뢰. 24시간 내 악화 가능."
  
[Without Care AI]
  → Day 12: "좀 심해졌네요, 다음 외래에서 봅시다"
  → Day 15: 체표면적 30% 이상 박리 → TEN
  → Day 18: 패혈증 → 사망

[With Care AI]
  → Day 12: 즉시 중단 + 전원
  → Day 14: 스테로이드 + 보존적 치료
  → Day 21: 회복 → 4주 후 pembrolizumab 단독으로 전환 → 치료 지속
```

**2. 기존 Nivolumab God Engine의 90%를 재활용**

```
이미 완성된 것:
✅ irAE 확률 모델 (Nivo ≈ Pembro)
✅ 4-barrier Persona 프레임워크
✅ Disclosure probability 계산
✅ 시간적 진행 모델 (log-normal onset)
✅ 영상통화 시뮬레이션 구조

새로 추가할 것:
🔲 Padcev 피부독성 진행 모델 (G1→G2→SJS 경로)
🔲 Padcev 고혈당/DKA 모델 (혈당 시계열)  
🔲 Padcev 신경병증 모델 (누적 용량)
🔲 방광암 Persona (빈뇨 혼동, 비뇨기 수치심)
🔲 Triple Rash Differential 로직

= 기존 2주 작업 + 추가 3-4일
vs GBM = 거의 처음부터 2주
```

**3. ADC는 2024-2025 종양학의 #1 트렌드**

```
심사위원 관점:
"이 팀이 ADC+ICI 콤보의 독성 관리를 AI로 해결했다면,
 이 플랫폼은 모든 ADC(13개+)에 적용 가능하다.
 그리고 ADC 파이프라인에는 100개+ 후보가 있다.
 이건 단순히 $100K 상금이 아니라, 
 수십억 달러 ADC 시장의 핵심 인프라다."

vs GBM:
"흥미로운 연구지만, GBM 환자는 연간 ~12,000명(미국)이고
 중위 생존 15개월이다. 상업적 스케일이 제한적이다."

방광암 (Padcev 적응증):
"전이성 요로상피암만 연간 ~30,000명(미국).
 MIBC까지 확대되면 ~80,000명.
 게다가 Padcev+Pembro가 1차 표준이 되면서 
 모든 환자가 이 독성 프로파일에 노출된다."
```

---

## 제안: 실행 계획

```
Phase 1 (Day 1-2): Padcev+Pembro AE 프로파일 완성
  - EV-302 데이터에서 AE 빈도, Grade, onset timing 추출
  - SJS/TEN 진행 모델 구축 (G1 rash → warning signs → SJS)
  - DKA 혈당 시계열 모델 구축
  
Phase 2 (Day 3-4): God Engine 확장
  - 기존 Nivo irAE 모듈 → Pembro로 리네이밍 (거의 동일)
  - Padcev ADC 독성 모듈 추가
  - Triple Rash Differential 로직 구현
  
Phase 3 (Day 5-7): Persona + 시뮬레이션
  - 방광암 환자 페르소나 3명 설계
  - 180일 시뮬레이션 실행
  - "빈뇨 역설" 시나리오 구현
  
Phase 4 (Day 8-10): MedGemma 통합
  - 피부 발진 이미지 분석 (SJS 감별)
  - Lab 트렌드 해석 (혈당, 신기능)
  - CT 이미지 분석 (ILD 감별)
  
Phase 5 (Day 11-14): 데모 + 문서
  - 영상 시나리오 촬영/녹화
  - 3-page technical writeup
  - 제출 준비
```
