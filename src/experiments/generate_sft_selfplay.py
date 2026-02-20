"""AMIE-style Self-Play + Critic + Dual-Objective SFT/DPO Data Generation

v3 — Cross-provider architecture:
  Critic:  Claude Sonnet 4 (Anthropic) — 독립 평가자, Gemini 편향 없음
  Expert:  Gemini 2.0 Flash — Critic 피드백 기반 개선 응답 생성
  Patient: Gemini 2.0 Flash — 환자 시뮬레이션
  Baseline: MedGemma 1.5 4B-IT (local) — 학습 대상

핵심:
  1. Dual-Objective: AE 감지율 × mood 변화를 동시에 측정
  2. 분기 실험: 같은 T1에서 분기 → 3회 반복으로 확률 변동 감소
  3. Mood = Patient 행동 기반 측정 (LLM Judge 불필요)
  4. Critic ≠ Expert (cross-provider → self-preference bias 제거)

Usage:
    export $(cat .env | xargs) && python -m src.experiments.generate_sft_selfplay \
        --run 20260219_050602_Padcev___Pembrolizumab_10pt_126d \
        --patient PT-001 --day 73 --scenario stoic --gpu 4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.care_agent import CareAgent
from src.agents.llm_client import generate_json as gemini_generate_json, set_caller
from src.engine.mood import MoodState, compute_interaction_quality, compute_grade_distortion
from src.engine.sampler import Sampler
from src.experiments.compare_care_models import (
    load_medgemma,
    medgemma_generate_json,
    load_patient_and_day,
)

CRITIC_MODEL = "claude-sonnet-4-6"
EXPERT_MODEL = "claude-sonnet-4-6"
PATIENT_MODEL = "gemini-2.0-flash"
ANTHROPIC_KEY = "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"
N_REPEAT = 3

_claude_client: anthropic.Anthropic | None = None


def _get_claude() -> anthropic.Anthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    return _claude_client


def claude_generate_json(system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> dict:
    """Claude API를 호출하여 JSON을 반환한다."""
    client = _get_claude()
    t0 = time.time()
    msg = client.messages.create(
        model=CRITIC_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
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
        depth = 0
        end = 0
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


def _patient_fn(system_prompt, user_prompt, **kwargs):
    kwargs.pop("model", None)
    return gemini_generate_json(system_prompt, user_prompt, model=PATIENT_MODEL, **kwargs)


def _medgemma_fn(system_prompt, user_prompt, **kwargs):
    kwargs.pop("model", None)
    return medgemma_generate_json(system_prompt, user_prompt, **kwargs)


# ─── Behavioral Mood Measurement ─────────────────────────────

def measure_patient_behavior(t3_response: dict) -> dict:
    """Patient T3 응답에서 행동 지표를 추출한다. LLM Judge 없이 측정."""
    responses = t3_response.get("responses", [])

    n_responses = len(responses)
    n_revealed = sum(1 for r in responses if r.get("revealed_symptom"))
    n_full_honest = sum(1 for r in responses if r.get("honesty_level") == "full")
    n_partial = sum(1 for r in responses if r.get("honesty_level") == "partial")

    visual = t3_response.get("visual_response", {})
    cooperated_visual = visual.get("cooperated", False)

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
        honesty_rate * 0.30
        + reveal_rate * 0.25
        + emotional_openness * 0.25
        + (1.0 if cooperated_visual else 0.0) * 0.10
        + (1.0 if new_info else 0.0) * 0.10
    )

    return {
        "n_responses": n_responses,
        "n_revealed": n_revealed,
        "honesty_rate": round(honesty_rate, 3),
        "reveal_rate": round(reveal_rate, 3),
        "cooperated_visual": cooperated_visual,
        "new_info_revealed": new_info,
        "emotional_reaction": emotional,
        "emotional_openness": round(emotional_openness, 3),
        "mood_proxy": round(mood_proxy, 3),
    }


def compute_dual_objective(
    t3_behavior: dict, gt_aes: list[dict], detected_aes: list[str],
) -> dict:
    """Dual-Objective 점수: AE 감지율 × mood proxy."""
    gt_names = {ae.get("ae", "").lower() for ae in gt_aes}
    det_lower = {a.lower() for a in detected_aes}

    if gt_names:
        ae_recall = sum(
            1 for g in gt_names if any(g in d or d in g for d in det_lower)
        ) / len(gt_names)
    else:
        ae_recall = 1.0

    reveal_bonus = t3_behavior["reveal_rate"] * 0.2
    ae_score = min(ae_recall + reveal_bonus, 1.0)
    mood_score = t3_behavior["mood_proxy"]

    pareto_score = ae_score * mood_score

    return {
        "ae_score": round(ae_score, 3),
        "mood_score": round(mood_score, 3),
        "pareto_score": round(pareto_score, 3),
        "ae_recall": round(ae_recall, 3),
    }


# ─── Critic (2.5 Pro) ────────────────────────────────────────

def critique_nurse_turn(
    nurse_response: dict,
    patient_utterance: dict,
    conversation_so_far: list[dict],
    patient_mood: dict,
    patient_persona: dict,
    clinical_context: dict,
    turn_number: int,
    dual_objective_results: list[dict] | None = None,
) -> dict:
    """Critic(2.5 Pro)이 Nurse 턴을 평가 + Dual-Objective 결과 반영."""
    set_caller("critic")

    history_parts = []
    for t in conversation_so_far:
        role = t["role"].upper()
        content = t["content"]
        if role == "PATIENT":
            content = {k: v for k, v in content.items()
                       if k not in ("omitted_symptoms", "_turn", "_fallback")}
        history_parts.append(f"[T{t['turn']} {role}]: {json.dumps(content, ensure_ascii=False)[:500]}")
    history_text = "\n".join(history_parts)

    do_text = ""
    if dual_objective_results:
        avg_ae = sum(d["ae_score"] for d in dual_objective_results) / len(dual_objective_results)
        avg_mood = sum(d["mood_score"] for d in dual_objective_results) / len(dual_objective_results)
        do_text = f"""
