"""v3 SFT Data Generation — Rule-Set Driven, 2-Stage MedGemma Architecture

핵심 변경 (v2 → v3):
  1. 시나리오를 기존 시뮬레이션(data/runs)에서 가져오지 않고, rule_set에서 직접 합성
  2. 앞단 MedGemma-Vision의 출력(visual_assessment)을 시뮬레이션하여 제공
  3. Nurse에게 drug_ae_profile (non-visual AE + 증상 설명)을 컨텍스트로 제공
  4. T4 제거 — T2(질문)만 학습
  5. AE 채널 기반으로 video_detectable vs patient_reported 분류

파이프라인:
  Scenario Synthesizer (rule_set → scenario)
    → T1: Patient (Gemini) — GT AE + mood 기반 보고
    → T2a: MedGemma Nurse (local) — visual_assessment + drug_ae_profile 컨텍스트
    → Branch A: Patient T3 × N_REPEAT
    → Critic (Claude) → Expert T2 (Claude)
    → Branch B: Patient T3 × N_REPEAT
    → Dual-Objective Scoring → SFT/DPO 저장

Usage:
    python -m src.experiments.generate_sft_data_v3 \
        --rule-sets data/rule_set_calibrated_ev302.json data/rule_set_ep_sclc.json \
        --samples-per-drug 30 --gpu 7 --seed 42
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import random
import sys
import time
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from config.defaults import normalize_ae_term
from src.agents.llm_client import generate_json as gemini_generate_json, set_caller
from src.engine.mood import MoodState, compute_interaction_quality, compute_grade_distortion
from src.engine.observation import AE_DETECTION_CHANNELS, get_ae_channels
from src.engine.sampler import Sampler

# ═══════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════

CRITIC_MODEL = "claude-sonnet-4-6"
EXPERT_MODEL = "claude-sonnet-4-6"
PATIENT_MODEL = "gemini-2.0-flash"
ANTHROPIC_KEY = "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"
N_REPEAT = 3

def _update_repeat(n: int):
    global N_REPEAT
    N_REPEAT = n


MOOD_SCENARIOS = {
    "cooperative": {
        "anxiety": 0.20, "depression": 0.15, "irritability": 0.10,
        "energy": 0.70, "cognitive_clarity": 0.80,
        "trust_in_ai": 0.75, "defensiveness": 0.15,
    },
    "stoic": {
        "anxiety": 0.15, "depression": 0.20, "irritability": 0.30,
        "energy": 0.50, "cognitive_clarity": 0.70,
        "trust_in_ai": 0.25, "defensiveness": 0.70,
    },
    "hostile": {
        "anxiety": 0.20, "depression": 0.15, "irritability": 0.75,
        "energy": 0.30, "cognitive_clarity": 0.65,
        "trust_in_ai": 0.12, "defensiveness": 0.55,
    },
    "shame": {
        "anxiety": 0.50, "depression": 0.45, "irritability": 0.20,
        "energy": 0.40, "cognitive_clarity": 0.60,
        "trust_in_ai": 0.30, "defensiveness": 0.65,
    },
    "anxious": {
        "anxiety": 0.70, "depression": 0.25, "irritability": 0.15,
        "energy": 0.60, "cognitive_clarity": 0.65,
        "trust_in_ai": 0.50, "defensiveness": 0.20,
    },
}

AE_SYMPTOM_DESCRIPTIONS: dict[str, str] = {
    "nausea": "feeling sick to stomach, queasiness, aversion to food smells",
    "vomiting": "throwing up, unable to keep food or liquids down",
    "fatigue": "extreme tiredness, no energy, needing to rest frequently",
    "diarrhea": "loose/watery stools, frequent urgent bowel movements",
    "diarrhoea": "loose/watery stools, frequent urgent bowel movements",
    "constipation": "difficulty having bowel movements, bloating, abdominal discomfort",
    "peripheral_neuropathy": "tingling, numbness, burning sensation in hands and feet",
    "neuropathy": "numbness, tingling, weakness in extremities",
    "paraesthesia": "tingling, pins and needles sensation in fingers and toes",
    "headache": "head pain, pressure, throbbing",
    "cough": "persistent dry or productive cough",
    "dyspnoea": "shortness of breath, difficulty breathing, getting winded easily",
    "dyspnea": "shortness of breath, difficulty breathing",
    "chest_pain": "pain, tightness, or pressure in the chest area",
    "anorexia": "loss of appetite, no desire to eat, food seems unappealing",
    "decreased_appetite": "eating much less than usual, food doesn't appeal",
    "stomatitis": "mouth sores, pain when eating or drinking, difficulty swallowing",
    "mucositis": "mouth/throat soreness, painful swallowing",
    "arthralgia": "joint pain, stiffness, difficulty moving joints",
    "myalgia": "muscle aches, soreness, body pain",
    "abdominal_pain": "stomach or belly pain, cramping",
    "pyrexia": "fever, chills, feeling hot and cold alternately",
    "pruritus": "intense itching of the skin, scratching urge",
    "insomnia": "difficulty falling asleep, waking during the night",
    "back_pain": "pain in the lower or upper back",
    "asthenia": "general weakness, lack of strength",
    "conductive_deafness": "hearing loss, sounds seem muffled, ringing in ears",
    "dysgeusia": "metallic or altered taste, food tastes strange",
    "dyspepsia": "indigestion, heartburn, stomach discomfort after eating",
    "infection": "fever, sore throat, painful urination, redness/swelling",
    "febrile_neutropenia": "high fever with very low immunity, dangerous condition",
    "colitis": "severe abdominal cramps, bloody or frequent diarrhea, urgency",
    "pneumonitis": "dry cough, shortness of breath, chest discomfort",
    "cancer_pain": "persistent pain from tumor, dull aching",
    "hypertension": "headache, dizziness, visual changes from high blood pressure",
    "non_cardiac_chest_pain": "chest pain not related to heart, may be muscular",
    "bleeding": "unusual bleeding, bruising easily",
    "injection_site_reaction": "pain, redness, swelling at injection site",
}

_llm_symptom_cache: dict[str, str] = {}


def _generate_missing_symptom_descriptions(
    ae_terms: list[str],
    drug_name: str,
) -> None:
    """dict에 없는 AE만 LLM(Gemini)으로 생성하여 _llm_symptom_cache에 저장."""
    missing = [
        t for t in ae_terms
        if normalize_ae_term(t) not in AE_SYMPTOM_DESCRIPTIONS
        and normalize_ae_term(t) not in _llm_symptom_cache
    ]
    if not missing:
        return

    set_caller("symptom_gen")
    system_prompt = (
        "You are a medical expert. For each adverse event term, "
        "describe the symptoms a patient would actually FEEL or NOTICE, "
        "using everyday language (not medical jargon). "
        "Each description should be 10-20 words. Output JSON only."
    )
    user_prompt = f"""Drug: {drug_name}
Adverse event terms: {json.dumps(missing, ensure_ascii=False)}

