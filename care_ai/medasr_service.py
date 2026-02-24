"""MedASR Service — Medical Automatic Speech Recognition.

Uses google/medasr (Conformer 105M) to transcribe medical audio.
Designed to run on CPU by default; GPU configurable via constructor.
"""

from __future__ import annotations

import base64
import io
import logging

import librosa
import numpy as np
from transformers import pipeline

log = logging.getLogger("care-ai-api.medasr")


class MedASRService:
    """Medical ASR using google/medasr via transformers pipeline."""

    def __init__(self, device: str = "cpu"):
        log.info("Loading MedASR model (google/medasr) on %s ...", device)
        self.pipe = pipeline(
            "automatic-speech-recognition",
            model="google/medasr",
            device=device,
        )
        log.info("MedASR model loaded.")

    def transcribe(self, audio_b64: str) -> str:
        """Transcribe base64-encoded WAV audio to text.

        Args:
            audio_b64: Base64-encoded WAV audio data.

        Returns:
            Transcribed text string.
        """
        raw = base64.b64decode(audio_b64)
        audio, _ = librosa.load(io.BytesIO(raw), sr=16000, mono=True)
        audio = audio.astype(np.float32)
        result = self.pipe({"raw": audio, "sampling_rate": 16000})
        return result["text"]