MEASURED OUTCOMES (averaged over {len(dual_objective_results)} patient responses):
- AE detection effectiveness: {avg_ae:.2f}/1.0
- Patient mood/openness: {avg_mood:.2f}/1.0
- Combined (Pareto): {avg_ae * avg_mood:.2f}/1.0
This tells you how well the nurse's approach ACTUALLY worked with this patient type."""

    system_prompt = f"""You are a senior oncology nurse educator (expert level) evaluating an AI nurse's response.

PATIENT CONTEXT:
- Mood: {json.dumps(patient_mood, indent=2)}
- Persona: {json.dumps(patient_persona, indent=2, ensure_ascii=False)[:400]}
- Drug: {clinical_context.get('drug_name', '?')}
- Known AEs: {json.dumps(clinical_context.get('known_aes', []), ensure_ascii=False)[:400]}
{do_text}

★ DUAL OBJECTIVE: The ideal nurse response maximizes BOTH:
  (a) AE information extraction — getting the patient to reveal symptoms
  (b) Patient psychological comfort — keeping mood positive, building trust

These often conflict. Aggressive probing catches more AEs but alienates the patient.
Gentle approaches preserve mood but miss symptoms. The BEST nurses navigate this tension.

EVALUATION CRITERIA (Motivational Interviewing — OARS):
1. Open questions: Did the nurse invite sharing freely?
2. Affirmations: Did the nurse validate the patient's experience?
3. Reflective listening: Did the nurse mirror the patient's words?
4. Summarizing: Did the nurse check understanding?

Be SPECIFIC about what should change. Don't say "improve empathy" — say EXACTLY what to say differently.
Output JSON only."""

    nurse_text = json.dumps(nurse_response, indent=2, ensure_ascii=False)[:1500]
    patient_text = json.dumps(patient_utterance, indent=2, ensure_ascii=False)[:800]

    user_prompt = f"""EVALUATE NURSE TURN {turn_number}

CONVERSATION SO FAR:
{history_text}

PATIENT SAID (T{turn_number - 1}):
{patient_text}

NURSE RESPONDED (T{turn_number}):
{nurse_text}

{{
    "overall_assessment": "string (2-3 sentences)",
    "strengths": ["string"],
    "weaknesses": ["string"],
    "missed_opportunities": ["string"],
    "improvement_instructions": "string (DETAILED rewrite guidance for dual objective)",
    "dual_objective_advice": "string (specific advice on balancing AE detection vs mood preservation for THIS patient type)",
    "priority_fix": "string (single most important change)"
}}"""

    try:
        result = claude_generate_json(system_prompt, user_prompt, max_tokens=2048)
    except Exception as e:
        result = {
            "overall_assessment": f"Critic failed: {e}",
            "strengths": [], "weaknesses": ["evaluation unavailable"],
            "missed_opportunities": [], "improvement_instructions": "",
            "dual_objective_advice": "", "priority_fix": "N/A", "_error": str(e),
        }
    return result


# ─── Expert (2.5 Pro) ────────────────────────────────────────

def generate_expert_response(
    patient_utterance: dict,
    conversation_so_far: list[dict],
    critic_feedback: dict,
    patient_mood: dict,
    clinical_context: dict,
    turn_number: int,
    turn_type: str = "followup",
) -> dict:
    """Expert(2.5 Pro)가 Critic 피드백 + Dual-Objective를 반영한 개선 응답 생성."""
    set_caller("expert_nurse")

    history_parts = []
    for t in conversation_so_far:
        role = t["role"].upper()
        content = t["content"]
        if role == "PATIENT":
            content = {k: v for k, v in content.items()
                       if k not in ("omitted_symptoms", "_turn", "_fallback")}
        history_parts.append(f"[T{t['turn']} {role}]: {json.dumps(content, ensure_ascii=False)[:500]}")
    history_text = "\n".join(history_parts)

    patient_text = json.dumps(
        {k: v for k, v in patient_utterance.items()
         if k not in ("omitted_symptoms", "_turn", "_fallback")},
        indent=2, ensure_ascii=False
    )[:1000]

    improvement_guide = critic_feedback.get("improvement_instructions", "")
    do_advice = critic_feedback.get("dual_objective_advice", "")
    priority_fix = critic_feedback.get("priority_fix", "")
    missed = critic_feedback.get("missed_opportunities", [])
    missed_text = "\n".join(f"- {m}" for m in missed) if missed else "None"

    if turn_type == "followup":
        output_schema = """{
    "approach_style": "empathetic|neutral|concerned|urgent",
    "acknowledgment": "string",
    "questions": [
        {"question": "string", "target_ae": "string|null", "requires_visual": true/false, "rationale": "string"}
    ],
    "visual_request": {"requested": true/false, "body_area": "string|null", "reason": "string|null"},
    "preliminary_concerns": ["string"]
}"""
    else:
        output_schema = """{
    "assessment": {"summary": "string", "severity_level": "green|yellow|orange|red", "key_findings": ["string"],
        "ae_status": [{"ae": "string", "grade_assessed": 1-4, "trend": "new|worsening|stable|improving"}]},
    "actions": [{"action": "string", "reason": "string", "urgency": "routine|soon|urgent"}],
    "detection": {"aes_detected": ["string"], "aes_suspected": ["string"], "new_concerns": ["string"]},
    "patient_education": ["string"],
    "next_call_focus": ["string"]
}"""

    system_prompt = f"""You are the BEST oncology AI nurse — generating a model response for training.

CLINICAL CONTEXT:
- Drug: {clinical_context.get('drug_name', '?')}
- Indication: {clinical_context.get('indication', '?')}
- Known AEs: {json.dumps(clinical_context.get('known_aes', []), ensure_ascii=False)[:400]}

PATIENT MOOD: {json.dumps(patient_mood, indent=2)}

★ DUAL OBJECTIVE — you must optimize BOTH simultaneously:
  (a) MAXIMIZE AE detection: get the patient to reveal hidden symptoms
  (b) MAXIMIZE mood/trust: keep the patient comfortable, open, and trusting

★ REVIEWER FEEDBACK TO INCORPORATE:
{improvement_guide}

★ DUAL-OBJECTIVE STRATEGY FOR THIS PATIENT:
{do_advice}

★ PRIORITY FIX: {priority_fix}

★ MISSED OPPORTUNITIES:
{missed_text}

COMMUNICATION PRINCIPLES (OARS):
- OPEN questions: "Tell me more about..." not "Do you have...?"
- AFFIRM: "I appreciate you sharing that"
- REFLECT: Mirror the patient's own words
- SUMMARIZE: "So what I'm hearing is..."

Output JSON only."""

    user_prompt = f"""GENERATE IMPROVED NURSE RESPONSE FOR TURN {turn_number}

CONVERSATION SO FAR:
{history_text}

PATIENT JUST SAID (T{turn_number - 1}):
{patient_text}

{output_schema}"""

    try:
        result = claude_generate_json(system_prompt, user_prompt, max_tokens=4096)
    except Exception as e:
        result = {"_error": str(e), "_fallback": True}

    result = _unwrap_nested_response(result)
    result["_generated_by"] = f"expert_{EXPERT_MODEL}"
    result["_turn"] = turn_number
    return result