Output format:
{{
    "ae_term_1": "patient-friendly symptom description",
    "ae_term_2": "patient-friendly symptom description"
}}"""

    try:
        result = gemini_generate_json(system_prompt, user_prompt, model=PATIENT_MODEL)
        for term in missing:
            norm = normalize_ae_term(term)
            desc = result.get(term) or result.get(norm) or f"discomfort related to {term}"
            _llm_symptom_cache[norm] = desc
        print(f"  [symptom_gen] LLM generated {len(missing)} new descriptions for {drug_name}")
    except Exception as e:
        print(f"  [symptom_gen] Failed: {e} — using fallback for {len(missing)} AEs")
        for term in missing:
            _llm_symptom_cache[normalize_ae_term(term)] = f"discomfort related to {term}"


def get_symptom_description(ae_term: str) -> str:
    """증상 설명 조회: 하드코딩 dict → LLM 캐시 → 폴백 순."""
    norm = normalize_ae_term(ae_term)
    return (
        AE_SYMPTOM_DESCRIPTIONS.get(norm)
        or _llm_symptom_cache.get(norm)
        or f"discomfort related to {ae_term}"
    )

# ═══════════════════════════════════════════════════════════
# Claude Client (reused from v2)
# ═══════════════════════════════════════════════════════════

_claude_client = None


def _get_claude():
    global _claude_client
    if _claude_client is None:
        if anthropic is None:
            raise ImportError("anthropic package required for Claude calls: pip install anthropic")
        _claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    return _claude_client


def claude_generate_json(system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> dict:
    client = _get_claude()
    t0 = time.time()
    msg = client.messages.create(
        model=CRITIC_MODEL, max_tokens=max_tokens, system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    elapsed = time.time() - t0
    raw = msg.content[0].text

    json_str = raw.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in json_str:
        json_str = json_str.split("```", 1)[1].split("```", 1)[0]

    start = json_str.find("{")
    if start >= 0:
        json_str = json_str[start:]
        depth, end = 0, 0
        for i, ch in enumerate(json_str):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > 0:
            json_str = json_str[:end]

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        result = {"_parse_error": True, "_raw": raw[:500]}

    in_tok = msg.usage.input_tokens
    out_tok = msg.usage.output_tokens
    print(f"  [Claude] {CRITIC_MODEL} | in={in_tok}tok out={out_tok}tok | {elapsed:.1f}s", flush=True)
    return result


# ═══════════════════════════════════════════════════════════
# 1. AE Channel Classification
# ═══════════════════════════════════════════════════════════

def classify_ae_channels(ae_profile: list[dict]) -> dict:
    """Classify each AE from ae_profile into video_detectable, patient_reported, lab_only."""
    result = {"video": [], "patient_reported": [], "lab_only": []}

    for ae in ae_profile:
        term = ae["ae_term"]
        incidence = ae.get("incidence_all_grade", 0)
        grade_dist = ae.get("grade_distribution", {})
        ch_info = get_ae_channels(term)
        channels = ch_info.get("channels", ["patient_reported"])

        entry = {
            "ae_term": term,
            "incidence": incidence,
            "grade_distribution": grade_dist,
            "channels": channels,
            "video_signs": ch_info.get("video_signs", []),
            "patient_aware_threshold": ch_info.get("patient_aware_threshold", 1),
        }

        if channels == ["lab"] or (len(channels) == 1 and channels[0] == "lab"):
            result["lab_only"].append(entry)
        elif "video_detectable" in channels:
            result["video"].append(entry)
        else:
            result["patient_reported"].append(entry)

    for cat in result.values():
        cat.sort(key=lambda x: -x["incidence"])

    return result


def build_drug_ae_profile(
    classified: dict,
    drug_name: str,
    top_n: int = 10,
) -> list[dict]:
    """Build non-visual AE profile for the Nurse's context.

    Uses hardcoded descriptions where available, calls LLM for the rest.
    """
    pr_aes = classified["patient_reported"][:top_n]
    _generate_missing_symptom_descriptions([ae["ae_term"] for ae in pr_aes], drug_name)

    profile = []
    for ae in pr_aes:
        term = ae["ae_term"]
        profile.append({
            "ae_term": term,
            "incidence_pct": f"{ae['incidence'] * 100:.0f}%",
            "common_symptoms": get_symptom_description(term),
            "patient_aware_threshold": ae["patient_aware_threshold"],
        })
    return profile


# ═══════════════════════════════════════════════════════════
# 2. Scenario Synthesizer
# ═══════════════════════════════════════════════════════════

def synthesize_scenarios(
    rule_set: dict,
    n_scenarios: int,
    seed: int = 42,
) -> list[dict]:
    """Synthesize diverse scenarios directly from a rule_set.

    Each scenario specifies:
    - GT AEs (non-visual + visual)
    - Patient mood
    - Treatment day/cycle
    - Visual assessment (simulated front-end output)
    - Drug AE profile for nurse context
    """
    rng = random.Random(seed)
    classified = classify_ae_channels(rule_set.get("ae_profile", []))
    drug_name = rule_set["drug_name"]
    drug_ae_profile = build_drug_ae_profile(classified, drug_name)
    cycle_len = rule_set.get("trial_design", {}).get("cycle_length_days", 21)

    non_visual_pool = classified["patient_reported"]
    visual_pool = classified["video"]

    scenarios = []
    mood_names = list(MOOD_SCENARIOS.keys())
    ae_counts = [0, 1, 2, 3]

    # Deterministic grid: every (ae_count, mood) combination appears at least once
    grid = [(ac, mood_names[m]) for ac in ae_counts for m in range(len(mood_names))]
    rng.shuffle(grid)

    for i in range(n_scenarios):
        # --- Treatment timing ---
        day = rng.randint(7, 126)
        cycle = (day - 1) // cycle_len + 1
        cycle_day = (day - 1) % cycle_len + 1

        # --- Select GT non-visual AEs: round-robin from grid ---
        grid_idx = i % len(grid)
        n_nv = grid[grid_idx][0]
        if non_visual_pool:
            weighted_pool = [(ae, ae["incidence"]) for ae in non_visual_pool]
            chosen_nv = _weighted_sample(rng, weighted_pool, min(n_nv, len(non_visual_pool)))
        else:
            chosen_nv = []

        # --- Select GT visual AEs (0 to 1) ---
        n_vis = rng.choices([0, 1], weights=[0.4, 0.6])[0]
        if visual_pool and n_vis > 0:
            weighted_vis = [(ae, ae["incidence"]) for ae in visual_pool]
            chosen_vis = _weighted_sample(rng, weighted_vis, min(n_vis, len(visual_pool)))
        else:
            chosen_vis = []

        # --- Assign grades ---
        gt_nv_aes = []
        for ae in chosen_nv:
            grade = _sample_grade(rng, ae["grade_distribution"])
            gt_nv_aes.append({
                "ae_term": ae["ae_term"],
                "grade": grade,
                "symptom_description": get_symptom_description(ae["ae_term"]),
            })

        gt_vis_aes = []
        for ae in chosen_vis:
            grade = _sample_grade(rng, ae["grade_distribution"])
            gt_vis_aes.append({
                "ae_term": ae["ae_term"],
                "grade": grade,
                "video_signs": ae.get("video_signs", []),
            })

        # --- Simulate visual_assessment (front-end MedGemma-Vision output) ---
        visual_assessment = _simulate_visual_assessment(rng, gt_vis_aes, gt_nv_aes)

        # --- Patient mood (from grid) ---
        mood_name = grid[grid_idx][1]

        # --- Patient persona (simplified) ---
        sex = rng.choice(["M", "F"])
        age = rng.randint(45, 78)
        persona_types = [
            "stoic_minimizer", "anxious_reporter", "trusting_compliant",
            "suspicious_skeptic", "depressed_withdrawn",
        ]
        persona_type = persona_types[i % len(persona_types)]

        scenarios.append({
            "scenario_id": f"{rule_set['drug_name'][:20]}_s{i:03d}",
            "drug_name": rule_set["drug_name"],
            "indication": rule_set.get("indication", ""),
            "treatment_day": day,
            "cycle": cycle,
            "cycle_day": cycle_day,
            "gt_non_visual_aes": gt_nv_aes,
            "gt_visual_aes": gt_vis_aes,
            "visual_assessment": visual_assessment,
            "drug_ae_profile": drug_ae_profile,
            "patient_mood": mood_name,
            "patient_demographics": {"age": age, "sex": sex},
            "patient_persona_type": persona_type,
        })

    return scenarios


def _weighted_sample(rng: random.Random, items_weights: list[tuple], k: int) -> list:
    """Weighted sampling without replacement."""
    if k <= 0:
        return []
    pool = list(items_weights)
    selected = []
    for _ in range(k):
        if not pool:
            break
        items, weights = zip(*pool)
        total = sum(weights)
        if total <= 0:
            break
        cum = []
        s = 0
        for w in weights:
            s += w
            cum.append(s)
        r = rng.random() * total
        for idx, c in enumerate(cum):
            if r <= c:
                selected.append(items[idx])
                pool.pop(idx)
                break
    return selected


def _sample_grade(rng: random.Random, grade_dist: dict) -> int:
    """Sample a grade from grade distribution dict like {"1": 0.45, "2": 0.47, ...}."""
    if not grade_dist:
        return rng.choice([1, 2])
    grades = []
    probs = []
    for g, p in sorted(grade_dist.items(), key=lambda x: int(x[0])):
        grades.append(int(g))
        probs.append(float(p))
    total = sum(probs)
    if total <= 0:
        return grades[0] if grades else 1
    probs = [p / total for p in probs]
    return rng.choices(grades, weights=probs, k=1)[0]


def _simulate_visual_assessment(
    rng: random.Random,
    gt_visual_aes: list[dict],
    gt_nv_aes: list[dict],
) -> dict:
    """Simulate what a front-end MedGemma-Vision would output from the patient's video.

    Assumptions:
    - Visual AEs are detected with high probability (80-95%)
    - Some non-visual AEs have subtle visual cues (fatigue → slow_movements, etc.)
    - May include false observations or miss subtle signs
    """
    findings = []

    for ae in gt_visual_aes:
        if rng.random() < 0.9:
            signs = ae.get("video_signs", ["visible abnormality"])
            sign_desc = ", ".join(signs[:2])
            grade_map = {1: "mild", 2: "moderate", 3: "significant", 4: "severe"}
            findings.append({
                "observation": f"{ae['ae_term'].replace('_', ' ')} suspected",
                "visual_evidence": sign_desc,
                "estimated_severity": grade_map.get(ae["grade"], "moderate"),
                "confidence": round(rng.uniform(0.6, 0.95), 2),
            })

    general_obs = []
    for ae in gt_nv_aes:
        norm = normalize_ae_term(ae["ae_term"])
        ch_info = get_ae_channels(norm)
        if "video_detectable" in ch_info.get("channels", []) and ae["grade"] >= 2:
            signs = ch_info.get("video_signs", [])
            if signs and rng.random() < 0.5:
                findings.append({
                    "observation": f"Possible {norm.replace('_', ' ')} related sign",
                    "visual_evidence": signs[0],
                    "estimated_severity": "mild",
                    "confidence": round(rng.uniform(0.3, 0.6), 2),
                })

    if not findings:
        general_obs.append("Patient appears alert and oriented")
        general_obs.append("No obvious skin lesions or discoloration observed")
    else:
        general_obs.append("Patient is seated and attentive during video call")

    return {
        "source": "MedGemma-Vision (front-end)",
        "findings": findings,
        "general_observations": general_obs,
    }


# ═══════════════════════════════════════════════════════════
# 3. T1 — Patient Initial Report (Gemini)
# ═══════════════════════════════════════════════════════════

def generate_t1_patient(
    scenario: dict,
    mood: MoodState,
    quality: dict,
    grade_distortion: int,
) -> dict:
    """Generate T1: Patient's initial report based on GT AEs + mood."""
    set_caller("patient_t1")
    demo = scenario["patient_demographics"]
    drug = scenario["drug_name"]
    indication = scenario["indication"]
    gt_nv = scenario["gt_non_visual_aes"]
    gt_vis = scenario["gt_visual_aes"]
    day = scenario["treatment_day"]

    all_gt = []
    for ae in gt_nv:
        all_gt.append({
            "ae": ae["ae_term"], "grade": ae["grade"], "type": "non_visual",
            "symptoms": ae["symptom_description"],
        })
    for ae in gt_vis:
        all_gt.append({
            "ae": ae["ae_term"], "grade": ae["grade"], "type": "visual",
            "video_signs": ae.get("video_signs", []),
        })

    system_prompt = f"""You are roleplaying as a clinical trial patient in a daily video call with an AI nurse.

PATIENT PROFILE:
- Age: {demo['age']}, Sex: {demo['sex']}
- Personality type: {scenario['patient_persona_type']}
- Drug: {drug} for {indication}
- Treatment day: {day}

BEHAVIORAL PARAMETERS (follow strictly):
- Engagement: {quality['engagement']:.2f} (0=silent, 1=talkative)
- Under-report probability: {quality['under_report_prob']:.2f} (higher = hide more symptoms)
- Over-report probability: {quality['over_report_prob']:.2f}
- Grade distortion: {grade_distortion:+d} (negative = downplay severity)

RULES:
- This is your INITIAL greeting — be natural and conversational
- Report what YOU feel/see, not medical terminology
- If under-report is high: omit mild symptoms, say "I'm fine"
- If engagement is low: brief answers, less detail
- If grade_distortion is negative: describe as less severe

Output JSON only."""

    user_prompt = f"""Day {day} video call — your initial report.

GROUND TRUTH (your actual state — filter through personality):
- Active side effects: {json.dumps(all_gt, ensure_ascii=False)}
- What the camera can see: {json.dumps(scenario['visual_assessment']['findings'], ensure_ascii=False)}

OUTPUT:
{{
    "greeting": "string",
    "reported_symptoms": [
        {{"symptom": "string (your own words)", "severity_perception": "none|mild|moderate|severe",
          "duration": "string", "is_new": true/false}}
    ],
    "omitted_symptoms": ["string (symptoms you're hiding or unaware of)"],
    "general_wellbeing": "string",
    "mood_expression": "string (emotional state shown in call)",
    "video_visible": ["string (what nurse can SEE on camera)"]
}}"""

    try:
        result = gemini_generate_json(system_prompt, user_prompt, model=PATIENT_MODEL)
    except Exception as e:
        result = {
            "greeting": "Hi.", "reported_symptoms": [], "omitted_symptoms": [],
            "general_wellbeing": "Okay I guess.", "mood_expression": "flat",
            "video_visible": [], "_error": str(e),
        }
    result["_turn"] = 1
    return result


