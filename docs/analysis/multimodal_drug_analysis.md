# 멀티모달 데이터 활용 약물 시뮬레이션 후보 분석

## 요구 모달리티 매트릭스

| 모달리티 | 시뮬레이션 역할 | 공개 데이터 요건 |
|---------|---------------|----------------|
| **EMR** | 환자 히스토리, 동반질환, 약물 이력 | 환자 수준 구조화 데이터 |
| **Lab** | 혈액검사 추이, 이상 탐지 | 종단적 lab values (CBC, LFT, etc.) |
| **Radiology** | 영상 기반 AE/반응 평가 | DICOM 이미지 + 판독문 |
| **Clinical Info** | 바이오마커, 병기, 예후 | 분자진단, 유전체 데이터 |
| **Persona** | 환자 커뮤니케이션 장벽 모델링 | 인구통계, 사회경제적 맥락 |
| **Video Call** | 육안/기능 평가 가능 증상 | 시각적 AE 빈도 데이터 |

---

## 후보 3개 비교

### 🏆 후보 1: 유방암 + AC-T (Doxorubicin/Cyclophosphamide → Taxane)
**핵심 데이터: I-SPY 1/2 Trial (TCIA)**

| 모달리티 | 데이터 소스 | 내용 | 강도 |
|---------|-----------|------|------|
| EMR | I-SPY clinical data | 인구통계, 동반질환, 투약 이력 | ⭐⭐⭐⭐ |
| Lab | I-SPY + 논문 | ANC, WBC, PLT, LFT, Cr, LVEF(심초음파) | ⭐⭐⭐⭐⭐ |
| Radiology | TCIA I-SPY2 | **985명 유방 MRI, 4개 시점 연속 촬영** | ⭐⭐⭐⭐⭐ |
| Clinical | I-SPY + GEO | ER/PR/HER2, MammaPrint 70-gene, pCR | ⭐⭐⭐⭐⭐ |
| Persona | 인구통계 기반 | 젊은 여성, 불임 우려, 체형 변화, 탈모 심리 | ⭐⭐⭐⭐⭐ |
| Video | AE 프로파일 | 탈모, 구내염, 손발증후군, 피부변색, 네일변화 | ⭐⭐⭐⭐⭐ |

**AE 프로파일 (AC-T 레지멘):**

| AE | 빈도 | 시각적 | Lab | 영상 | 환자 보고 장벽 |
|----|------|-------|-----|------|--------------|
| 호중구감소증 | 70-80% G3-4 | ❌ | ✅ ANC | ❌ | 무증상 → 발열성 위기 |
| 심독성 (LVEF↓) | 5-26% (누적용량) | ❌ | ✅ troponin | ✅ ECHO/MRI | 초기 무증상 |
| 탈모 | 95-100% | ✅ 영상통화 | ❌ | ❌ | 수치심, 정체성 위기 |
| 구내염/점막염 | 40-60% | ✅ 입 벌려보기 | ❌ | ❌ | 식사 회피 숨김 |
| 손발증후군 | 20-40% (taxane) | ✅ 손/발 보여주기 | ❌ | ❌ | "별거 아님" 축소 |
| 말초신경병증 | 30-60% (taxane) | ✅ 기능검사 | ❌ | ❌ | 점진적 → 비가역적! |
| 오심/구토 | 60-80% | ❌ | ❌ | ❌ | 치료 포기 원인 1위 |
| 설사 | 20-40% | ❌ | ❌ | ❌ | 수치심 |
| 빈혈 | 50-70% | ✅ 창백함 | ✅ Hgb | ❌ | "피곤한 거" 무시 |
| 무월경/불임 | 40-70% (<40세) | ❌ | ✅ FSH/E2 | ❌ | 질문 안 하면 절대 말 안 함 |

**왜 AC-T가 최적인가:**