def _unwrap_nested_response(resp: dict) -> dict:
    """Claude가 nurse_response 등으로 감싸서 줄 경우 한 단계 풀어준다."""
    wrapper_keys = {"nurse_response", "response", "nurse_turn", "output"}
    for key in wrapper_keys:
        inner = resp.get(key)
        if isinstance(inner, dict) and ("assessment" in inner or "questions" in inner or "approach_style" in inner):
            inner.update({k: v for k, v in resp.items() if k != key and k.startswith("_")})
            return inner
    return resp


# ─── Branch: 같은 T1에서 분기하여 T3 측정 ─────────────────

def run_branch(
    agent: CareAgent,
    nurse_t2: dict,
    day: int,
    day_data: dict,
    quality: dict,
    grade_distortion: int,
    n_repeat: int = N_REPEAT,
) -> list[dict]:
    """같은 nurse T2에 대해 Patient T3를 n_repeat회 생성하여 행동을 측정한다."""
    results = []
    for i in range(n_repeat):
        with patch("src.agents.care_agent.generate_json", side_effect=_patient_fn):
            t3 = agent._patient_followup_response(day, day_data, nurse_t2, quality, grade_distortion)
        behavior = measure_patient_behavior(t3)
        results.append({"t3": t3, "behavior": behavior, "trial": i})
    return results


def aggregate_branch(branch_results: list[dict], gt_aes: list[dict]) -> dict:
    """분기 결과를 집계한다."""
    behaviors = [r["behavior"] for r in branch_results]

    avg = {}
    for key in ["honesty_rate", "reveal_rate", "emotional_openness", "mood_proxy"]:
        vals = [b[key] for b in behaviors]
        avg[key] = round(sum(vals) / len(vals), 3)

    avg["cooperated_visual_rate"] = round(
        sum(1 for b in behaviors if b["cooperated_visual"]) / len(behaviors), 3
    )
    avg["new_info_rate"] = round(
        sum(1 for b in behaviors if b["new_info_revealed"]) / len(behaviors), 3
    )
    avg["n_revealed_avg"] = round(
        sum(b["n_revealed"] for b in behaviors) / len(behaviors), 2
    )

    avg_behavior = {
        "reveal_rate": avg["reveal_rate"],
        "mood_proxy": avg["mood_proxy"],
    }

    all_detected: set[str] = set()
    for r in branch_results:
        for resp in r["t3"].get("responses", []):
            sym = resp.get("revealed_symptom")
            if sym:
                all_detected.add(sym.lower())

    do = compute_dual_objective(avg_behavior, gt_aes, list(all_detected))
    do["detected_aes"] = sorted(all_detected)

    return {"avg_behavior": avg, "dual_objective": do, "n_trials": len(branch_results)}


# ─── Main Self-Play Pipeline ─────────────────────────────────