# ═══════════════════════════════════════════════════════════
# 4. T2 — Nurse Response (MedGemma or Expert)
# ═══════════════════════════════════════════════════════════

def build_nurse_system_prompt(scenario: dict, quality: dict) -> str:
    """Build the T2 system prompt with visual_assessment + drug_ae_profile context."""
    drug = scenario["drug_name"]
    indication = scenario["indication"]
    vis_assess = scenario["visual_assessment"]
    drug_profile = scenario["drug_ae_profile"]

    vis_text = json.dumps(vis_assess["findings"], ensure_ascii=False, indent=2) if vis_assess["findings"] else "No significant visual findings."
    gen_obs = "; ".join(vis_assess.get("general_observations", []))

    profile_lines = []
    for ae in drug_profile:
        profile_lines.append(
            f"  - {ae['ae_term']} ({ae['incidence_pct']}): {ae['common_symptoms']}"
        )
    profile_text = "\n".join(profile_lines)

    return f"""You are an AI nurse conducting Turn 2 of a daily video call with a cancer patient.
You've just heard the patient's initial report and received visual analysis from a separate system.

CLINICAL CONTEXT:
- Drug: {drug}
- Indication: {indication}

VISUAL ASSESSMENT (from MedGemma-Vision front-end — already analyzed patient video):
{vis_text}
General: {gen_obs}

NON-VISUAL AE PROFILE FOR THIS DRUG (these require conversation to detect):
{profile_text}

PATIENT INTERACTION PROFILE:
- Under-report likelihood: {quality['under_report_prob']:.2f} — {'HIGH: patient likely hiding symptoms' if quality['under_report_prob'] > 0.4 else 'moderate' if quality['under_report_prob'] > 0.2 else 'low: patient is forthcoming'}
- Engagement: {quality['engagement']:.2f}

YOUR OBJECTIVES (dual):
  (a) DETECT non-visual AEs through conversation — ask about specific symptoms from the drug profile
  (b) MAINTAIN patient comfort — be warm, empathetic, build trust

STRATEGY:
1. Acknowledge what the patient shared (empathy first)
2. If visual findings exist, acknowledge them naturally ("I noticed..." or "The camera picked up...")
3. Ask about TOP non-visual AEs for this drug — use open-ended, non-threatening language
4. Maximum 3 targeted questions (don't overwhelm)
5. Use OARS: Open questions, Affirmations, Reflective listening, Summarizing

Output JSON only."""


