# 항암제 멀티모달 시뮬레이션 후보: "필수성" 기반 재평가

## 평가 기준 변경

기존: "데이터가 있는가?" (availability)
변경: **"이 모달리티가 없으면 환자가 죽거나 영구 장애를 입는가?"** (criticality)

등급:
- 🔴 FATAL: 이 모달리티 없으면 사망 가능
- 🟠 IRREVERSIBLE: 이 모달리티 없으면 영구 장애
- 🟡 IMPORTANT: 삶의 질 저하
- ⚪ NICE: 있으면 좋음

---

## 5개 후보 Criticality Matrix

### 1. GBM + TMZ/RT (교모세포종 + 테모졸로마이드/방사선)

| 모달리티 | Criticality | 근거 |
|---------|-------------|------|
| **EMR** | 🔴 FATAL | KPS, 수술 범위, 스테로이드 용량이 치료 결정 좌우. 동반질환(간질, B형간염 재활성화) 미확인시 사망 |
| **Lab** | 🔴 FATAL | 림프구감소(81%) → PCP 폐렴 → 사망. 혈소판감소(27%) → 뇌출혈 → 사망. Weekly CBC 필수 |
| **Radiology** | 🔴 FATAL | **유사진행(pseudoprogression) vs 진짜 진행**: MRI 없이 구별 불가. 오판시 효과있는 치료 중단 → 조기 사망 |
| **Clinical** | 🔴 FATAL | MGMT 메틸화 → TMZ 반응 예측 + 유사진행 확률. IDH 변이 → 예후/치료 결정 |
| **Persona** | 🔴 FATAL | **뇌종양이 인지기능을 직접 손상** → 환자가 자기 증상을 인지/보고할 수 없음. 실어증, 기억력 저하, 판단력 상실 = 신경학적 커뮤니케이션 장벽 |
| **Video** | 🟠 IRREVERSIBLE | 신경학적 평가: 언어유창성↓, 안면비대칭, 사지위약, 의식변화, 경련 징후, 스테로이드 부작용(moon face, 기분변화) |

**6개 모달리티 중 5개가 🔴 FATAL**

**GBM의 독보적 특성: "환자의 뇌 자체가 손상된 상태에서 커뮤니케이션해야 한다"**

```
다른 암 환자의 커뮤니케이션 장벽:
- Stoic: "안 아파요" (심리적 → 전략으로 극복 가능)
- Shame: "말하기 부끄러워요" (심리적 → 정상화로 극복 가능)
- Confused: "잘 모르겠어요" (교육적 → 설명으로 극복 가능)

GBM 환자의 커뮤니케이션 장벽:
- Aphasia: 말 자체를 못함 (신경학적 → 극복 불가, 적응 필요)
- Anosognosia: 자기 장애를 인식 못함 (신경학적 → 환자가 "괜찮다"고 진심으로 믿음)
- Memory: 어제 증상을 오늘 기억 못함 (신경학적)
- Executive dysfunction: 약 복용 순서를 잊음 (신경학적)
- Personality change: 별개의 사람이 됨 (보호자만 감지 가능)

→ Care AI가 "환자의 신경학적 상태 변화"를 영상통화에서 추적해야 함
→ 이것은 다른 어떤 암에서도 없는 고유한 도전
```

**데이터 가용성:**
- TCGA-GBM: ~600명, Brain MRI + WGS/WES + RNA-seq + clinical (TCIA + GDC)
- UPenn-GBM: 630명, 멀티파라메트릭 MRI + clinical + genomic + radiomics
- RHUH-GBM: 40명, 3시점 MRI (수술전/직후/재발시)
- BraTS Challenge: 수천명 뇌종양 MRI + 세그먼테이션
- Stupp Trial (NEJM 2005): TMZ 유효성/안전성 전체 데이터 공개

**데모 시나리오:**
```
환자: 58세 남성, GBM, 좌측 두정엽, MGMT methylated
치료: Stupp protocol (RT 60Gy + TMZ 75mg/m² 동시 → TMZ 유지 6cycle)

Week 3: Lab: 림프구 800→350 (급감) → Care AI: PCP 예방약 시작 권고
Week 4: 영상통화: "어제... 그... 뭐였지..." 단어찾기 어려움 증가
         → Care AI: baseline 대비 언어유창성 저하 감지 (MedGemma 음성분석)
Week 6: MRI: 수술 부위 주변 새로운 조영증강
         → 종양전문의: "진행인가? TMZ 중단할까?"
         → MedGemma MRI 분석: MGMT+ 환자 + 조영증강 패턴 
           → 82% 확률 유사진행 (치료 효과!)
         → Care AI: "림프구 180이지만 환자 상태 안정, 
                     언어기능 Week 3 대비 오히려 개선.
                     유사진행 가능성 높음. 치료 지속 권고."
         → TMZ 지속 → 3개월 후 MRI에서 종양 축소 확인 → 생존 연장

         만약 Care AI 없었다면:
         → MRI만 보고 "진행"으로 판단
         → TMZ 중단 → 2차 치료 (효과 낮음) → 6개월 사망
```