def run_selfplay_sample(
    patient: dict,
    rule_set: dict,
    day_data: dict,
    last_hr: dict | None,
    mood_scenario: str,
    seed: int = 42,
) -> dict:
    pid = patient.get("patient_id", "?")
    day = day_data["day"]
    persona_type = patient.get("persona", {}).get("type", "stoic_minimizer")

    mood = MoodState(persona_type=persona_type, seed=seed)
    for dim, val in MOOD_SCENARIOS[mood_scenario].items():
        if dim in mood.state:
            mood.state[dim] = val

    sampler = Sampler(seed=seed)
    quality = compute_interaction_quality(mood)
    grade_distortion = compute_grade_distortion(mood)

    obj = day_data.get("objective", {})
    active_aes = obj.get("active_aes", [])
    max_ae_grade = max((ae.get("grade", 0) for ae in active_aes), default=0)

    if max_ae_grade >= 3:
        mood.apply_defensiveness_override(max_ae_grade)
        quality = compute_interaction_quality(mood)

    clinical_context = {
        "drug_name": rule_set.get("drug_name", "?"),
        "indication": rule_set.get("indication", "?"),
        "known_aes": rule_set.get("ae_profiles", []),
    }

    agent = CareAgent(
        patient=patient, rule_set=rule_set, mood=mood,
        sampler=sampler, model="medgemma-1.5-4b-it",
    )
    agent._last_hospital_record = last_hr

    ae_labels = [f"{a.get('ae','?')} G{a.get('grade','?')}" for a in active_aes]
    print(f"\n{'='*70}")
    print(f"  Dual-Objective Self-Play: {pid} day {day} mood={mood_scenario}")
    print(f"  Critic: {CRITIC_MODEL} (Anthropic)  |  Expert: {EXPERT_MODEL}  |  Patient: {PATIENT_MODEL}")
    print(f"  GT AEs: {ae_labels}")
    print(f"  Mood: def={mood.state['defensiveness']:.2f} trust={mood.state['trust_in_ai']:.2f}")
    print(f"  Branching: {N_REPEAT} repeats per branch")
    print(f"{'='*70}")

    turns: list[dict] = []
    sft_examples: list[dict] = []

    # ═══ T1: Patient initial report (2.0 Flash) ═══
    print(f"\n  T1: Patient (2.0 Flash) → initial report...", end=" ", flush=True)
    t0 = time.time()
    with patch("src.agents.care_agent.generate_json", side_effect=_patient_fn):
        t1 = agent._patient_initial_report(day, day_data, quality, grade_distortion)
    print(f"({time.time()-t0:.1f}s)")
    turns.append({"turn": 1, "role": "patient", "content": t1})

    omitted = t1.get("omitted_symptoms", [])
    reported = [s.get("symptom", "?")[:50] for s in t1.get("reported_symptoms", [])]
    print(f"    reported: {reported}")
    print(f"    omitted:  {omitted}")

    # ═══ T2: MedGemma Nurse ═══
    print(f"\n  T2: MedGemma (Nurse) → followup...", end=" ", flush=True)
    t0 = time.time()
    with patch("src.agents.care_agent.generate_json", side_effect=_medgemma_fn):
        t2_medgemma = agent._nurse_followup_questions(day, t1, quality)
    print(f"({time.time()-t0:.1f}s)")
    turns.append({"turn": 2, "role": "nurse", "content": t2_medgemma})

    mm_qs = [q.get("question", "?")[:60] for q in t2_medgemma.get("questions", [])]
    print(f"    approach: {t2_medgemma.get('approach_style', '?')}")
    for q in mm_qs:
        print(f"      Q: {q}")

    # ═══ Branch A: MedGemma T2 → Patient T3 (×N) ═══
    print(f"\n  Branch A: Patient responds to MedGemma (×{N_REPEAT})...", end=" ", flush=True)
    t0 = time.time()
    branch_a = run_branch(agent, t2_medgemma, day, day_data, quality, grade_distortion)
    agg_a = aggregate_branch(branch_a, active_aes)
    print(f"({time.time()-t0:.1f}s)")
    print(f"    AE score:  {agg_a['dual_objective']['ae_score']:.3f}")
    print(f"    Mood score: {agg_a['dual_objective']['mood_score']:.3f}")
    print(f"    Pareto:    {agg_a['dual_objective']['pareto_score']:.3f}")
    print(f"    Detected:  {agg_a['dual_objective']['detected_aes']}")
    print(f"    Behavior:  honesty={agg_a['avg_behavior']['honesty_rate']:.2f} "
          f"reveal={agg_a['avg_behavior']['reveal_rate']:.2f} "
          f"openness={agg_a['avg_behavior']['emotional_openness']:.2f}")

    # ═══ Critic evaluates T2 (with Branch A outcomes) ═══
    print(f"\n  Critic (Claude Sonnet) → evaluating T2...", end=" ", flush=True)
    t0 = time.time()
    do_results_a = [
        compute_dual_objective(r["behavior"], active_aes, [])
        for r in branch_a
    ]
    critic_t2 = critique_nurse_turn(
        nurse_response=t2_medgemma, patient_utterance=t1,
        conversation_so_far=turns, patient_mood=mood.to_dict(),
        patient_persona=patient.get("persona", {}),
        clinical_context=clinical_context, turn_number=2,
        dual_objective_results=do_results_a,
    )
    print(f"({time.time()-t0:.1f}s)")
    print(f"    assessment: {critic_t2.get('overall_assessment', '?')[:120]}")
    print(f"    priority:   {critic_t2.get('priority_fix', '?')[:100]}")
    print(f"    DO advice:  {critic_t2.get('dual_objective_advice', '?')[:120]}")

    # ═══ Expert generates improved T2 ═══
    print(f"\n  Expert (Claude) → improved T2...", end=" ", flush=True)
    t0 = time.time()
    t2_expert = generate_expert_response(
        patient_utterance=t1, conversation_so_far=turns[:1],
        critic_feedback=critic_t2, patient_mood=mood.to_dict(),
        clinical_context=clinical_context, turn_number=2, turn_type="followup",
    )
    print(f"({time.time()-t0:.1f}s)")
    exp_qs = [q.get("question", "?")[:60] for q in t2_expert.get("questions", [])]
    print(f"    approach: {t2_expert.get('approach_style', '?')}")
    for q in exp_qs:
        print(f"      Q: {q}")

    # ═══ Branch B: Expert T2 → Patient T3 (×N) ═══
    print(f"\n  Branch B: Patient responds to Expert (×{N_REPEAT})...", end=" ", flush=True)
    t0 = time.time()
    branch_b = run_branch(agent, t2_expert, day, day_data, quality, grade_distortion)
    agg_b = aggregate_branch(branch_b, active_aes)
    print(f"({time.time()-t0:.1f}s)")
    print(f"    AE score:  {agg_b['dual_objective']['ae_score']:.3f}")
    print(f"    Mood score: {agg_b['dual_objective']['mood_score']:.3f}")
    print(f"    Pareto:    {agg_b['dual_objective']['pareto_score']:.3f}")
    print(f"    Detected:  {agg_b['dual_objective']['detected_aes']}")
    print(f"    Behavior:  honesty={agg_b['avg_behavior']['honesty_rate']:.2f} "
          f"reveal={agg_b['avg_behavior']['reveal_rate']:.2f} "
          f"openness={agg_b['avg_behavior']['emotional_openness']:.2f}")

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

    print(f"\n  {'─'*70}")
    print(f"  DUAL-OBJECTIVE COMPARISON (T2)")
    print(f"  {'─'*70}")
    print(f"  {'Metric':<25} {'MedGemma':>12} {'Expert':>12} {'Winner':>12}")
    print(f"  {'─'*70}")
    print(f"  {'AE detection':<25} {pa['ae_score']:>12.3f} {pb['ae_score']:>12.3f} {ae_better:>12}")
    print(f"  {'Mood/openness':<25} {pa['mood_score']:>12.3f} {pb['mood_score']:>12.3f} {mood_better:>12}")
    print(f"  {'Pareto (AE × mood)':<25} {pa['pareto_score']:>12.3f} {pb['pareto_score']:>12.3f} {pareto_better:>12}")
    print(f"  {'─'*70}")
    if pareto_dominant:
        print(f"  ★ Pareto dominant: {pareto_dominant}")
    else:
        print(f"  ⚖ Trade-off: neither Pareto-dominates")

    # ═══ SFT + DPO example (T2) ═══
    chosen_label = "expert" if pareto_better in ("expert", "tie") else "medgemma"

    t1_visible = {k: v for k, v in t1.items() if k not in ("omitted_symptoms", "_turn", "_fallback")}
    sft_examples.append({
        "turn": 2,
        "turn_type": "followup",
        "context": {
            "patient_said": t1_visible,
            "conversation_history": [],
            "patient_mood": mood.to_dict(),
            "clinical_context": {"drug_name": clinical_context["drug_name"], "indication": clinical_context["indication"]},
        },
        "medgemma_response": {k: v for k, v in t2_medgemma.items() if not k.startswith("_")},
        "expert_response": {k: v for k, v in t2_expert.items() if not k.startswith("_")},
        "critic_feedback": critic_t2,
        "branch_a_scores": pa,
        "branch_b_scores": pb,
        "chosen": chosen_label,
        "pareto_dominant": pareto_dominant,
    })

    # ═══ T4: Final assessment (both branches) ═══
    # Use best T3 from Branch A for MedGemma's T4 context
    best_t3a = max(branch_a, key=lambda r: r["behavior"]["mood_proxy"])["t3"]
    turns_mm = turns + [{"turn": 3, "role": "patient", "content": best_t3a}]

    print(f"\n  T4: MedGemma → final assessment...", end=" ", flush=True)
    t0 = time.time()
    with patch("src.agents.care_agent.generate_json", side_effect=_medgemma_fn):
        t4_medgemma = agent._nurse_final_assessment(day, turns_mm, quality)
    print(f"({time.time()-t0:.1f}s)")

    mm_sev = t4_medgemma.get("assessment", {}).get("severity_level", "?")
    mm_det = t4_medgemma.get("detection", {}).get("aes_detected", [])
    print(f"    severity: {mm_sev}, detected: {mm_det}")

    best_t3b = max(branch_b, key=lambda r: r["behavior"]["mood_proxy"])["t3"]
    turns_exp = turns[:1] + [
        {"turn": 2, "role": "nurse", "content": t2_expert},
        {"turn": 3, "role": "patient", "content": best_t3b},
    ]

    # Critic evaluates T4
    print(f"  Critic (Claude Sonnet) → evaluating T4...", end=" ", flush=True)
    t0 = time.time()
    critic_t4 = critique_nurse_turn(
        nurse_response=t4_medgemma, patient_utterance=best_t3a,
        conversation_so_far=turns_mm + [{"turn": 4, "role": "nurse", "content": t4_medgemma}],
        patient_mood=mood.to_dict(), patient_persona=patient.get("persona", {}),
        clinical_context=clinical_context, turn_number=4,
    )
    print(f"({time.time()-t0:.1f}s)")

    print(f"  Expert (Claude) → improved T4...", end=" ", flush=True)
    t0 = time.time()
    t4_expert = generate_expert_response(
        patient_utterance=best_t3b, conversation_so_far=turns_exp,
        critic_feedback=critic_t4, patient_mood=mood.to_dict(),
        clinical_context=clinical_context, turn_number=4, turn_type="assessment",
    )
    print(f"({time.time()-t0:.1f}s)")

    exp_sev = t4_expert.get("assessment", {}).get("severity_level", "?")
    exp_det = t4_expert.get("detection", {}).get("aes_detected", [])
    print(f"    severity: {exp_sev}, detected: {exp_det}")

    # ═══ T4 recall-based scoring ═══
    gt_names = {ae.get("ae", "").lower() for ae in active_aes}
    mm_all_det = {a.lower() for a in mm_det}
    exp_all_det = {a.lower() for a in exp_det}

    def _fuzzy_match(gt_set: set, det_set: set) -> float:
        if not gt_set:
            return 1.0
        hits = 0
        for g in gt_set:
            g_words = set(g.replace("_", " ").split())
            for d in det_set:
                d_norm = d.replace("_", " ")
                if g in d or d in g or any(w in d_norm for w in g_words if len(w) > 3):
                    hits += 1
                    break
        return hits / len(gt_set)

    mm_recall = _fuzzy_match(gt_names, mm_all_det)
    exp_recall = _fuzzy_match(gt_names, exp_all_det)

    sev_score = {"green": 0.25, "yellow": 0.50, "orange": 0.75, "red": 1.0}
    mm_sev_s = sev_score.get(mm_sev, 0.0)
    exp_sev_s = sev_score.get(exp_sev, 0.0)

    mm_t4_score = round(mm_recall * 0.7 + mm_sev_s * 0.3, 3)
    exp_t4_score = round(exp_recall * 0.7 + exp_sev_s * 0.3, 3)
    t4_chosen = "expert" if exp_t4_score >= mm_t4_score else "medgemma"

    t4_scores_a = {"recall": round(mm_recall, 3), "severity": mm_sev, "t4_score": mm_t4_score}
    t4_scores_b = {"recall": round(exp_recall, 3), "severity": exp_sev, "t4_score": exp_t4_score}

    print(f"  T4 scores: MedGemma={mm_t4_score:.3f} Expert={exp_t4_score:.3f} → {t4_chosen}")

    t3_visible = {k: v for k, v in best_t3a.items() if k not in ("omitted_symptoms", "_turn", "_fallback")}
    sft_examples.append({
        "turn": 4,
        "turn_type": "assessment",
        "context": {
            "patient_said": t3_visible,
            "conversation_history": [
                {"turn": 1, "role": "patient", "summary": t1_visible},
                {"turn": 2, "role": "nurse", "summary": {k: v for k, v in t2_medgemma.items() if not k.startswith("_")}},
                {"turn": 3, "role": "patient", "summary": t3_visible},
            ],
            "patient_mood": mood.to_dict(),
            "clinical_context": {"drug_name": clinical_context["drug_name"], "indication": clinical_context["indication"]},
        },
        "medgemma_response": {k: v for k, v in t4_medgemma.items() if not k.startswith("_")},
        "expert_response": {k: v for k, v in t4_expert.items() if not k.startswith("_")},
        "critic_feedback": critic_t4,
        "branch_a_scores": t4_scores_a,
        "branch_b_scores": t4_scores_b,
        "chosen": t4_chosen,
    })

    print(f"\n{'='*70}")
    print(f"  FINAL SUMMARY")
    print(f"{'='*70}")
    print(f"  GT: {list(gt_names)}")
    print(f"  MedGemma: recall={mm_recall:.0%} sev={mm_sev} det={list(mm_all_det)}")
    print(f"  Expert:   recall={exp_recall:.0%} sev={exp_sev} det={list(exp_all_det)}")
    print(f"  T2 Pareto: MedGemma={pa['pareto_score']:.3f} Expert={pb['pareto_score']:.3f} → {pareto_better}")
    print(f"  T4 Score:  MedGemma={mm_t4_score:.3f} Expert={exp_t4_score:.3f} → {t4_chosen}")
    print(f"  SFT examples: {len(sft_examples)} (turn-aligned)")
    print(f"{'='*70}")

    return {
        "patient_id": pid, "day": day, "mood_scenario": mood_scenario,
        "models": {"expert": f"{EXPERT_MODEL} (anthropic)", "critic": f"{CRITIC_MODEL} (anthropic)", "patient": PATIENT_MODEL, "baseline": "medgemma-1.5-4b-it"},
        "gt_aes": [{"ae": a.get("ae"), "grade": a.get("grade")} for a in active_aes],
        "omitted_symptoms": omitted,
        "t2_comparison": {
            "medgemma": {"response": {k: v for k, v in t2_medgemma.items() if not k.startswith("_")}, "branch": agg_a},
            "expert": {"response": {k: v for k, v in t2_expert.items() if not k.startswith("_")}, "branch": agg_b},
            "pareto_dominant": pareto_dominant, "pareto_better": pareto_better,
        },
        "t4_comparison": {
            "medgemma": {"severity": mm_sev, "detected": list(mm_all_det), "recall": round(mm_recall, 2)},
            "expert": {"severity": exp_sev, "detected": list(exp_all_det), "recall": round(exp_recall, 2)},
        },
        "critic_feedback": {"t2": critic_t2, "t4": critic_t4},
        "sft_examples": sft_examples,
    }