def build_nurse_user_prompt(scenario: dict, t1_visible: dict) -> str:
    """Build the T2 user prompt."""
    day = scenario["treatment_day"]
    return f"""DAY {day} — TURN 2

PATIENT'S INITIAL REPORT (T1):
{json.dumps(t1_visible, indent=2, ensure_ascii=False)}

OUTPUT:
{{
    "approach_style": "empathetic|neutral|concerned|urgent",
    "acknowledgment": "string (brief empathetic response to patient's T1)",
    "questions": [
        {{
            "question": "string (what you ask the patient)",
            "target_ae": "string|null (which non-visual AE you're probing for)",
            "rationale": "string (why you're asking this)"
        }}
    ],
    "visual_followup": "string|null (comment on visual assessment findings, if any)",
    "preliminary_concerns": ["string (initial suspicions)"]
}}"""


def generate_t2_nurse_local(
    scenario: dict,
    t1_visible: dict,
    quality: dict,
    nurse_fn: callable,
) -> dict:
    """Generate T2 using local MedGemma (or any nurse_fn)."""
    sys_prompt = build_nurse_system_prompt(scenario, quality)
    usr_prompt = build_nurse_user_prompt(scenario, t1_visible)
    result = nurse_fn(sys_prompt, usr_prompt)
    result["_turn"] = 2
    return result


# ═══════════════════════════════════════════════════════════
# 5. T3 — Patient Response to Nurse (Gemini)
# ═══════════════════════════════════════════════════════════

def generate_t3_patient(
    scenario: dict,
    nurse_t2: dict,
    mood: MoodState,
    quality: dict,
    grade_distortion: int,
) -> dict:
    """Generate T3: Patient responds to nurse's questions."""
    set_caller("patient_t3")
    demo = scenario["patient_demographics"]
    gt_nv = scenario["gt_non_visual_aes"]
    gt_vis = scenario["gt_visual_aes"]
    day = scenario["treatment_day"]

    all_gt = []
    for ae in gt_nv:
        all_gt.append({"ae": ae["ae_term"], "grade": ae["grade"], "symptoms": ae["symptom_description"]})
    for ae in gt_vis:
        all_gt.append({"ae": ae["ae_term"], "grade": ae["grade"], "type": "visual"})

    will_cooperate_visual = quality.get("video_cooperation", 0.5) > 0.5

    system_prompt = f"""You are the same clinical trial patient, responding to the nurse's follow-up questions.

PATIENT PROFILE:
- Age: {demo['age']}, Sex: {demo['sex']}
- Personality type: {scenario['patient_persona_type']}

MOOD PARAMETERS:
- Under-report probability: {quality['under_report_prob']:.2f}
- Grade distortion: {grade_distortion:+d}
- Engagement: {quality['engagement']:.2f}

RULES:
- Answer each question based on your ACTUAL symptoms
- Your defensiveness may have lowered slightly because the nurse was caring
- If asked about a symptom you HAVE but were hiding: reveal PARTIALLY
- If asked about something you DON'T have: say you don't have it
- Be natural and conversational

Output JSON only."""

    questions = nurse_t2.get("questions", [])
    user_prompt = f"""Day {day} — TURN 3: Responding to nurse's questions

NURSE'S QUESTIONS:
{json.dumps(questions, indent=2, ensure_ascii=False)}

GROUND TRUTH (your actual state):
- Active AEs: {json.dumps(all_gt, ensure_ascii=False)}

OUTPUT:
{{
    "responses": [
        {{
            "to_question": "string (nurse's question)",
            "answer": "string (your response in your own words)",
            "revealed_symptom": "string|null (AE term if newly revealed)",
            "honesty_level": "full|partial|evasive|denied"
        }}
    ],
    "new_info_revealed": true/false,
    "emotional_reaction": "string (your emotional response)"
}}"""

    try:
        result = gemini_generate_json(system_prompt, user_prompt, model=PATIENT_MODEL)
    except Exception as e:
        result = {
            "responses": [], "new_info_revealed": False,
            "emotional_reaction": "neutral", "_error": str(e),
        }
    result["_turn"] = 3
    return result


