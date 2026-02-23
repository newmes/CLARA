"""Care AI API Server — FastAPI

Endpoints:
    POST /v1/consult
        Input:  SigLIP 1152-dim vector + patient STT text + (optional) drug/patient info
        Output: Nurse AI text response + TTS audio (base64 MP3)

    GET /v1/health
        Health check

Startup:
    uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from api.siglip_classifier import SigLIPClassifier
from api.nurse_engine import NurseEngine
from api.tts_service import TTSService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("care-ai-api")

# ═══════════════════════════════════════════════════════════
# Config (environment variables with defaults)
# ═══════════════════════════════════════════════════════════

GPU_ID = int(os.getenv("CARE_AI_GPU", "7"))
MEDGEMMA_PATH = os.getenv("MEDGEMMA_MODEL", "google/medgemma-4b-it")
MEDGEMMA_ADAPTER = os.getenv("MEDGEMMA_ADAPTER", "")
MEDGEMMA_TOKENIZER = os.getenv("MEDGEMMA_TOKENIZER", "")
SIGLIP_HEAD = os.getenv("SIGLIP_HEAD", str(API_DIR / "models" / "siglip_head.pt"))

DEFAULT_RULE_SET = os.getenv(
    "DEFAULT_RULE_SET",
    str(PROJECT_ROOT / "data" / "rule_set_calibrated_ev302.json"),
)

# ═══════════════════════════════════════════════════════════
# Request / Response Schemas
# ═══════════════════════════════════════════════════════════

class ConsultRequest(BaseModel):
    siglip_vector: list[float] = Field(
        ...,
        description="1152-dim SigLIP embedding from the mobile app's MedSigLIP encoder",
        min_length=1152,
        max_length=1152,
    )
    patient_text: str = Field(
        ...,
        description="STT-transcribed patient speech",
    )
    drug_name: Optional[str] = Field(
        None,
        description="Drug name (e.g., 'Padcev + Pembrolizumab'). Falls back to default if not provided.",
    )
    indication: Optional[str] = Field(
        None,
        description="Indication (e.g., 'metastatic urothelial carcinoma'). Falls back to default.",
    )
    skip_tts: bool = Field(
        False,
        description="If true, skip TTS and return text only (faster).",
    )


class VisualFinding(BaseModel):
    ae_term: str | None
    estimated_grade: int | None
    confidence: float
    description: str


class ConsultResponse(BaseModel):
    nurse_text: str = Field(
        ...,
        description="Natural language nurse response (ready for TTS or display)",
    )
    nurse_structured: dict = Field(
        ...,
        description="Structured JSON nurse response (acknowledgment, questions, concerns)",
    )
    visual_assessment: dict = Field(
        ...,
        description="SigLIP visual analysis results",
    )
    audio_base64: str | None = Field(
        None,
        description="Base64-encoded MP3 audio of the nurse response (null if TTS unavailable or skipped)",
    )
    latency_ms: dict = Field(
        ...,
        description="Latency breakdown in milliseconds",
    )


# ═══════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════

app = FastAPI(
    title="Care AI — Nurse Agent API",
    version="0.1.0",
    description="MedGemma Nurse Agent with MedSigLIP visual assessment + Google TTS",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

classifier: SigLIPClassifier | None = None
nurse: NurseEngine | None = None
tts: TTSService | None = None


@app.on_event("startup")
async def startup():
    global classifier, nurse, tts

    log.info("Loading SigLIP classification head from %s", SIGLIP_HEAD)
    classifier = SigLIPClassifier(SIGLIP_HEAD, device="cpu")
    log.info("SigLIP head loaded.")

    log.info("Loading MedGemma nurse from %s (GPU %d)", MEDGEMMA_PATH, GPU_ID)
    nurse = NurseEngine(
        model_path=MEDGEMMA_PATH,
        gpu_id=GPU_ID,
        adapter_path=MEDGEMMA_ADAPTER or None,
        tokenizer_path=MEDGEMMA_TOKENIZER or None,
    )

    if Path(DEFAULT_RULE_SET).exists():
        ctx = nurse.load_drug_context(DEFAULT_RULE_SET)
        log.info("Default drug context loaded: %s", ctx["drug_name"])

    rule_sets_dir = PROJECT_ROOT / "data" / "new_drugs"
    if rule_sets_dir.exists():
        for rs_path in sorted(rule_sets_dir.glob("*/base.json")):
            try:
                ctx = nurse.load_drug_context(rs_path)
                log.info("  Loaded drug: %s", ctx["drug_name"])
            except Exception as e:
                log.warning("  Failed to load %s: %s", rs_path, e)

    log.info("MedGemma nurse ready.")

    log.info("Initializing TTS service...")
    tts = TTSService()
    if tts.available:
        log.info("TTS ready (Google Cloud).")
    else:
        log.warning("TTS unavailable — will return text-only responses.")

    log.info("=== Care AI API ready ===")


@app.get("/v1/health")
async def health():
    return {
        "status": "ok",
        "classifier_loaded": classifier is not None,
        "nurse_loaded": nurse is not None,
        "tts_available": tts.available if tts else False,
        "loaded_drugs": list(nurse._drug_profiles.keys()) if nurse else [],
    }


@app.post("/v1/consult", response_model=ConsultResponse)
async def consult(req: ConsultRequest):
    if not classifier or not nurse:
        raise HTTPException(status_code=503, detail="Models not loaded yet")

    timings = {}

    t0 = time.time()
    visual_assessment = classifier.build_visual_assessment(req.siglip_vector)
    timings["siglip_ms"] = round((time.time() - t0) * 1000)

    t0 = time.time()
    nurse_response = nurse.generate_response(
        patient_text=req.patient_text,
        visual_assessment=visual_assessment,
        drug_name=req.drug_name,
        indication=req.indication,
    )
    timings["nurse_ms"] = round((time.time() - t0) * 1000)

    speech_text = nurse.response_to_speech_text(nurse_response)

    audio_b64 = None
    if not req.skip_tts and tts and tts.available:
        t0 = time.time()
        audio_b64 = tts.synthesize_base64(speech_text)
        timings["tts_ms"] = round((time.time() - t0) * 1000)

    timings["total_ms"] = sum(timings.values())

    return ConsultResponse(
        nurse_text=speech_text,
        nurse_structured=nurse_response,
        visual_assessment=visual_assessment,
        audio_base64=audio_b64,
        latency_ms=timings,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=int(os.getenv("CARE_AI_PORT", "8300")),
        reload=False,
        workers=1,
    )