BATCH_CONFIGS = [
    ("PT-001", 60, "stoic"),
    ("PT-002", 32, "hostile"),
    ("PT-003", 58, "shame"),
    ("PT-004", 42, "cooperative"),
    ("PT-005", 57, "anxious"),
    ("PT-006", 38, "stoic"),
    ("PT-007", 60, "hostile"),
    ("PT-008", 61, "cooperative"),
    ("PT-009", 42, "shame"),
]


def auto_sample_configs(
    run_path: str,
    seed: int = 42,
    days_per_patient: int = 3,
    scenarios: list[str] | None = None,
) -> list[tuple[str, int, str]]:
    """AE 0개~최대치까지 다양하게 환자-일-시나리오 조합을 자동 샘플링한다.

    각 환자에서 AE 개수 구간별로 대표 day를 뽑아 다양성을 확보한다.
    """
    import os
    import random as _rng
    _rng.seed(seed)

    scenarios = scenarios or list(MOOD_SCENARIOS.keys())
    sim_dir = os.path.join(run_path, "simulations")
    pt_dir = os.path.join(run_path, "patients")

    if not os.path.isdir(sim_dir) or not os.path.isdir(pt_dir):
        print(f"  [auto_sample] Run not found: {run_path}")
        return []

    pts = sorted(f.replace(".json", "") for f in os.listdir(pt_dir) if f.endswith(".json"))
    configs = []

    for pid in pts:
        fpath = None
        for suffix in ["_care_ai.jsonl", "_natural.jsonl"]:
            p = os.path.join(sim_dir, f"{pid}{suffix}")
            if os.path.exists(p):
                fpath = p
                break
        if not fpath:
            continue

        buckets: dict[int, list[int]] = {}
        with open(fpath) as f:
            for line in f:
                d = json.loads(line)
                day = d.get("day", 0)
                n_ae = len([ae for ae in d.get("AE", []) if ae.get("AEONGO")])
                buckets.setdefault(n_ae, []).append(day)

        if not buckets:
            continue

        selected_days = []

        ae_counts = sorted(buckets.keys())
        if len(ae_counts) <= days_per_patient:
            for ac in ae_counts:
                selected_days.append(_rng.choice(buckets[ac]))
        else:
            indices = [round(i * (len(ae_counts) - 1) / (days_per_patient - 1))
                       for i in range(days_per_patient)]
            for idx in indices:
                ac = ae_counts[idx]
                selected_days.append(_rng.choice(buckets[ac]))

        for day in selected_days:
            scenario = _rng.choice(scenarios)
            configs.append((pid, day, scenario))

    _rng.shuffle(configs)
    return configs