# ═══════════════════════════════════════════════════════════
# 6. Behavioral Measurement (reused from v2)
# ═══════════════════════════════════════════════════════════

def measure_patient_behavior(t3_response: dict) -> dict:
    """Extract behavioral metrics from T3 without LLM judge."""
    responses = t3_response.get("responses", [])
    n_responses = len(responses)
    n_revealed = sum(1 for r in responses if r.get("revealed_symptom"))
    n_full_honest = sum(1 for r in responses if r.get("honesty_level") == "full")

    new_info = t3_response.get("new_info_revealed", False)
    emotional = t3_response.get("emotional_reaction", "neutral")

    emotion_score_map = {
        "relaxed": 1.0, "open": 0.9, "comfortable": 0.85,
        "neutral": 0.5, "calm": 0.5,
        "slightly defensive": 0.3, "guarded": 0.25,
        "defensive": 0.15, "hostile": 0.05, "withdrawn": 0.1,
        "anxious": 0.3, "tearful": 0.35, "frustrated": 0.2,
    }
    emotional_openness = emotion_score_map.get(emotional.lower().strip(), 0.4)

    honesty_rate = n_full_honest / max(n_responses, 1)
    reveal_rate = n_revealed / max(n_responses, 1)

    mood_proxy = (
        honesty_rate * 0.35
        + reveal_rate * 0.30
        + emotional_openness * 0.25
        + (1.0 if new_info else 0.0) * 0.10
    )

    return {
        "n_responses": n_responses,
        "n_revealed": n_revealed,
        "honesty_rate": round(honesty_rate, 3),
        "reveal_rate": round(reveal_rate, 3),
        "new_info_revealed": new_info,
        "emotional_reaction": emotional,
        "emotional_openness": round(emotional_openness, 3),
        "mood_proxy": round(mood_proxy, 3),
    }


def compute_dual_objective(
    t3_behavior: dict, gt_nv_aes: list[dict], detected_aes: list[str],
) -> dict:
    """Dual-Objective score: non-visual AE detection × mood proxy."""
    gt_names = {normalize_ae_term(ae.get("ae_term", "")) for ae in gt_nv_aes}
    det_lower = {normalize_ae_term(a) for a in detected_aes}

    if gt_names:
        ae_recall = sum(
            1 for g in gt_names if any(g in d or d in g for d in det_lower)
        ) / len(gt_names)
    else:
        ae_recall = 1.0

    reveal_bonus = t3_behavior.get("reveal_rate", 0) * 0.2
    ae_score = min(ae_recall + reveal_bonus, 1.0)
    mood_score = t3_behavior.get("mood_proxy", 0.5)
    pareto_score = ae_score * mood_score

    return {
        "ae_score": round(ae_score, 3),
        "mood_score": round(mood_score, 3),
        "pareto_score": round(pareto_score, 3),
        "ae_recall": round(ae_recall, 3),
    }


# ═══════════════════════════════════════════════════════════
# 7. Critic + Expert (Claude)
# ═══════════════════════════════════════════════════════════

def critique_nurse_t2(
    nurse_t2: dict,
    t1_visible: dict,
    scenario: dict,
    patient_mood: dict,
    dual_results: list[dict] | None = None,
) -> dict:
    """Critic evaluates nurse T2 with non-visual AE focus."""
    set_caller("critic")

    do_text = ""
    if dual_results:
        avg_ae = sum(d["ae_score"] for d in dual_results) / len(dual_results)
        avg_mood = sum(d["mood_score"] for d in dual_results) / len(dual_results)
        do_text = f"""
MEASURED OUTCOMES (averaged over {len(dual_results)} patient responses):
- Non-visual AE detection: {avg_ae:.2f}/1.0
- Patient mood/openness: {avg_mood:.2f}/1.0
- Combined (Pareto): {avg_ae * avg_mood:.2f}/1.0"""

    drug_profile_text = json.dumps(scenario["drug_ae_profile"][:6], ensure_ascii=False, indent=2)
    gt_nv_text = json.dumps(
        [{"ae": a["ae_term"], "grade": a["grade"]} for a in scenario["gt_non_visual_aes"]],
        ensure_ascii=False,
    )

    system_prompt = f"""You are a senior oncology nurse educator evaluating an AI nurse's response.

DRUG: {scenario['drug_name']}
PATIENT MOOD: {json.dumps(patient_mood, indent=2)}
GROUND TRUTH NON-VISUAL AEs (patient actually has): {gt_nv_text}
DRUG AE PROFILE (what nurse should ask about): {drug_profile_text}
{do_text}

★ DUAL OBJECTIVE: The ideal nurse response maximizes BOTH:
  (a) Non-visual AE detection — getting the patient to reveal hidden symptoms
  (b) Patient comfort — keeping mood positive, building trust

EVALUATION:
1. Did the nurse ask about the RIGHT non-visual AEs for this drug?
2. Were questions open-ended and non-threatening (OARS)?
3. Did the nurse naturally reference visual findings to build rapport?
4. Was the approach calibrated to the patient's mood?

Be SPECIFIC about what to improve.
Output JSON only."""

    user_prompt = f"""EVALUATE NURSE TURN 2

PATIENT SAID (T1):
{json.dumps(t1_visible, indent=2, ensure_ascii=False)[:800]}

NURSE RESPONDED (T2):
{json.dumps(nurse_t2, indent=2, ensure_ascii=False)[:1500]}

{{
    "overall_assessment": "string (2-3 sentences)",
    "strengths": ["string"],
    "weaknesses": ["string"],
    "missed_aes": ["string (non-visual AEs the nurse should have asked about)"],
    "improvement_instructions": "string (DETAILED rewrite guidance)",
    "dual_objective_advice": "string (balancing AE detection vs mood)",
    "priority_fix": "string (single most important change)"
}}"""

    try:
        result = claude_generate_json(system_prompt, user_prompt, max_tokens=2048)
    except Exception as e:
        result = {
            "overall_assessment": f"Critic failed: {e}",
            "strengths": [], "weaknesses": [], "missed_aes": [],
            "improvement_instructions": "", "dual_objective_advice": "",
            "priority_fix": "N/A", "_error": str(e),
        }
    return result


