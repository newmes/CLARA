## 1. 입력 데이터

### 1.1 CRF 도메인 (13개 + InvestigatorInfo)

| 도메인 | 클래스 | MedWatch 매핑 | 중요도 |
|--------|--------|---------------|--------|
| **DM** (인구통계) | `DMDomain` | A1 (SUBJID), A2 (AGE/BRTHDAT), A3 (SEX), A5 (RACE/ETHNIC) | ★★★ |
| **AE** (이상반응) | `AEDomain` | B2 (중대성), B3 (발생일), G7 (AETERM), + AETOXGR (CTCAE Grade), 입원일 | ★★★ |
| **EC** (시험약 투약) | `ECDomain` (list) | C2 (용량/빈도/경로), C3 (투약 시작/종료일) | ★★★ |
| **LB** (검사실 결과) | `LBDomain` | B5 (Narrative), B6 (Lab 텍스트) | ★★★ |
| **CM** (병용약) | `CMDomain` | C9 (baseline만), B5 (AE 치료약은 narrative에) | ★★☆ |
| **MH** (과거병력) | `MHDomain` | B5, B7 | ★★☆ |
| **VS** (활력징후) | `VSDomain` | A4 (체중), B5 | ★★☆ |
| **DD** (사망 상세) | `DDDomain` | B2 (사망일) | ★★★ (사망 시) |
| **DA** (약물 관리) | `DADomain` | C1 (로트번호), C6 (유효기한) | ★☆☆ |
| **Imaging** (영상검사) | `ImagingDomain` | B5 (Narrative — HRCT/CXR 소견) | ★★★ (ILD/감염 시) |
| **PFT** (폐기능검사) | `PFTDomain` | B5 (Narrative — DLCO/FVC 변화) | ★★★ (ILD 시) |
| **MB** (미생물검사) | `MBDomain` | B5 (Narrative — 배양/PCR 결과) | ★★☆ (감염 감별 시) |
| **Consult** (전문의 협진) | `ConsultDomain` | B5 (Narrative — 협진 소견) | ★★☆ |
| **Investigator** | `InvestigatorInfo` | E1~E3 (보고자 정보) | ★★☆ |

### 1.2 외부 참조 데이터

| 소스 | Doc Agent 용도 |
|------|---------------|
| AACT | 발생률, cross-trial 비교 (DESTINY 트라이얼) |
| FAERS | Onset time, 시판 후 사망률, RAG 코퍼스 |
| Drug Label | BBW 텍스트, Grade별 management, dose modification |
| ICH 가이드라인 | E2A, E2F, E3 — 문서 구조/규제 요건 |
| MedDRA | AE term → PT/SOC 코딩 |

### 1.3 Sentinel Agent 출력 (ILD 시)

```python
class SentinelOutput(BaseModel):
    ild_detected: bool
    ild_grade: Optional[int]        # CTCAE Grade 1-5
    cxr_findings: str               # 흉부 영상 소견
    differential_diagnosis: str     # 감별진단
    kl6_value: Optional[float]
    spo2_value: Optional[float]
```

---

## 2. 출력 문서

| 문서 | 형식 | 규제 기한 | 규제 근거 |
|------|------|---------|----------|
| MedWatch 3500A | JSON + PDF | 치명적/생명위협 7일, 기타 SAE 15일 | 21 CFR 312.32 |
| E2B(R3) XML | XML | MedWatch 확정 후 | ICH E2B(R3) |