# Multimodal AE Detection — 코드 모듈 개발 (Hyena)

**작성일**: 2026-02-17  
**마감**: 2026-02-19 (목)  
**목표**: Game UI 영상통화 화면에 연결할 수 있는 **Python 모듈** 완성

---

## 최종 목표 (닉이 붙일 구조)

```
게임 화면 (영상통화 UI)
┌──────────────────────────┐
│  [환자 얼굴 사진]          │  ← 합성된 얼굴 (AE 반영)
│  🔴 LIVE                  │
│                          │
│  🔊 "좀 가렵긴 한데..."    │  ← TTS 음성 + 기침 사운드
│                          │
│  ───── 분석 결과 ─────    │
│  📷 Rash detected G2     │  ← 이미지 분류 결과
│  🎤 Dry cough detected   │  ← 오디오 분석 결과
└──────────────────────────┘
```

**혜나가 만든 코드를 닉이 game UI의 영상통화 화면에 직접 붙임.**  
따라서 산출물 = **호출 가능한 Python 함수들** + 결과 JSON 포맷 정의.

---

## 코드 구조 (이대로 만들어 줘)

```
src/multimodal/
├── __init__.py
├── face_generator.py      ← 얼굴 이미지 생성
├── face_analyzer.py       ← 얼굴 이미지 → AE 분류
├── voice_generator.py     ← 환자 음성 생성 (TTS + 기침)
├── voice_analyzer.py      ← 음성 → STT + 기침 분류
├── schemas.py             ← 입출력 데이터 스키마 (dataclass)
└── config.py              ← API 키, 모델 설정
```

---

## Part A. 얼굴 (face_generator + face_analyzer)

### A-1. `face_generator.py` — 환자 얼굴 이미지 생성

```python
def generate_patient_face(
    patient_profile: dict,    # EMR에서 age, sex, race 등
    active_aes: list[dict],   # [{"ae": "maculopapular_rash", "grade": 2, ...}]
    day: int,
) -> FaceGenerationResult:
    """
    Gemini Imagen / Nano Banana 으로 환자 얼굴 합성.
    AE가 있으면 해당 AE가 반영된 얼굴, 없으면 정상 얼굴.

    Returns:
        FaceGenerationResult:
            image_bytes: bytes          # PNG 이미지
            prompt_used: str            # 생성에 사용된 프롬프트
            ae_applied: list[dict]      # 반영된 AE 목록
            metadata: dict              # 모델명, 생성 시간 등
    """
```

**핵심 요구사항:**
- [ ] AE type + CTCAE grade에 맞는 시각적 묘사 프롬프트 자동 생성
- [ ] 아래 CTCAE 기준표를 프롬프트에 정확히 반영

| AE Term | Grade 1 | Grade 2 | Grade 3+ |
|---------|---------|---------|----------|
| Maculopapular rash | 뺨에 경미한 붉은 반점 | 얼굴 넓게 붉은 반점+각질 | 얼굴 전체 심한 발진+부종 |
| Acneiform rash | 이마에 작은 구진 몇 개 | 볼/이마에 농포 다수 | 얼굴 전체 농포+이차감염 징후 |
| Periorbital edema | 눈 주위 약간 부어오름 | 눈 주위 뚜렷한 부종 | 눈 뜨기 어려울 정도 부종 |
| SJS 전구증상 | 입술 발적 | 입술/점막 수포 | 표피 박리 시작 |

- [ ] 같은 환자는 일관된 base face 유지 (seed/reference 활용)
- [ ] 다양한 피부톤 지원 (EMR의 race 필드 참고)

### A-2. `face_analyzer.py` — 얼굴 AE 분류

```python
def analyze_face(
    image_bytes: bytes,
    method: str = "medgemma",  # "medgemma" | "medsiglip"
) -> FaceAnalysisResult:
    """
    환자 얼굴 사진 → AE 감지 + CTCAE grade 분류.

    Returns:
        FaceAnalysisResult:
            detected_aes: list[DetectedAE]  # [{ae_term, grade, confidence, reasoning}]
            model_used: str
            latency_ms: float
    """
```

**두 가지 방식 모두 구현:**

- [ ] **MedGemma (프롬프트 기반)**
  - Gemini에 이미지 + 프롬프트 전달
  - 프롬프트: CTCAE v5.0 기준으로 AE 식별 + grade 판정
  - JSON 출력 파싱

- [ ] **MedSigLIP + Classification Head**
  - 이미지 → MedSigLIP embedding → classifier
  - classifier: AE type (4종) × grade (1-3) 분류
  - `train_classifier()` 함수도 만들어서 fine-tuning 가능하게

```python
def train_classifier(
    image_dir: str,          # 라벨링된 이미지 폴더
    output_path: str,        # 학습된 head 저장 경로
    epochs: int = 20,
) -> dict:  # {"accuracy": 0.85, "f1": 0.82, ...}
```

---

## Part B. 음성 (voice_generator + voice_analyzer)

### B-1. `voice_generator.py` — 환자 음성 생성

```python
def generate_patient_voice(
    text: str,                # 환자가 말할 대사
    patient_profile: dict,    # age, sex → 음색 결정
    cough_config: dict | None = None,  # {"type": "dry", "frequency": "occasional", "severity": "mild"}
) -> VoiceGenerationResult:
    """
    Gemini TTS로 환자 음성 합성. cough_config 있으면 기침 섞인 음성.

    Returns:
        VoiceGenerationResult:
            audio_bytes: bytes        # WAV/MP3
            duration_sec: float
            transcript: str           # 원문
            cough_inserted: bool
            metadata: dict
    """
```