def generate_expert_t2(
    t1_visible: dict,
    scenario: dict,
    patient_mood: dict,
    critic_feedback: dict,
    quality: dict,
) -> dict:
    """Expert (Claude) generates improved T2 based on critic feedback."""
    set_caller("expert_nurse")

    improvement = critic_feedback.get("improvement_instructions", "")
    do_advice = critic_feedback.get("dual_objective_advice", "")
    priority = critic_feedback.get("priority_fix", "")
    missed = critic_feedback.get("missed_aes", [])
    missed_text = ", ".join(missed) if missed else "None"

    drug_profile_text = json.dumps(scenario["drug_ae_profile"][:6], ensure_ascii=False, indent=2)
    vis_text = json.dumps(scenario["visual_assessment"]["findings"], ensure_ascii=False, indent=2) if scenario["visual_assessment"]["findings"] else "No significant visual findings."

    system_prompt = f"""You are the BEST oncology AI nurse — generating a model response for training data.

CLINICAL CONTEXT:
- Drug: {scenario['drug_name']}
- Indication: {scenario['indication']}

VISUAL ASSESSMENT (from front-end MedGemma-Vision):
{vis_text}

NON-VISUAL AE PROFILE (what to ask about):
{drug_profile_text}

PATIENT MOOD: {json.dumps(patient_mood, indent=2)}

★ DUAL OBJECTIVE — optimize BOTH simultaneously:
  (a) MAXIMIZE non-visual AE detection through conversation
  (b) MAXIMIZE mood/trust — keep patient comfortable

★ REVIEWER FEEDBACK:
{improvement}

★ DUAL-OBJECTIVE STRATEGY:
{do_advice}

★ PRIORITY FIX: {priority}
★ MISSED AEs TO ASK ABOUT: {missed_text}

COMMUNICATION (OARS):
- OPEN questions: "Tell me more about..." not "Do you have...?"
- AFFIRM: "I appreciate you sharing that"
- REFLECT: Mirror the patient's own words
- SUMMARIZE: "So what I'm hearing is..."

Output JSON only."""

    user_prompt = f"""GENERATE IMPROVED NURSE RESPONSE FOR TURN 2

PATIENT SAID (T1):
{json.dumps(t1_visible, indent=2, ensure_ascii=False)[:1000]}

{{
    "approach_style": "empathetic|neutral|concerned|urgent",
    "acknowledgment": "string (empathetic response to T1)",
    "questions": [
        {{
            "question": "string (what you ask)",
            "target_ae": "string|null (non-visual AE you're probing)",
            "rationale": "string"
        }}
    ],
    "visual_followup": "string|null (comment on visual findings)",
    "preliminary_concerns": ["string"]
}}"""

    try:
        result = claude_generate_json(system_prompt, user_prompt, max_tokens=4096)
    except Exception as e:
        result = {"_error": str(e), "_fallback": True}

    result = _unwrap_nested(result)
    result["_generated_by"] = f"expert_{EXPERT_MODEL}"
    result["_turn"] = 2
    return result


def _unwrap_nested(resp: dict) -> dict:
    wrapper_keys = {"nurse_response", "response", "nurse_turn", "output"}
    for key in wrapper_keys:
        inner = resp.get(key)
        if isinstance(inner, dict) and ("questions" in inner or "approach_style" in inner):
            inner.update({k: v for k, v in resp.items() if k != key and k.startswith("_")})
            return inner
    return resp


# ═══════════════════════════════════════════════════════════
# 8. Branch: Run T3 × N for variance reduction
# ═══════════════════════════════════════════════════════════

def run_branch(
    scenario: dict,
    nurse_t2: dict,
    mood: MoodState,
    quality: dict,
    grade_distortion: int,
    n_repeat: int = N_REPEAT,
) -> list[dict]:
    """Run Patient T3 n_repeat times for the same nurse T2."""
    results = []
    for i in range(n_repeat):
        t3 = generate_t3_patient(scenario, nurse_t2, mood, quality, grade_distortion)
        behavior = measure_patient_behavior(t3)
        results.append({"t3": t3, "behavior": behavior, "trial": i})
    return results


def aggregate_branch(branch_results: list[dict], gt_nv_aes: list[dict]) -> dict:
    """Aggregate branch results into dual-objective scores."""
    behaviors = [r["behavior"] for r in branch_results]

    avg = {}
    for key in ["honesty_rate", "reveal_rate", "emotional_openness", "mood_proxy"]:
        vals = [b[key] for b in behaviors]
        avg[key] = round(sum(vals) / len(vals), 3)

    avg["new_info_rate"] = round(
        sum(1 for b in behaviors if b["new_info_revealed"]) / len(behaviors), 3
    )
    avg["n_revealed_avg"] = round(
        sum(b["n_revealed"] for b in behaviors) / len(behaviors), 2
    )

    all_detected: set[str] = set()
    for r in branch_results:
        for resp in r["t3"].get("responses", []):
            sym = resp.get("revealed_symptom")
            if sym:
                all_detected.add(sym)

    avg_behavior = {"reveal_rate": avg["reveal_rate"], "mood_proxy": avg["mood_proxy"]}
    do = compute_dual_objective(avg_behavior, gt_nv_aes, list(all_detected))
    do["detected_aes"] = sorted(all_detected)

    return {"avg_behavior": avg, "dual_objective": do, "n_trials": len(branch_results)}


# ═══════════════════════════════════════════════════════════
# 9. Main Self-Play Pipeline (T2-only, no T4)
# ═══════════════════════════════════════════════════════════