1. **모든 모달리티가 "의미있게" 사용됨**
   - Lab: 호중구감소증(생명위협) + 심독성 바이오마커(troponin, BNP)
   - Imaging: 유방 MRI(치료 반응) + ECHO(심기능) + CXR(폐독성)
   - Video: 탈모, 구내염, 손발증후군, 신경병증 기능검사, 빈혈 시각 징후
   - EMR: 동반질환이 AE 위험도 변경 (기존 심질환 → 심독성 고위험)

2. **페르소나 다양성이 극대화됨**
   - 30대 미혼 여성: 탈모 + 불임 → 정서적 붕괴, 치료 거부 위험
   - 50대 직장인: "아무도 모르게" → 증상 숨김
   - 70대 독거 노인: 인지기능 저하 + 복약 순응도 문제

3. **"일찍 발견하면 살린다" 스토리가 2개**
   - 호중구감소증: 동일한 neutropenia 사례 (이미 분석함)
   - 심독성: 누적용량 450mg/m² 넘으면 비가역적 → 조기 troponin 상승 감지

4. **실제 공개 데이터가 가장 풍부**
   - TCIA I-SPY 1: 222명, 847 MRI studies + clinical outcomes
   - TCIA I-SPY 2: 985명, 4시점 MRI + pCR + 바이오마커
   - Project Data Sphere: 유방암 phase III 다수 포함
   - CT-ADE (HuggingFace): doxorubicin, paclitaxel, cyclophosphamide 모두 포함

---

### 후보 2: NSCLC + Pembrolizumab (ICI)

| 모달리티 | 데이터 소스 | 내용 | 강도 |
|---------|-----------|------|------|
| EMR | MIMIC-IV (간접) | 폐암 입원환자 discharge summary | ⭐⭐⭐ |
| Lab | KEYNOTE 논문 | CBC, LFT, TFT (nivolumab과 유사) | ⭐⭐⭐⭐ |
| Radiology | TCIA NSCLC | **422명 CT + PET/CT + 종양 세그먼테이션** | ⭐⭐⭐⭐⭐ |
| Clinical | TCIA + GEO | 유전체, 방사선학적 특징, 생존 데이터 | ⭐⭐⭐⭐ |
| Persona | 인구통계 기반 | 고령 남성, 흡연력, COPD 동반 | ⭐⭐⭐⭐ |
| Video | AE 프로파일 | 피부 발진, 기침(폐렴 감별!), 호흡곤란 | ⭐⭐⭐ |

**강점:** 기존 nivolumab 작업 재활용, 폐렴 vs 기침 감별이 극적
**약점:** 영상통화에서 시각적으로 보이는 AE가 상대적으로 적음
         Lab/Imaging 데이터가 직접 연결되지 않음 (별도 소스)

---

### 후보 3: mCRPC + Docetaxel (Project Data Sphere)

| 모달리티 | 데이터 소스 | 내용 | 강도 |
|---------|-----------|------|------|
| EMR | PDS | 9,000+ 환자 수준 데이터 | ⭐⭐⭐⭐⭐ |
| Lab | PDS | PSA, CBC, LFT, 종합 lab | ⭐⭐⭐⭐⭐ |
| Radiology | 제한적 | PDS에 영상 없음 | ⭐⭐ |
| Clinical | PDS | Gleason, PSA kinetics, prior tx | ⭐⭐⭐⭐ |
| Persona | 인구통계 기반 | 고령 남성, 비뇨생식기 증상 수치심 | ⭐⭐⭐⭐ |
| Video | AE 프로파일 | 부종, 탈모, 피부변화, 신경병증 | ⭐⭐⭐ |

**강점:** PDS의 환자 수준 데이터가 가장 상세 (실제 Lab 시계열)
**약점:** 영상 데이터 부재, Video call 시나리오가 약함

---

## 종합 비교

| 기준 | 유방암 AC-T | NSCLC Pembro | mCRPC Doce |
|------|-----------|-------------|------------|
| EMR 데이터 풍부도 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Lab 시계열 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Radiology 이미지 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| Clinical/Genomic | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| Persona 다양성 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| Video Call 활용도 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| MedGemma 멀티모달 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| "살릴 수 있었다" 스토리 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **합계** | **39** | **32** | **29** |

