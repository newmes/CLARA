"""Google Cloud Text-to-Speech wrapper.

Converts nurse text responses into audio bytes (WAV, 24 kHz).
Falls back to a simple text-only response if TTS is unavailable.
"""

from __future__ import annotations

import base64
import os

try:
    from google.cloud import texttospeech
    HAS_TTS = True
except ImportError:
    HAS_TTS = False


class TTSService:
    """Thin wrapper around Google Cloud TTS."""

    def __init__(
        self,
        language_code: str = "en-US",
        voice_name: str = "Kore",
        speaking_rate: float = 0.95,
    ):
        self.language_code = language_code
        self.voice_name = voice_name
        self.speaking_rate = speaking_rate
        self._client = None

        if HAS_TTS:
            try:
                self._client = texttospeech.TextToSpeechClient()
            except Exception:
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def synthesize(self, text: str) -> bytes | None:
        """Convert text to WAV audio bytes (24 kHz). Returns None if TTS is unavailable."""
        if not self._client or not text.strip():
            return None

        input_text = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=self.language_code,
            name=self.voice_name,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=24000,
            speaking_rate=self.speaking_rate,
        )

        response = self._client.synthesize_speech(
            input=input_text, voice=voice, audio_config=audio_config,
        )
        return response.audio_content

    def synthesize_base64(self, text: str) -> str | None:
        """Convert text to base64-encoded WAV for JSON transport."""
        audio = self.synthesize(text)
        if audio:
            return base64.b64encode(audio).decode("utf-8")
        return None