def run_selfplay_scenario(
    scenario: dict,
    nurse_fn: callable,
    seed: int = 42,
) -> dict:
    """Run the full T1→T2(branch)→Critic→Expert→T2(branch) pipeline for one scenario.

    Returns SFT + DPO data if Expert Pareto-dominates.
    """
    sid = scenario["scenario_id"]
    drug = scenario["drug_name"]
    mood_name = scenario["patient_mood"]
    day = scenario["treatment_day"]
    gt_nv = scenario["gt_non_visual_aes"]

    mood = MoodState(persona_type=scenario["patient_persona_type"], seed=seed)
    for dim, val in MOOD_SCENARIOS[mood_name].items():
        if dim in mood.state:
            mood.state[dim] = val

    quality = compute_interaction_quality(mood)
    grade_distortion = compute_grade_distortion(mood)

    max_grade = max((ae["grade"] for ae in gt_nv), default=0)
    if max_grade >= 3:
        mood.apply_defensiveness_override(max_grade)
        quality = compute_interaction_quality(mood)

    ae_labels = [f"{a['ae_term']} G{a['grade']}" for a in gt_nv]
    vis_labels = [f['observation'] for f in scenario['visual_assessment'].get('findings', [])]

    print(f"\n{'='*70}")
    print(f"  {sid} | {drug} | day {day} | mood={mood_name}")
    print(f"  GT non-visual AEs: {ae_labels}")
    print(f"  Visual findings: {vis_labels}")
    print(f"  Mood: def={mood.state['defensiveness']:.2f} trust={mood.state['trust_in_ai']:.2f}")
    print(f"{'='*70}")

    # ═══ T1: Patient (Gemini) ═══
    print(f"\n  T1: Patient → initial report...", end=" ", flush=True)
    t0 = time.time()
    t1 = generate_t1_patient(scenario, mood, quality, grade_distortion)
    print(f"({time.time()-t0:.1f}s)")

    reported = [s.get("symptom", "?")[:50] for s in t1.get("reported_symptoms", [])]
    omitted = t1.get("omitted_symptoms", [])
    print(f"    reported: {reported}")
    print(f"    omitted:  {omitted}")

    t1_visible = {k: v for k, v in t1.items() if k not in ("omitted_symptoms", "_turn", "_fallback", "_error")}

    # ═══ T2a: MedGemma Nurse ═══
    print(f"\n  T2a: MedGemma → nurse question...", end=" ", flush=True)
    t0 = time.time()
    t2_medgemma = generate_t2_nurse_local(scenario, t1_visible, quality, nurse_fn)
    print(f"({time.time()-t0:.1f}s)")

    mm_qs = [q.get("question", "?")[:60] for q in t2_medgemma.get("questions", [])]
    for q in mm_qs:
        print(f"      Q: {q}")

    # ═══ Branch A: MedGemma T2 → Patient T3 × N ═══
    print(f"\n  Branch A: Patient responds to MedGemma (×{N_REPEAT})...", end=" ", flush=True)
    t0 = time.time()
    branch_a = run_branch(scenario, t2_medgemma, mood, quality, grade_distortion)
    agg_a = aggregate_branch(branch_a, gt_nv)
    print(f"({time.time()-t0:.1f}s)")
    print(f"    AE={agg_a['dual_objective']['ae_score']:.3f} Mood={agg_a['dual_objective']['mood_score']:.3f} Pareto={agg_a['dual_objective']['pareto_score']:.3f}")

    # ═══ Critic (Claude) ═══
    print(f"\n  Critic → evaluating T2...", end=" ", flush=True)
    t0 = time.time()
    do_results_a = [compute_dual_objective(r["behavior"], gt_nv, []) for r in branch_a]
    critic = critique_nurse_t2(t2_medgemma, t1_visible, scenario, mood.to_dict(), do_results_a)
    print(f"({time.time()-t0:.1f}s)")
    print(f"    priority: {critic.get('priority_fix', '?')[:100]}")

    # ═══ Expert T2 (Claude) ═══
    print(f"\n  Expert → improved T2...", end=" ", flush=True)
    t0 = time.time()
    t2_expert = generate_expert_t2(t1_visible, scenario, mood.to_dict(), critic, quality)
    print(f"({time.time()-t0:.1f}s)")

    exp_qs = [q.get("question", "?")[:60] for q in t2_expert.get("questions", [])]
    for q in exp_qs:
        print(f"      Q: {q}")

    # ═══ Branch B: Expert T2 → Patient T3 × N ═══
    print(f"\n  Branch B: Patient responds to Expert (×{N_REPEAT})...", end=" ", flush=True)
    t0 = time.time()
    branch_b = run_branch(scenario, t2_expert, mood, quality, grade_distortion)
    agg_b = aggregate_branch(branch_b, gt_nv)
    print(f"({time.time()-t0:.1f}s)")
    print(f"    AE={agg_b['dual_objective']['ae_score']:.3f} Mood={agg_b['dual_objective']['mood_score']:.3f} Pareto={agg_b['dual_objective']['pareto_score']:.3f}")

    # ═══ Pareto Comparison ═══
    pa = agg_a["dual_objective"]
    pb = agg_b["dual_objective"]

    ae_better = "expert" if pb["ae_score"] > pa["ae_score"] else "medgemma" if pa["ae_score"] > pb["ae_score"] else "tie"
    mood_better = "expert" if pb["mood_score"] > pa["mood_score"] else "medgemma" if pa["mood_score"] > pb["mood_score"] else "tie"
    pareto_better = "expert" if pb["pareto_score"] > pa["pareto_score"] else "medgemma" if pa["pareto_score"] > pb["pareto_score"] else "tie"

    pareto_dominant = None
    if pb["ae_score"] >= pa["ae_score"] and pb["mood_score"] >= pa["mood_score"] and pb["pareto_score"] > pa["pareto_score"]:
        pareto_dominant = "expert"
    elif pa["ae_score"] >= pb["ae_score"] and pa["mood_score"] >= pb["mood_score"] and pa["pareto_score"] > pb["pareto_score"]:
        pareto_dominant = "medgemma"

    margin = abs(pb["pareto_score"] - pa["pareto_score"])

    print(f"\n  {'─'*60}")
    print(f"  {'Metric':<20} {'MedGemma':>10} {'Expert':>10} {'Winner':>10}")
    print(f"  {'─'*60}")
    print(f"  {'AE detection':<20} {pa['ae_score']:>10.3f} {pb['ae_score']:>10.3f} {ae_better:>10}")
    print(f"  {'Mood/openness':<20} {pa['mood_score']:>10.3f} {pb['mood_score']:>10.3f} {mood_better:>10}")
    print(f"  {'Pareto':<20} {pa['pareto_score']:>10.3f} {pb['pareto_score']:>10.3f} {pareto_better:>10}")
    print(f"  {'Margin':<20} {margin:>10.3f}")
    print(f"  {'─'*60}")
    if pareto_dominant:
        print(f"  ★ Pareto dominant: {pareto_dominant}")

    # ═══ Build SFT/DPO example ═══
    chosen_label = "expert" if pareto_better in ("expert", "tie") else "medgemma"

    sft_example = {
        "scenario_id": sid,
        "turn": 2,
        "turn_type": "followup",
        "context": {
            "patient_said": t1_visible,
            "visual_assessment": scenario["visual_assessment"],
            "drug_ae_profile": scenario["drug_ae_profile"],
            "patient_mood": mood.to_dict(),
            "drug_name": drug,
            "indication": scenario["indication"],
            "treatment_day": day,
        },
        "medgemma_response": {k: v for k, v in t2_medgemma.items() if not k.startswith("_")},
        "expert_response": {k: v for k, v in t2_expert.items() if not k.startswith("_")},
        "critic_feedback": critic,
        "branch_a_scores": pa,
        "branch_b_scores": pb,
        "chosen": chosen_label,
        "pareto_dominant": pareto_dominant,
        "margin": round(margin, 4),
        "gt_non_visual_aes": gt_nv,
    }

    return {
        "scenario": scenario,
        "t1": t1,
        "t2_medgemma": {k: v for k, v in t2_medgemma.items() if not k.startswith("_")},
        "t2_expert": {k: v for k, v in t2_expert.items() if not k.startswith("_")},
        "branch_a": agg_a,
        "branch_b": agg_b,
        "critic": critic,
        "pareto_better": pareto_better,
        "pareto_dominant": pareto_dominant,
        "margin": round(margin, 4),
        "sft_example": sft_example,
    }


# ═══════════════════════════════════════════════════════════
# 10. Save Results
# ═══════════════════════════════════════════════════════════

MIN_DPO_MARGIN = 0.10
MIN_SFT_EXPERT_PARETO = 0.15