**핵심 요구사항:**
- [ ] 환자 특성(나이/성별)에 맞는 음성 생성
- [ ] 기침 유형별 합성: dry / productive / wheezing
- [ ] 기침 빈도 조절: none / occasional / frequent / severe
- [ ] 대사 + 기침이 자연스럽게 섞인 오디오 생성

### B-2. `voice_analyzer.py` — 음성 분석 (STT + 기침 분류)

```python
def analyze_voice(
    audio_bytes: bytes,
    method: str = "gemini",  # "gemini" | "hear"
) -> VoiceAnalysisResult:
    """
    환자 음성 → 텍스트 변환 + 기침 감지/분류.

    Returns:
        VoiceAnalysisResult:
            transcript: str                  # STT 결과
            cough_events: list[CoughEvent]   # [{timestamp_sec, type, severity, confidence}]
            respiratory_assessment: dict     # {has_cough, has_wheeze, has_dyspnea, overall_severity}
            model_used: str
            latency_ms: float
    """
```

**두 가지 방식:**
- [ ] **Gemini audio understanding** — 프롬프트로 기침 분류
- [ ] **Google Hear** (또는 대안 오디오 모델) — 전용 audio classifier

---

## Part C. `schemas.py` — 공통 데이터 스키마

```python
from dataclasses import dataclass

@dataclass
class DetectedAE:
    ae_term: str           # "maculopapular_rash"
    grade: int             # 1-4
    confidence: float      # 0.0-1.0
    reasoning: str         # "Grade 2: 볼과 이마에 걸쳐 10-30% BSA 범위의 홍반성 발진"
    channel: str           # "visual" | "audio"

@dataclass
class CoughEvent:
    timestamp_sec: float
    cough_type: str        # "dry" | "productive" | "wheezing"
    severity: str          # "mild" | "moderate" | "severe"
    confidence: float

@dataclass
class FaceGenerationResult:
    image_bytes: bytes
    prompt_used: str
    ae_applied: list[dict]
    metadata: dict

@dataclass
class FaceAnalysisResult:
    detected_aes: list[DetectedAE]
    model_used: str
    latency_ms: float

@dataclass
class VoiceGenerationResult:
    audio_bytes: bytes
    duration_sec: float
    transcript: str
    cough_inserted: bool
    metadata: dict

@dataclass
class VoiceAnalysisResult:
    transcript: str
    cough_events: list[CoughEvent]
    respiratory_assessment: dict
    model_used: str
    latency_ms: float
```

**이 스키마가 닉이 Game UI에 연결하는 인터페이스.**  
**반드시 이 구조를 지켜주세요.**

---

## Game UI 연동 포인트 (참고용 — 닉이 할 부분)

```python
# 게임에서 이렇게 호출할 예정:

# 1) 매 Day, 환자 얼굴 생성
face_result = generate_patient_face(patient_profile, active_aes, day)
# → face_result.image_bytes를 프론트에 base64로 전달

# 2) 대화 시작 시, 환자 음성 생성
voice_result = generate_patient_voice(dialogue_text, patient_profile, cough_config)
# → voice_result.audio_bytes를 프론트에서 <audio> 재생

# 3) 플레이어가 "분석" 버튼 누르면
face_analysis = analyze_face(face_result.image_bytes)
voice_analysis = analyze_voice(voice_result.audio_bytes)
# → 결과를 프론트 사이드바에 표시
```

---

## 산출물 (2/19까지)

| # | 산출물 | 형식 | 용도 |
|---|--------|------|------|
| 1 | `src/multimodal/*.py` (6개 파일) | Python 모듈 | Game UI 직접 연동 |
| 2 | 테스트 이미지/오디오 샘플 | `data/multimodal/samples/` | 동작 확인용 |
| 3 | `test_multimodal.py` | pytest | 각 함수 정상동작 확인 |
| 4 | MedSigLIP classifier weights (가능하면) | `.pt` 파일 | face_analyzer에서 로드 |
| 5 | 성능 비교 리포트 | `docs/multimodal_benchmark.md` | MedGemma vs MedSigLIP, Gemini vs Hear |

---

## 일정

```
Day 1 (2/18 수):
  오전: schemas.py + config.py + face_generator.py + voice_generator.py
        → 생성 쪽 먼저. 샘플 데이터 몇 개씩 만들어서 data/multimodal/samples/에 저장
  오후: face_analyzer.py (MedGemma 먼저) + voice_analyzer.py (Gemini 먼저)
        → 분석 쪽. 오전에 만든 샘플로 바로 테스트

Day 2 (2/19 목):
  오전: face_analyzer.py (MedSigLIP + classifier head + train_classifier)
        voice_analyzer.py (Hear 추가)
  오후: test_multimodal.py 작성 + 성능 비교 + 리포트
        가능하면 fine-tuning까지
```

---

## 참고

- 모든 함수는 **동기(sync)**로 구현. 닉이 async wrapper 감쌀 예정
- API 키는 `config.py`에서 환경변수로 읽기 (`GOOGLE_API_KEY` 등)
- CTCAE v5.0 기준은 `docs/drug_profile.md` 참고
- 기존 Gemini 합성 실험 코드가 있으면 그대로 각 함수 안에 통합
- **함수 시그니처/리턴 스키마 바꾸지 말 것** — 닉이 이 인터페이스 기준으로 UI 작업 병행함