import fcntl


def _save_result(result: dict, patient_id: str, day: int, scenario: str, out_dir: Path):
    """Save self-play result, SFT examples, and DPO pairs to disk (file-lock safe)."""
    out_file = out_dir / f"selfplay_{patient_id}_d{day}_{scenario}.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Result → {out_file}")

    sft_file = out_dir / "sft_data.jsonl"
    n_sft = 0
    with open(sft_file, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        for ex in result["sft_examples"]:
            if ex.get("chosen") == "medgemma":
                continue
            f.write(json.dumps(ex, ensure_ascii=False, default=str) + "\n")
            n_sft += 1
        fcntl.flock(f, fcntl.LOCK_UN)
    n_skip = len(result["sft_examples"]) - n_sft
    skip_msg = f" (skipped {n_skip} medgemma-wins)" if n_skip else ""
    print(f"  SFT → {sft_file} (+{n_sft}{skip_msg})")

    dpo_file = out_dir / "dpo_pairs.jsonl"
    n_dpo = 0
    with open(dpo_file, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        for ex in result["sft_examples"]:
            winner = ex.get("chosen", "expert")
            if winner == "expert":
                chosen_resp = ex["expert_response"]
                rejected_resp = ex["medgemma_response"]
            else:
                chosen_resp = ex["medgemma_response"]
                rejected_resp = ex["expert_response"]

            pair = {
                "turn": ex["turn"], "turn_type": ex["turn_type"],
                "prompt": ex["context"],
                "chosen": chosen_resp,
                "rejected": rejected_resp,
                "chosen_model": winner,
                "critic_feedback": ex["critic_feedback"].get("improvement_instructions", ""),
                "dual_objective_advice": ex["critic_feedback"].get("dual_objective_advice", ""),
                "branch_scores": {"a": ex.get("branch_a_scores"), "b": ex.get("branch_b_scores")},
                "pareto_dominant": ex.get("pareto_dominant"),
                "patient_id": patient_id, "day": day, "mood_scenario": scenario,
            }
            f.write(json.dumps(pair, ensure_ascii=False, default=str) + "\n")
            n_dpo += 1
        fcntl.flock(f, fcntl.LOCK_UN)
    print(f"  DPO → {dpo_file} (+{n_dpo})")


def _worker_process(
    worker_id: int,
    gpu_id: int,
    run_id: str,
    configs: list[tuple],
    seed: int,
    repeats: int,
    out_dir: str,
):
    """독립 프로세스: 자체 GPU에 MedGemma 로드 후 할당된 샘플 순차 처리."""
    import os
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    _update_repeat(repeats)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"\n  [Worker {worker_id}] GPU {gpu_id} — {len(configs)} samples assigned")
    load_medgemma(gpu_id=0)

    n_ok, n_fail = 0, 0
    for i, (pid, day, scenario) in enumerate(configs):
        tag = f"[W{worker_id} {i+1}/{len(configs)}]"
        print(f"\n{'#'*70}")
        print(f"  {tag} {pid} day {day} scenario={scenario}  (GPU {gpu_id})")
        print(f"{'#'*70}")
        try:
            patient, rule_set, day_data, last_hr = load_patient_and_day(run_id, pid, day)
            t0 = time.time()
            result = run_selfplay_sample(patient, rule_set, day_data, last_hr, scenario, seed + i)
            _save_result(result, pid, day, scenario, out_path)
            print(f"  {tag} done in {time.time()-t0:.1f}s")
            n_ok += 1
        except Exception as e:
            import traceback
            print(f"  {tag} ✗ FAILED: {e}")
            traceback.print_exc()
            n_fail += 1

    print(f"\n  [Worker {worker_id}] Finished: {n_ok} ok, {n_fail} failed")
    return n_ok, n_fail


def _worker_process_auto(
    worker_id: int,
    gpu_id: int,
    configs: list[tuple],
    run_ids: list[str],
    seed: int,
    repeats: int,
    out_dir: str,
):
    """Auto 모드 워커: 각 config마다 다른 run_id를 사용할 수 있다."""
    import os
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    _update_repeat(repeats)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"\n  [Worker {worker_id}] GPU {gpu_id} — {len(configs)} samples assigned")
    load_medgemma(gpu_id=0)

    n_ok, n_fail = 0, 0
    for i, ((pid, day, scenario), run_id) in enumerate(zip(configs, run_ids)):
        tag = f"[W{worker_id} {i+1}/{len(configs)}]"
        print(f"\n{'#'*70}")
        print(f"  {tag} {pid} day {day} scenario={scenario} run={run_id.split('_')[-1][:10]}  (GPU {gpu_id})")
        print(f"{'#'*70}")
        try:
            patient, rule_set, day_data, last_hr = load_patient_and_day(run_id, pid, day)
            t0 = time.time()
            result = run_selfplay_sample(patient, rule_set, day_data, last_hr, scenario, seed + i)
            _save_result(result, pid, day, scenario, out_path)
            print(f"  {tag} done in {time.time()-t0:.1f}s")
            n_ok += 1
        except Exception as e:
            import traceback
            print(f"  {tag} ✗ FAILED: {e}")
            traceback.print_exc()
            n_fail += 1

    print(f"\n  [Worker {worker_id}] Finished: {n_ok} ok, {n_fail} failed")
    return n_ok, n_fail