def save_result(result: dict, out_dir: Path):
    """Save self-play result, SFT example, and DPO pair."""
    sid = result["sft_example"]["scenario_id"]

    detail_file = out_dir / f"detail_{sid}.json"
    with open(detail_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)

    ex = result["sft_example"]
    margin = ex.get("margin", 0)
    expert_pareto = ex["branch_b_scores"].get("pareto_score", 0)

    # SFT: Expert wins AND expert response quality is above threshold
    if ex["chosen"] == "expert" and expert_pareto >= MIN_SFT_EXPERT_PARETO:
        sft_file = out_dir / "sft_data.jsonl"
        with open(sft_file, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(ex, ensure_ascii=False, default=str) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        print(f"  SFT ✓ (margin={margin:.3f}, expert_pareto={expert_pareto:.3f})")
    elif ex["chosen"] == "expert":
        sft_skip_file = out_dir / "sft_skipped.jsonl"
        with open(sft_skip_file, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(ex, ensure_ascii=False, default=str) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        print(f"  SFT ✗ low quality (expert_pareto={expert_pareto:.3f} < {MIN_SFT_EXPERT_PARETO})")
    else:
        print(f"  SFT ✗ (medgemma won or tie)")

    # DPO: margin must be large enough for a clear preference signal
    chosen_resp = ex["expert_response"] if ex["chosen"] == "expert" else ex["medgemma_response"]
    rejected_resp = ex["medgemma_response"] if ex["chosen"] == "expert" else ex["expert_response"]

    dpo_pair = {
        "scenario_id": sid,
        "turn": 2,
        "prompt": ex["context"],
        "chosen": chosen_resp,
        "rejected": rejected_resp,
        "chosen_model": ex["chosen"],
        "margin": margin,
        "branch_scores": {"a": ex["branch_a_scores"], "b": ex["branch_b_scores"]},
        "pareto_dominant": ex["pareto_dominant"],
    }

    if margin >= MIN_DPO_MARGIN:
        dpo_file = out_dir / "dpo_pairs.jsonl"
        with open(dpo_file, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(dpo_pair, ensure_ascii=False, default=str) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        print(f"  DPO ✓ (margin={margin:.3f} ≥ {MIN_DPO_MARGIN})")
    else:
        dpo_skip_file = out_dir / "dpo_skipped.jsonl"
        with open(dpo_skip_file, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(dpo_pair, ensure_ascii=False, default=str) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        print(f"  DPO ✗ (margin={margin:.3f} < {MIN_DPO_MARGIN})")


# ═══════════════════════════════════════════════════════════
# 11. Main
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="v3 SFT Data Generation (Rule-Set Driven)")
    parser.add_argument(
        "--rule-sets", nargs="+", required=True,
        help="Path(s) to rule_set JSON files",
    )
    parser.add_argument("--samples-per-drug", type=int, default=30)
    parser.add_argument("--gpu", type=int, default=7)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--repeats", type=int, default=N_REPEAT)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Generate scenarios only, no LLM calls")
    args = parser.parse_args()

    _update_repeat(args.repeats)

    out_dir = Path(args.output_dir) if args.output_dir else (PROJECT_ROOT / "data" / "training_v3")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load rule sets
    rule_sets = []
    for rs_path in args.rule_sets:
        p = Path(rs_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        with open(p) as f:
            rs = json.load(f)
        rule_sets.append(rs)
        print(f"  Loaded: {rs['drug_name']} ({len(rs.get('ae_profile', []))} AEs)")

    # Synthesize scenarios
    all_scenarios = []
    for rs in rule_sets:
        scenarios = synthesize_scenarios(rs, args.samples_per_drug, seed=args.seed)
        all_scenarios.extend(scenarios)
        classified = classify_ae_channels(rs.get("ae_profile", []))
        print(f"  {rs['drug_name']}: {len(scenarios)} scenarios "
              f"(video={len(classified['video'])} pr={len(classified['patient_reported'])} lab={len(classified['lab_only'])})")

    print(f"\n  Total scenarios: {len(all_scenarios)}")
    print(f"  Output: {out_dir}")

    # Save scenario manifest
    manifest = []
    for s in all_scenarios:
        manifest.append({
            "id": s["scenario_id"],
            "drug": s["drug_name"],
            "day": s["treatment_day"],
            "mood": s["patient_mood"],
            "n_nv_aes": len(s["gt_non_visual_aes"]),
            "n_vis_aes": len(s["gt_visual_aes"]),
            "nv_aes": [a["ae_term"] for a in s["gt_non_visual_aes"]],
        })
    with open(out_dir / "scenario_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"  Manifest → {out_dir / 'scenario_manifest.json'}")

    if args.dry_run:
        print("\n  [DRY RUN] Scenarios generated. No LLM calls made.")
        for s in all_scenarios[:5]:
            print(f"    {s['scenario_id']}: day={s['treatment_day']} mood={s['patient_mood']} "
                  f"nv_aes={[a['ae_term'] for a in s['gt_non_visual_aes']]} "
                  f"vis={[f['observation'] for f in s['visual_assessment']['findings']]}")
        return

    # Load MedGemma
    import os
    os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)
    from src.experiments.compare_care_models import load_medgemma, medgemma_generate_json

    print(f"\n  Loading MedGemma on GPU {args.gpu}...")
    load_medgemma(gpu_id=0)

    def _nurse_fn(system_prompt, user_prompt, **kwargs):
        kwargs.pop("model", None)
        return medgemma_generate_json(system_prompt, user_prompt, **kwargs)

    # Run pipeline
    print(f"\n{'='*70}")
    print(f"  v3 Self-Play Pipeline")
    print(f"  Critic: {CRITIC_MODEL} | Expert: {EXPERT_MODEL} | Patient: {PATIENT_MODEL}")
    print(f"  Nurse: MedGemma 1.5 4B-IT (GPU {args.gpu})")
    print(f"  Drugs: {[rs['drug_name'] for rs in rule_sets]}")
    print(f"  Scenarios: {len(all_scenarios)} | Repeats: {N_REPEAT}")
    print(f"{'='*70}")

    total_t0 = time.time()
    n_ok, n_fail = 0, 0
    n_sft, n_dpo = 0, 0

    for i, scenario in enumerate(all_scenarios):
        print(f"\n{'#'*70}")
        print(f"  [{i+1}/{len(all_scenarios)}] {scenario['scenario_id']}")
        print(f"{'#'*70}")

        try:
            t0 = time.time()
            result = run_selfplay_scenario(scenario, _nurse_fn, seed=args.seed + i)
            save_result(result, out_dir)
            elapsed = time.time() - t0

            ex = result["sft_example"]
            expert_pareto = ex["branch_b_scores"].get("pareto_score", 0)
            if ex["chosen"] == "expert" and expert_pareto >= MIN_SFT_EXPERT_PARETO:
                n_sft += 1
            if result["margin"] >= MIN_DPO_MARGIN:
                n_dpo += 1

            print(f"  [{i+1}/{len(all_scenarios)}] done in {elapsed:.1f}s | SFT={n_sft} DPO={n_dpo}")
            n_ok += 1
        except Exception as e:
            import traceback
            print(f"  ✗ FAILED: {e}")
            traceback.print_exc()
            n_fail += 1

    total_elapsed = time.time() - total_t0
    print(f"\n{'='*70}")
    print(f"  COMPLETE")
    print(f"  OK: {n_ok} | Failed: {n_fail}")
    print(f"  SFT examples: {n_sft}")
    print(f"  DPO pairs: {n_dpo}")
    print(f"  Total time: {total_elapsed:.0f}s ({total_elapsed/max(n_ok,1):.0f}s per sample)")
    print(f"  Output: {out_dir}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