---

### 2. 유방암 AC-T (Doxorubicin/Cyclophosphamide → Taxane)

| 모달리티 | Criticality | 근거 |
|---------|-------------|------|
| **EMR** | 🟡 IMPORTANT | 동반질환(심질환)이 심독성 위험 변경 |
| **Lab** | 🔴 FATAL | 호중구감소증 → 발열성 위기 → 사망 (CheckMate 067과 동일) |
| **Radiology** | 🟡 IMPORTANT | 유방 MRI는 치료반응 평가용. AE 감지보다는 효과 판정 |
| **Clinical** | 🟠 IRREVERSIBLE | ER/PR/HER2가 치료 선택 결정하지만, AE 감지와는 간접적 관련 |
| **Persona** | 🟡 IMPORTANT | 탈모/불임 심리적 고통은 크지만 "사망"까지는 아님 |
| **Video** | 🟠 IRREVERSIBLE | 신경병증 기능평가 (비가역적 손상 예방), 구내염, 탈모 |

**🔴 FATAL: 1개 (Lab)**
**문제: Radiology가 "AE 감지"보다 "치료 반응 평가"에 치우침**

---

### 3. NSCLC + Pembrolizumab (비소세포폐암 + ICI)

| 모달리티 | Criticality | 근거 |
|---------|-------------|------|
| **EMR** | 🟡 IMPORTANT | 자가면역질환 병력이 irAE 위험 변경 |
| **Lab** | 🔴 FATAL | 간염(LFT), 갑상선(TFT), 신염(Cr) → 무증상 진행 → 장기부전 |
| **Radiology** | 🔴 FATAL | 면역매개 폐렴 40% 무증상 → CT에서만 발견 → 미발견시 사망 |
| **Clinical** | 🟡 IMPORTANT | PD-L1, TMB → 치료 선택. AE 감지와 간접적 |
| **Persona** | 🟠 IRREVERSIBLE | 고령 남성, COPD 동반 → 기침이 "원래 그런 건지 폐렴인지" 구별 불가 |
| **Video** | 🟡 IMPORTANT | 피부발진(시각적), 호흡곤란(관찰), 기침 → 그러나 핵심 irAE는 비시각적 |

**🔴 FATAL: 2개 (Lab, Radiology)**
**기존 nivolumab 작업과 거의 동일한 프로파일 → 추가 가치 제한적**

---

### 4. 다발성골수종 + VRd (Bortezomib/Lenalidomide/Dex)

| 모달리티 | Criticality | 근거 |
|---------|-------------|------|
| **EMR** | 🔴 FATAL | 신기능, 감염력, 골절력 → 치료 용량/선택 직접 결정 |
| **Lab** | 🔴 FATAL | M-protein, FLC, CBC, Ca, Cr, LDH → Lab이 치료의 "눈". 변화가 곧 진행/반응 |
| **Radiology** | 🔴 FATAL | PET/CT → 골수외 질환. 전신 MRI/저선량 CT → 새 골절(척추 압박골절 → 마비!) |
| **Clinical** | 🔴 FATAL | del(17p), t(4;14) → 고위험 분류 → 치료 강화 필수. MRD → 치료 중단 결정 |
| **Persona** | 🟠 IRREVERSIBLE | 고령(중위 69), 골통증 "나이 탓", 피로 "원래 그래", VZV 대상포진 숨김 |
| **Video** | 🟠 IRREVERSIBLE | 대상포진(bortezomib → VZV 재활성화, VISIBLE!), 부종, 신경병증 기능검사 |

**🔴 FATAL: 4개 (EMR, Lab, Radiology, Clinical)**
**Lab이 가장 밀도 높음. 그러나 Video/Persona의 "독보적 특성"은 약함**
**데이터: MMRF CoMMpass (1,143명, 8년 추적, WGS+RNA-seq+clinical) → 최고 수준**
**약점: 영상 데이터가 CoMMpass에 포함되지 않음 (별도 수집 필요)**

---

### 5. mCRC + FOLFOX + Bevacizumab (전이성 대장암)

| 모달리티 | Criticality | 근거 |
|---------|-------------|------|
| **EMR** | 🟠 IRREVERSIBLE | 수술력, 장루 유무, 동반질환 |
| **Lab** | 🔴 FATAL | CEA 추이, CBC(호중구감소), LFT(간독성), 소변단백(bev 신독성), 응고(출혈) |
| **Radiology** | 🔴 FATAL | CT q8-12w RECIST → 간전이 반응 평가. 장천공(bev) → 응급 CT |
| **Clinical** | 🟠 IRREVERSIBLE | KRAS/NRAS/BRAF, MSI → anti-EGFR 사용 가능 여부 결정 |
| **Persona** | 🟡 IMPORTANT | 장루 관리 수치심, 설사 수치심 |
| **Video** | 🟠 IRREVERSIBLE | 손발증후군(oxaliplatin), 신경병증, 상처치유 문제(bev) |