def main():
    parser = argparse.ArgumentParser(description="Dual-Objective Self-Play SFT/DPO generation")
    parser.add_argument("--run", required=True)
    parser.add_argument("--patient", default=None, help="Single patient ID (omit for batch)")
    parser.add_argument("--day", type=int, default=None)
    parser.add_argument("--scenario", default=None, choices=list(MOOD_SCENARIOS.keys()))
    parser.add_argument("--batch", action="store_true", help="Run all BATCH_CONFIGS")
    parser.add_argument("--batch-limit", type=int, default=None, help="Max samples in batch mode")
    parser.add_argument("--auto", action="store_true", help="Auto-sample diverse AE counts per patient")
    parser.add_argument("--days-per-patient", type=int, default=3, help="Days to sample per patient (auto mode)")
    parser.add_argument("--runs", type=str, default=None, help="Comma-separated run IDs for auto mode")
    parser.add_argument("--gpu", type=int, default=4)
    parser.add_argument("--gpus", type=str, default=None, help="Comma-separated GPU IDs for parallel (e.g. 5,6,7)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--repeats", type=int, default=N_REPEAT)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    _update_repeat(args.repeats)

    out_dir = Path(args.output_dir) if args.output_dir else (PROJECT_ROOT / "data" / "training_selfplay_v2")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.auto:
        run_ids = [r.strip() for r in (args.runs or args.run).split(",")]
        all_configs = []
        run_map = {}

        runs_base = PROJECT_ROOT / "data" / "runs"
        for rid in run_ids:
            rpath = runs_base / rid
            if not rpath.exists():
                rpath = runs_base / "old" / rid
            if not rpath.exists():
                print(f"  [auto] Skipping {rid} — not found")
                continue
            cfgs = auto_sample_configs(str(rpath), seed=args.seed, days_per_patient=args.days_per_patient)
            for c in cfgs:
                all_configs.append(c)
                run_map[c] = rid
            print(f"  [auto] {rid}: {len(cfgs)} samples")

        if args.batch_limit:
            all_configs = all_configs[:args.batch_limit]

        ae_counts = {}
        for pid, day, sc in all_configs:
            rid = run_map[(pid, day, sc)]
            rpath = runs_base / rid
            if not rpath.exists():
                rpath = runs_base / "old" / rid
            for suffix in ["_care_ai.jsonl", "_natural.jsonl"]:
                fp = rpath / "simulations" / f"{pid}{suffix}"
                if fp.exists():
                    with open(fp) as f:
                        for line in f:
                            d = json.loads(line)
                            if d.get("day") == day:
                                n = len([a for a in d.get("AE", []) if a.get("AEONGO")])
                                ae_counts.setdefault(n, 0)
                                ae_counts[n] += 1
                    break

        print(f"\n  Total auto-sampled: {len(all_configs)} configs")
        print(f"  AE distribution: { {k: ae_counts.get(k,0) for k in sorted(ae_counts)} }")
        print(f"  Scenarios: { {sc: sum(1 for _,_,s in all_configs if s==sc) for sc in MOOD_SCENARIOS} }")

        if args.gpus:
            import multiprocessing as mp
            mp.set_start_method("spawn", force=True)

            gpu_ids = [int(g) for g in args.gpus.split(",")]
            n_workers = len(gpu_ids)
            chunks = [[] for _ in range(n_workers)]
            chunk_runs = [[] for _ in range(n_workers)]
            for i, cfg in enumerate(all_configs):
                chunks[i % n_workers].append(cfg)
                chunk_runs[i % n_workers].append(run_map[cfg])

            print(f"\n{'='*70}")
            print(f"  Parallel Auto-Sample ({n_workers} workers)")
            print(f"  GPUs: {gpu_ids}")
            for w in range(n_workers):
                print(f"  Worker {w} (GPU {gpu_ids[w]}): {len(chunks[w])} samples")
            print(f"{'='*70}")

            total_t0 = time.time()
            processes = []
            for w, (gpu, chunk, c_runs) in enumerate(zip(gpu_ids, chunks, chunk_runs)):
                if not chunk:
                    continue
                p = mp.Process(
                    target=_worker_process_auto,
                    args=(w, gpu, chunk, c_runs, args.seed + w * 100, args.repeats, str(out_dir)),
                )
                p.start()
                processes.append(p)

            for p in processes:
                p.join()

            total = time.time() - total_t0
            n_sft = sum(1 for _ in open(out_dir / "sft_data.jsonl")) if (out_dir / "sft_data.jsonl").exists() else 0
            n_dpo = sum(1 for _ in open(out_dir / "dpo_pairs.jsonl")) if (out_dir / "dpo_pairs.jsonl").exists() else 0
            print(f"\n{'='*70}")
            print(f"  All workers done in {total:.0f}s (wall)")
            print(f"  SFT: {n_sft}  DPO: {n_dpo}")
            print(f"  Throughput: {total/max(len(all_configs),1):.0f}s/sample (wall)")
            print(f"{'='*70}")
        else:
            print(f"  Use --gpus to run in parallel, or --batch for sequential.")
        return

    if args.batch and args.gpus:
        import multiprocessing as mp
        mp.set_start_method("spawn", force=True)

        gpu_ids = [int(g) for g in args.gpus.split(",")]
        configs = BATCH_CONFIGS[:args.batch_limit] if args.batch_limit else BATCH_CONFIGS
        n_workers = len(gpu_ids)

        chunks = [[] for _ in range(n_workers)]
        for i, cfg in enumerate(configs):
            chunks[i % n_workers].append(cfg)

        print(f"\n{'='*70}")
        print(f"  Parallel Self-Play ({n_workers} workers)")
        print(f"  GPUs: {gpu_ids}")
        print(f"  Critic:  {CRITIC_MODEL} (Anthropic)")
        print(f"  Expert:  {EXPERT_MODEL} (Anthropic)")
        print(f"  Patient: {PATIENT_MODEL} (Google)")
        print(f"  Total samples: {len(configs)}")
        for w, chunk in enumerate(chunks):
            labels = [f"{c[0]}d{c[1]}_{c[2]}" for c in chunk]
            print(f"  Worker {w} (GPU {gpu_ids[w]}): {labels}")
        print(f"{'='*70}")

        total_t0 = time.time()
        processes = []
        for w, (gpu, chunk) in enumerate(zip(gpu_ids, chunks)):
            if not chunk:
                continue
            p = mp.Process(
                target=_worker_process,
                args=(w, gpu, args.run, chunk, args.seed + w * 100, args.repeats, str(out_dir)),
            )
            p.start()
            processes.append(p)

        for p in processes:
            p.join()

        total = time.time() - total_t0
        n_sft = sum(1 for _ in open(out_dir / "sft_data.jsonl")) if (out_dir / "sft_data.jsonl").exists() else 0
        n_dpo = sum(1 for _ in open(out_dir / "dpo_pairs.jsonl")) if (out_dir / "dpo_pairs.jsonl").exists() else 0

        print(f"\n{'='*70}")
        print(f"  All workers done in {total:.0f}s (wall clock)")
        print(f"  SFT examples: {n_sft}")
        print(f"  DPO pairs:    {n_dpo}")
        print(f"  Throughput:    {total/max(len(configs),1):.0f}s per sample (wall)")
        print(f"{'='*70}")

    elif args.batch:
        print(f"\n{'='*70}")
        print(f"  Dual-Objective Self-Play + Critic (v3 — cross-provider)")
        print(f"  Critic:   {CRITIC_MODEL} (Anthropic)")
        print(f"  Expert:   {EXPERT_MODEL} (Anthropic)")
        print(f"  Patient:  {PATIENT_MODEL} (Google)")
        print(f"  Baseline: MedGemma 1.5 4B-IT (local, GPU {args.gpu})")
        print(f"  Branch repeats: {N_REPEAT}")
        print(f"{'='*70}")

        load_medgemma(gpu_id=args.gpu)

        configs = BATCH_CONFIGS[:args.batch_limit] if args.batch_limit else BATCH_CONFIGS
        print(f"\n  Batch mode: {len(configs)} samples (sequential)")
        total_t0 = time.time()
        n_ok, n_fail = 0, 0
        for i, (pid, day, scenario) in enumerate(configs):
            print(f"\n{'#'*70}")
            print(f"  [{i+1}/{len(configs)}] {pid} day {day} scenario={scenario}")
            print(f"{'#'*70}")
            try:
                patient, rule_set, day_data, last_hr = load_patient_and_day(args.run, pid, day)
                t0 = time.time()
                result = run_selfplay_sample(patient, rule_set, day_data, last_hr, scenario, args.seed + i)
                _save_result(result, pid, day, scenario, out_dir)
                print(f"  Sample time: {time.time()-t0:.1f}s")
                n_ok += 1
            except Exception as e:
                print(f"  ✗ FAILED: {e}")
                n_fail += 1
        total = time.time() - total_t0
        print(f"\n{'='*70}")
        print(f"  Batch complete: {n_ok} ok, {n_fail} failed, {total:.0f}s total")
        print(f"  Avg: {total/max(n_ok,1):.0f}s per sample")
        print(f"{'='*70}")
    else:
        print(f"\n{'='*70}")
        print(f"  Dual-Objective Self-Play + Critic (v3 — cross-provider)")
        print(f"  Critic:   {CRITIC_MODEL} (Anthropic)")
        print(f"  Expert:   {EXPERT_MODEL} (Anthropic)")
        print(f"  Patient:  {PATIENT_MODEL} (Google)")
        print(f"  Baseline: MedGemma 1.5 4B-IT (local, GPU {args.gpu})")
        print(f"  Branch repeats: {N_REPEAT}")
        print(f"{'='*70}")

        load_medgemma(gpu_id=args.gpu)

        pid = args.patient or "PT-001"
        day = args.day or 73
        scenario = args.scenario or "stoic"
        patient, rule_set, day_data, last_hr = load_patient_and_day(args.run, pid, day)
        t0 = time.time()
        result = run_selfplay_sample(patient, rule_set, day_data, last_hr, scenario, args.seed)
        total = time.time() - t0
        _save_result(result, pid, day, scenario, out_dir)
        print(f"\n  Total: {total:.1f}s")


if __name__ == "__main__":
    main()
