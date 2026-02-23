"""MedGemma Nurse Engine — generates nurse responses for the Care AI API.

Loads a fine-tuned MedGemma model (or base) and produces T2 nurse responses
given visual assessment + patient text + drug context.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent

AE_SYMPTOM_DESCRIPTIONS: dict[str, str] = {
    "nausea": "feeling sick to stomach, queasiness, aversion to food smells",
    "vomiting": "throwing up, unable to keep food or liquids down",
    "fatigue": "extreme tiredness, no energy, needing to rest frequently",
    "diarrhea": "loose/watery stools, frequent urgent bowel movements",
    "diarrhoea": "loose/watery stools, frequent urgent bowel movements",
    "constipation": "difficulty having bowel movements, bloating, abdominal discomfort",
    "peripheral_neuropathy": "tingling, numbness, burning sensation in hands and feet",
    "neuropathy": "numbness, tingling, weakness in extremities",
    "headache": "head pain, pressure, throbbing",
    "cough": "persistent dry or productive cough",
    "dyspnoea": "shortness of breath, difficulty breathing, getting winded easily",
    "dyspnea": "shortness of breath, difficulty breathing",
    "anorexia": "loss of appetite, no desire to eat, food seems unappealing",
    "decreased_appetite": "eating much less than usual, food doesn't appeal",
    "stomatitis": "mouth sores, pain when eating or drinking, difficulty swallowing",
    "arthralgia": "joint pain, stiffness, difficulty moving joints",
    "myalgia": "muscle aches, soreness, body pain",
    "abdominal_pain": "stomach or belly pain, cramping",
    "pyrexia": "fever, chills, feeling hot and cold alternately",
    "pruritus": "intense itching of the skin, scratching urge",
    "insomnia": "difficulty falling asleep, waking during the night",
    "back_pain": "pain in the lower or upper back",
    "dysgeusia": "metallic or altered taste, food tastes strange",
    "pneumonitis": "dry cough, shortness of breath, chest discomfort",
    "hypertension": "headache, dizziness, visual changes from high blood pressure",
    "bleeding": "unusual bleeding, bruising easily",
}


def _extract_drug_ae_profile(rule_set: dict) -> list[dict]:
    """Extract non-visual AE profile from rule_set for nurse context."""
    from src.engine.observation import AE_DETECTION_CHANNELS
    ae_profile = rule_set.get("ae_profile", [])
    non_visual = []
    for ae in ae_profile:
        term = ae["ae_term"]
        channels = AE_DETECTION_CHANNELS.get(term, ["patient_reported"])
        if "video_detectable" not in channels:
            pct = f"{ae['incidence_all_grade']*100:.0f}%"
            symptoms = AE_SYMPTOM_DESCRIPTIONS.get(term, f"symptoms of {term.replace('_', ' ')}")
            non_visual.append({
                "ae_term": term,
                "incidence_pct": pct,
                "common_symptoms": symptoms,
            })
    return non_visual[:8]


class NurseEngine:
    """MedGemma-based nurse response generator."""

    def __init__(
        self,
        model_path: str,
        gpu_id: int = 0,
        adapter_path: str | None = None,
        tokenizer_path: str | None = None,
    ):
        self.device = f"cuda:{gpu_id}" if torch.cuda.is_available() else "cpu"
        tok_src = tokenizer_path or model_path
        self.tokenizer = AutoTokenizer.from_pretrained(tok_src)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            torch_dtype=torch.bfloat16,
            device_map={"": self.device},
        )
        if adapter_path:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, adapter_path)
            self.model = self.model.merge_and_unload()
        self.model.eval()

        self._drug_profiles: dict[str, list[dict]] = {}

    def load_drug_context(self, rule_set_path: str | Path) -> dict:
        """Load and cache drug context from a rule_set file."""
        rule_set = json.loads(Path(rule_set_path).read_text())
        drug_name = rule_set.get("drug_name", "Unknown")
        indication = rule_set.get("indication", "")
        profile = _extract_drug_ae_profile(rule_set)
        self._drug_profiles[drug_name] = {
            "drug_name": drug_name,
            "indication": indication,
            "ae_profile": profile,
        }
        return self._drug_profiles[drug_name]

    def build_system_prompt(
        self,
        drug_name: str,
        indication: str,
        visual_assessment: dict,
        drug_ae_profile: list[dict],
    ) -> str:
        vis_findings = visual_assessment.get("findings", [])
        vis_text = json.dumps(vis_findings, ensure_ascii=False, indent=2) if vis_findings else "No significant visual findings."
        gen_obs = "; ".join(visual_assessment.get("general_observations", []))

        profile_lines = []
        for ae in drug_ae_profile[:6]:
            profile_lines.append(
                f"  - {ae.get('ae_term','')} ({ae.get('incidence_pct','')}): {ae.get('common_symptoms','')}"
            )
        profile_text = "\n".join(profile_lines) if profile_lines else "Not available."

        return (
            f"You are an AI nurse conducting a daily video call with a cancer patient.\n"
            f"You've just heard the patient's report and received visual analysis from a separate system.\n\n"
            f"CLINICAL CONTEXT:\n- Drug: {drug_name}\n- Indication: {indication}\n\n"
            f"VISUAL ASSESSMENT (from MedGemma-Vision front-end):\n{vis_text}\nGeneral: {gen_obs}\n\n"
            f"NON-VISUAL AE PROFILE FOR THIS DRUG (these require conversation to detect):\n{profile_text}\n\n"
            f"YOUR OBJECTIVES (dual):\n"
            f"  (a) DETECT non-visual AEs through conversation — ask about specific symptoms from the drug profile\n"
            f"  (b) MAINTAIN patient comfort — be warm, empathetic, build trust\n\n"
            f"STRATEGY:\n"
            f"1. Acknowledge what the patient shared (empathy first)\n"
            f"2. If visual findings exist, acknowledge them naturally\n"
            f"3. Ask about TOP non-visual AEs for this drug — use open-ended, non-threatening language\n"
            f"4. Maximum 3 targeted questions (don't overwhelm)\n"
            f"5. Use OARS: Open questions, Affirmations, Reflective listening, Summarizing\n\n"
            f"Output JSON only."
        )

    def build_user_prompt(self, patient_text: str) -> str:
        return (
            f"PATIENT'S REPORT:\n{patient_text}\n\n"
            f"OUTPUT:\n"
            f'{{\n'
            f'    "approach_style": "empathetic|neutral|concerned|urgent",\n'
            f'    "acknowledgment": "string (brief empathetic response to patient report)",\n'
            f'    "questions": [\n'
            f'        {{\n'
            f'            "question": "string (what you ask the patient)",\n'
            f'            "target_ae": "string|null (which non-visual AE you\'re probing for)",\n'
            f'            "rationale": "string (why you\'re asking this)"\n'
            f'        }}\n'
            f'    ],\n'
            f'    "visual_followup": "string|null (comment on visual assessment findings, if any)",\n'
            f'    "preliminary_concerns": ["string (initial suspicions)"]\n'
            f'}}'
        )

    def generate_response(
        self,
        patient_text: str,
        visual_assessment: dict,
        drug_name: str | None = None,
        indication: str | None = None,
        drug_ae_profile: list[dict] | None = None,
    ) -> dict:
        """Generate a nurse response given patient input and visual assessment.

        If drug_name is not provided, falls back to the default loaded drug context.
        """
        if drug_name and drug_name in self._drug_profiles:
            ctx = self._drug_profiles[drug_name]
            drug_name = ctx["drug_name"]
            indication = indication or ctx["indication"]
            drug_ae_profile = drug_ae_profile or ctx["ae_profile"]
        elif not drug_name and self._drug_profiles:
            ctx = next(iter(self._drug_profiles.values()))
            drug_name = ctx["drug_name"]
            indication = indication or ctx["indication"]
            drug_ae_profile = drug_ae_profile or ctx["ae_profile"]
        else:
            drug_name = drug_name or "Unknown"
            indication = indication or ""
            drug_ae_profile = drug_ae_profile or []

        sys_prompt = self.build_system_prompt(drug_name, indication, visual_assessment, drug_ae_profile)
        usr_prompt = self.build_user_prompt(patient_text)

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": usr_prompt},
        ]
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(text, return_tensors="pt").to(self.device)

        with torch.inference_mode():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=512,
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
            )

        gen_ids = outputs[0][inputs["input_ids"].shape[1]:]
        raw = self.tokenizer.decode(gen_ids, skip_special_tokens=True).strip()

        try:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            parsed = json.loads(match.group()) if match else {"raw_response": raw}
        except (json.JSONDecodeError, AttributeError):
            parsed = {"raw_response": raw}

        return parsed

    def response_to_speech_text(self, response: dict) -> str:
        """Convert structured nurse JSON response into natural speech text for TTS."""
        parts = []
        ack = response.get("acknowledgment", "")
        if ack:
            parts.append(ack)

        visual = response.get("visual_followup")
        if visual:
            parts.append(visual)

        questions = response.get("questions", [])
        for q in questions:
            q_text = q.get("question", "") if isinstance(q, dict) else str(q)
            if q_text:
                parts.append(q_text)

        if not parts:
            raw = response.get("raw_response", "")
            if raw:
                parts.append(raw)

        return " ".join(parts)