---

## 🏆 최종 추천: 유방암 AC-T + I-SPY 데이터

### 데모 시나리오 구성

```
환자: 38세 여성, Stage IIB 유방암, TNBC
치료: AC (doxorubicin 60 + cyclophosphamide 600) x4 → T (paclitaxel weekly x12)

=== God Engine이 생성하는 AE 타임라인 ===

Week 1-2:  오심/구토 Grade 2 (환자 보고 가능)
Week 2-3:  탈모 시작 (영상통화에서 보임 → 정서적 위기)
Week 3:    ANC 1,200 (Grade 2) → Lab only, 무증상
Week 4:    구내염 Grade 1 (입안 궤양 → 영상통화에서 확인 가능)
Week 6:    ANC 400 (Grade 4) → 위험! 환자 무증상
Week 7:    미열 37.8°C → Care AI가 영상통화에서 포착
           → 즉시 Lab → febrile neutropenia 진단 → 생존
Week 8:    Lab: Troponin I 미세 상승 (0.04 → 0.08)
           → 일반 간호사: "정상 범위" 
           → Care AI: "AC 2cycle 후 상승 추세, ECHO 권고"
Week 12:   AC 완료 → Taxane 시작
Week 14:   손끝 저림 시작 (Grade 1 신경병증)
           → 영상통화: "손가락 모아보세요" 기능검사
Week 18:   MRI: 종양 82% 축소 (TCIA 실제 데이터로 시연)
Week 22:   신경병증 Grade 2 → 환자: "별거 아닌데요"
           → Care AI: baseline 비교 → 용량 감량 권고
           → 비가역적 손상 예방

=== MedGemma 활용 포인트 ===

1. 유방 MRI 분석: 종양 반응 평가 (FTV 변화)
2. 구내염 시각 분석: 입안 사진/영상에서 Grade 평가
3. 탈모 진행 모니터링: 영상통화 프레임에서 변화 추적
4. Lab 트렌드 해석: ANC 하강 패턴, troponin 추세
5. 심초음파(ECHO) 분석: LVEF 변화 감지
6. 신경병증 기능검사: 영상으로 손가락 움직임 평가
```

### 공개 데이터 소스 연결

```
시뮬레이션 계층     실제 데이터 소스
─────────────     ──────────────
AE 빈도/Grade  ←  CT-ADE (HuggingFace): doxorubicin, paclitaxel
               ←  NSABP/CALGB/ECOG Phase III 논문 (공개)
Lab 시계열     ←  I-SPY inclusion criteria + monitoring protocol
               ←  ANNOUNCE trial (doxorubicin 심독성 상세)
유방 MRI      ←  TCIA I-SPY 1 (222명) + I-SPY 2 (985명)
               ←  4시점 연속 MRI (T0, T1, T2, T3)
Clinical/Bio  ←  I-SPY: ER/PR/HER2, MammaPrint, pCR outcomes
               ←  TCIA: 유전체 데이터 (GEO)
환자 인구통계  ←  I-SPY: 연령, 인종, 병기 분포
페르소나 모델  ←  문헌 기반: 유방암 환자 심리/커뮤니케이션 연구
```

### nivolumab 작업과의 관계

```
현재 구축 완료:                 유방암 AC-T로 확장:
──────────────                ─────────────────
God Engine 구조       →  동일 구조, 약물 파라미터만 교체
4-barrier 페르소나    →  유방암 특화 장벽 추가 (탈모수치심, 불임공포)
disclosure 확률 모델  →  동일 모델, 수치심 토픽 재정의
video call 시뮬레이션 →  시각적 AE 종류 확장
Lab monitoring       →  CBC + 심장 바이오마커 추가
영상 분석            →  [NEW] 유방 MRI + ECHO
```
