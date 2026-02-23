# Care AI Nurse API

MedGemma 기반 Nurse Agent API. 환자 영상의 SigLIP 벡터와 음성 텍스트(STT)를 받아 간호사 AI 응답 텍스트를 반환합니다.

## 서버 정보

| 항목 | 값 |
|------|-----|
| Base URL | `http://localhost:8300` |
| GPU | 7번 |
| 모델 | MedGemma 4B (google/medgemma-4b-it) |
| 지원 약물 | 8종 (Padcev+Pembro, Etoposide+Cisplatin 등) |

---

## 엔드포인트

### 1. Health Check

```
GET /v1/health
```

```python
import requests

resp = requests.get("http://localhost:8300/v1/health")
print(resp.json())
```

응답 예시:
```json
{
    "status": "ok",
    "classifier_loaded": true,
    "nurse_loaded": true,
    "tts_available": false,
    "loaded_drugs": [
        "Padcev + Pembrolizumab",
        "Etoposide + Cisplatin",
        "Carboplatin + Etoposide",
        ...
    ]
}
```

---

### 2. Consult (메인 API)

```
POST /v1/consult
```

#### Request

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `siglip_vector` | `list[float]` | **필수** | MedSigLIP 인코더로 추출한 1152차원 임베딩 벡터 |
| `patient_text` | `str` | **필수** | STT로 변환된 환자 음성 텍스트 |
| `drug_name` | `str` | 선택 | 약물명. 미전달 시 기본값 "Padcev + Pembrolizumab" 사용 |
| `indication` | `str` | 선택 | 적응증. 미전달 시 drug_name에 매칭된 기본값 사용 |
| `skip_tts` | `bool` | 선택 | `true`면 TTS 생략 (기본값: `false`) |

#### Python 사용 예시

```python
import requests
import json

# 앱에서 받은 SigLIP 벡터 (1152차원)
siglip_vector = [...]  # MedSigLIP 인코더 출력

# 앱에서 STT로 변환한 환자 발화
patient_text = "오늘은 좀 괜찮은데, 손발이 좀 저려요. 그리고 밥맛이 없어요."

resp = requests.post(
    "http://localhost:8300/v1/consult",
    json={
        "siglip_vector": siglip_vector,
        "patient_text": patient_text,
        "drug_name": "Padcev + Pembrolizumab",  # 선택사항
        "skip_tts": True,
    },
)

data = resp.json()

# 간호사 응답 텍스트 (TTS용)
print(data["nurse_text"])

# 구조화된 응답 (질문 목록, AE 의심 등)
print(json.dumps(data["nurse_structured"], indent=2, ensure_ascii=False))

# 시각 분석 결과 (SigLIP)
print(json.dumps(data["visual_assessment"], indent=2, ensure_ascii=False))

# 레이턴시
print(data["latency_ms"])
```

#### Response

| 필드 | 타입 | 설명 |
|------|------|------|
| `nurse_text` | `str` | 간호사 응답 (자연어, TTS/화면 표시용) |
| `nurse_structured` | `dict` | 구조화된 응답 (아래 상세) |
| `visual_assessment` | `dict` | SigLIP 시각 분석 결과 |
| `audio_base64` | `str \| null` | TTS 오디오 (base64 MP3). TTS 미사용 시 null |
| `latency_ms` | `dict` | 레이턴시 분석 (siglip_ms, nurse_ms, tts_ms, total_ms) |

#### `nurse_structured` 구조

```json
{
    "approach_style": "empathetic",
    "acknowledgment": "피곤하시고 속이 안 좋으시다니 안타깝네요.",
    "questions": [
        {
            "question": "식욕에 변화가 있으신가요?",
            "target_ae": "decreased_appetite",
            "rationale": "이 약물의 흔한 부작용인 식욕감소를 확인"
        },
        {
            "question": "손발에 저림이나 화끈거림이 있나요?",
            "target_ae": "peripheral_neuropathy",
            "rationale": "Padcev의 주요 부작용인 말초신경병증 선별"
        }
    ],
    "visual_followup": "영상에서 경미한 피부 변화가 감지되었습니다.",
    "preliminary_concerns": ["Fatigue", "Nausea", "Peripheral neuropathy"]
}
```