**🔴 FATAL: 2개 (Lab, Radiology)**

---

## 종합 Criticality 비교

| 모달리티 | GBM+TMZ | 유방암 AC-T | NSCLC+Pembro | MM+VRd | mCRC+FOLFOX |
|---------|---------|-----------|-------------|--------|-------------|
| EMR | 🔴 | 🟡 | 🟡 | 🔴 | 🟠 |
| Lab | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 |
| Radiology | 🔴 | 🟡 | 🔴 | 🔴 | 🔴 |
| Clinical | 🔴 | 🟠 | 🟡 | 🔴 | 🟠 |
| Persona | 🔴 | 🟡 | 🟠 | 🟠 | 🟡 |
| Video | 🟠 | 🟠 | 🟡 | 🟠 | 🟠 |
| **🔴 개수** | **5** | **1** | **2** | **4** | **2** |
| **고유 특성** | 뇌손상→소통불가 | 탈모/불임 심리 | irAE 패턴 | Lab 밀도 최고 | 균형적 |

---

## 🏆 최종 평가

### 1위: GBM + TMZ/RT — "뇌가 손상된 환자와 소통하기"

**모든 모달리티가 FATAL인 유일한 후보** (5/6 🔴)

고유한 가치:
1. **Pseudoprogression 판별** = MedGemma 영상 분석의 최고 난이도 과제
   - MGMT+ 환자의 30-50%에서 발생
   - MRI 상 종양 진행과 구별 불가 → AI가 임상+영상+lab 통합해야 판별
   - 오판 = 효과있는 치료 중단 = 조기 사망
   
2. **신경학적 커뮤니케이션 장벽** = 기존 4-barrier 모델의 극한 버전
   - 심리적 장벽이 아닌 신경학적 장벽
   - 환자가 "괜찮다"고 진심으로 믿음 (anosognosia)
   - Care AI가 영상통화에서 미세한 신경학적 변화를 추적해야 함
   
3. **Lab-Video-Imaging 트리플 위기** 시나리오
   - Lab: 림프구 180 (PCP 위험) → 무증상
   - Video: 단어찾기 어려움 증가 → 종양 진행? 스테로이드 부작용? 피로?
   - MRI: 새로운 조영증강 → 유사진행? 진짜 진행?
   - **3개를 동시에 해석해야만 정답에 도달**

4. **데이터 최고 수준**
   - TCGA-GBM + UPenn-GBM: 1,200명+ Brain MRI + 유전체 + 임상
   - BraTS: 뇌종양 세그먼테이션 표준 데이터셋
   - Stupp Trial: 안전성 프로파일 완전 공개
   - MGMT 예측 AI 연구 다수 → MedGemma 활용 증거

5. **심사위원 임팩트**
   - "AI가 MRI에서 pseudoprogression을 판별해서 치료를 지속시켰고, 
      동시에 영상통화에서 환자의 미세한 인지변화를 감지해서 
      생명을 위협하는 감염 위험을 사전에 차단했다"
   - 이 스토리는 AC-T의 "탈모를 발견했다"보다 압도적으로 강력

### 2위: MM + VRd — "가장 Lab-밀도 높은 암"

4/6 🔴이지만, Video/Persona의 독보성이 GBM에 미치지 못함.
MMRF CoMMpass 데이터는 최고 수준이나 영상 데이터 부재.

### 기존 Nivolumab과의 관계

```
Nivolumab (현재): ICI → irAE → Lab+영상 기반 감지
GBM + TMZ (제안): 뇌종양 + 항암 → 신경학적 소통장벽 + 유사진행 판별

이 둘은 완전히 다른 차원의 도전:
- Nivolumab: "환자가 말하지 않는 증상을 찾아내는" 문제
- GBM: "환자가 말할 수 없는 상태에서 증상을 찾아내는" 문제

→ 둘 다 하면 Care AI의 범용성을 극적으로 증명
→ 하나만 하면 GBM이 더 강력
```

---

## 제안: 피벗 or 추가?

**Option A: GBM으로 메인 피벗**
- Pro: 모든 모달리티 maximized, 스토리 최강, 데이터 풍부
- Con: 2주 안에 새 약물 파라미터 구축 필요

**Option B: Nivolumab 유지 + GBM을 2차 시나리오**
- Pro: 기존 작업 보존, 범용성 증명
- Con: 2개를 모두 완성할 시간 부족 가능

**Option C: GBM으로 피벗하되, God Engine 구조는 재활용**
- Pro: 아키텍처는 동일 (약물 파라미터만 교체), 스토리 극대화
- Con: 피부 시각적 AE (rash) 데모가 사라짐

**추천: Option C**
이유: God Engine의 4-barrier 모델 → GBM의 신경학적 5th barrier로 확장
      MedGemma의 brain MRI 분석이 피부 rash 분석보다 심사위원에게 더 강력
      "rash를 발견하는 AI"보다 "pseudoprogression을 판별하는 AI"가 
      $100K 상금의 가치에 더 부합