#### `visual_assessment` 구조

```json
{
    "findings": [
        {
            "ae_term": "rash_maculopapular",
            "estimated_grade": 2,
            "confidence": 0.85,
            "description": "Visual analysis detected rash maculopapular (grade 2)"
        }
    ],
    "general_observations": ["rash maculopapular g2 (85.0%)"],
    "raw_prediction": {
        "prediction": "rash_maculopapular_g2",
        "probability": 0.85,
        "top_k": {"rash_maculopapular_g2": 0.85, "normal": 0.08, "rash_acneiform_g1": 0.03},
        "ae_term": "rash_maculopapular",
        "grade": 2
    }
}
```

---

## 지원 약물 목록

| 약물 | 적응증 |
|------|--------|
| Padcev + Pembrolizumab | Metastatic urothelial carcinoma |
| Etoposide + Cisplatin | Small cell lung cancer |
| Darbepoetin alfa | SCLC (anemia) |
| Paclitaxel + Cisplatin + Etoposide | NSCLC |
| Carboplatin + Etoposide | SCLC |
| Paclitaxel + Carboplatin + Bevacizumab | NSCLC |
| Paclitaxel + Carboplatin | NSCLC |
| Gemcitabine + Cisplatin | Bladder cancer |

`drug_name`을 전달하지 않으면 **Padcev + Pembrolizumab**이 기본 사용됩니다.

---

## SigLIP 벡터 생성 (앱 측 참고)

앱에서 MedSigLIP 인코더(`google/medsiglip-448`)로 이미지를 처리한 후 1152차원 벡터를 추출합니다.

```python
# 앱 측 코드 (참고용 — 서버에서는 벡터만 받음)
from transformers import AutoModel, AutoImageProcessor
from PIL import Image
import torch

encoder = AutoModel.from_pretrained("google/medsiglip-448")
processor = AutoImageProcessor.from_pretrained("google/medsiglip-448")

img = Image.open("patient_frame.png").convert("RGB").resize((448, 448))
inputs = processor(images=img, return_tensors="pt")

with torch.inference_mode():
    vision_out = encoder.vision_model(pixel_values=inputs["pixel_values"])
    vector = vision_out.pooler_output[0].tolist()  # 1152-dim list

# 이 vector를 API의 siglip_vector로 전달
```

---

## 서버 시작/중지

```bash
# 시작 (GPU 7, port 8300)
cd /data2/workspace/ClinicalTrialEngine
CARE_AI_GPU=7 nohup .venv/bin/python -m api.server > logs/api_server.log 2>&1 &

# 로그 확인
tail -f logs/api_server.log

# 중지
kill $(pgrep -f "api.server")
```

### 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `CARE_AI_GPU` | `7` | 사용할 GPU ID |
| `MEDGEMMA_MODEL` | `google/medgemma-4b-it` | MedGemma 모델 경로 |
| `MEDGEMMA_ADAPTER` | (없음) | LoRA 어댑터 경로 (fine-tuned 모델 사용 시) |
| `SIGLIP_HEAD` | `api/models/siglip_head.pt` | SigLIP 분류 head 경로 |

---

## 에러 처리

| Status Code | 의미 |
|-------------|------|
| 200 | 정상 응답 |
| 422 | 요청 형식 오류 (벡터 차원 불일치 등) |
| 503 | 모델 로딩 중 (서버 시작 직후 ~90초간) |

```python
resp = requests.post("http://localhost:8300/v1/consult", json=payload)
if resp.status_code == 200:
    data = resp.json()
    print(data["nurse_text"])
elif resp.status_code == 422:
    print("요청 형식 오류:", resp.json()["detail"])
elif resp.status_code == 503:
    print("모델 로딩 중, 잠시 후 재시도")
```

---

## 성능

| 단계 | 레이턴시 |
|------|----------|
| SigLIP 분류 | ~4ms |
| MedGemma 응답 | ~10-17s |
| TTS (설치 시) | ~1-2s |
| **총합** | **~10-17s** |
