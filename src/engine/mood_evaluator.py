"""mood_evaluator.py — LLM-as-Judge 기반 턴별 동적 Mood 평가

Nurse의 각 발화가 환자 심리 7차원에 미치는 영향을 Gemini 2.5 Flash가 평가한다.
기존 hardcoded delta를 대체하여, 실제 대화 품질에 비례하는 mood 변화를 생성한다.

평가 기준 (Motivational Interviewing — OARS 원칙 기반):
  - Open questions: 열린 질문으로 환자의 자발적 공유를 유도했는가
  - Affirmations: 환자의 노력/상태를 긍정적으로 인정했는가
  - Reflective listening: 환자가 말한 것을 정확히 반영했는가
  - Summarizing: 핵심을 요약하여 이해를 확인했는가

추가 평가 축:
  - Threat level: 질문이 위협적/침습적인가 (높을수록 defensiveness↑)
  - Empathy quality: 공감의 진정성 (표면적 vs 깊은 공감)
  - Information yield: 이 턴에서 새로운 의학 정보가 추출되었는가
"""

from __future__ import annotations

import json
from src.agents.llm_client import generate_json, set_caller
from src.engine.mood import MOOD_DIMENSIONS

JUDGE_MODEL = "gemini-2.0-flash"


def evaluate_nurse_turn(
    nurse_utterance: dict,
    patient_utterance: dict,
    patient_mood_before: dict[str, float],
    patient_persona: dict,
    turn_number: int,
    conversation_history: list[dict] | None = None,
) -> dict:
    """Nurse 발화의 품질을 평가하고 mood delta를 산출한다.

    Args:
        nurse_utterance: Nurse의 이번 턴 출력 (T2 or T4 or T6)
        patient_utterance: 직전 Patient 턴 출력 (T1 or T3 or T5)
        patient_mood_before: 7차원 mood state (평가 시점)
        patient_persona: 환자 성격 프로파일
        turn_number: 현재 턴 번호
        conversation_history: 이전 턴 요약 (optional)

    Returns:
        {
            "mood_delta": {dim: float for dim in MOOD_DIMENSIONS},
            "oars_scores": {"open_questions": float, ...},
            "threat_level": float,
            "empathy_quality": float,
            "information_yield": float,
            "overall_quality": float,
            "rationale": str,
            "should_extend": bool,  # 추가 턴이 유익할지
        }
    """
    set_caller("mood_evaluator")

    history_text = ""
    if conversation_history:
        parts = []
        for t in conversation_history[-4:]:
            role = t.get("role", "?").upper()
            turn = t.get("turn", "?")
            content = json.dumps(t.get("content", {}), ensure_ascii=False)[:400]
            parts.append(f"[T{turn} {role}]: {content}")
        history_text = "\n".join(parts)

    system_prompt = f"""You are an expert clinical communication evaluator assessing an AI nurse's 
interaction quality with a cancer patient during a video call.

PATIENT PSYCHOLOGICAL STATE (7 dimensions, 0-1 scale):
{json.dumps(patient_mood_before, indent=2)}

PATIENT PERSONA:
{json.dumps(patient_persona, indent=2, ensure_ascii=False)}

EVALUATION FRAMEWORK — Motivational Interviewing (OARS):
1. Open questions (0-1): Did the nurse use open-ended questions to invite sharing?
2. Affirmations (0-1): Did the nurse acknowledge the patient's efforts, feelings, or situation?
3. Reflective listening (0-1): Did the nurse accurately reflect back what the patient said?
4. Summarizing (0-1): Did the nurse synthesize information to show understanding?

ADDITIONAL AXES:
5. Threat level (0-1): How threatening/invasive were the questions? 
   0=gentle/non-threatening, 1=aggressive/interrogative
6. Empathy quality (0-1): Depth of emotional attunement.
   0=robotic/formulaic, 0.5=surface acknowledgment, 1=deep genuine empathy
7. Information yield (0-1): How much NEW clinically useful information was obtained?
   0=nothing new, 1=critical new finding

MOOD DELTA RULES (output values in range -0.15 to +0.15 per dimension):
- High empathy + low threat → trust_in_ai↑, defensiveness↓, anxiety↓
- High threat + low empathy → defensiveness↑, irritability↑, trust_in_ai↓
- Good reflective listening → trust_in_ai↑, anxiety↓
- Patient feeling heard → energy↑, depression↓
- Rushed/dismissive → irritability↑, trust_in_ai↓
- Appropriate concern without alarm → anxiety stays stable or decreases
- Over-alarming → anxiety↑↑
- Ignoring patient's emotional cues → trust_in_ai↓, defensiveness↑

SHOULD_EXTEND rule:
- true if: patient seems to be opening up, there are unresolved clinical questions, 
  AND patient engagement is sufficient (energy > 0.3, irritability < 0.6)
- false if: patient is disengaging, all key questions answered, or mood is deteriorating

Output JSON only."""

    nurse_text = json.dumps(nurse_utterance, indent=2, ensure_ascii=False)[:1200]
    patient_text = json.dumps(patient_utterance, indent=2, ensure_ascii=False)[:800]

    user_prompt = f"""TURN {turn_number} EVALUATION

PREVIOUS CONVERSATION:
{history_text or "(first exchange)"}

PATIENT SAID (T{turn_number - 1}):
{patient_text}

NURSE RESPONDED (T{turn_number}):
{nurse_text}

OUTPUT:
{{
    "mood_delta": {{
        "anxiety": float,
        "depression": float,
        "irritability": float,
        "energy": float,
        "cognitive_clarity": float,
        "trust_in_ai": float,
        "defensiveness": float
    }},
    "oars_scores": {{
        "open_questions": float,
        "affirmations": float,
        "reflective_listening": float,
        "summarizing": float
    }},
    "threat_level": float,
    "empathy_quality": float,
    "information_yield": float,
    "overall_quality": float,
    "rationale": "1-2 sentence explanation",
    "should_extend": true/false
}}"""

    try:
        result = generate_json(system_prompt, user_prompt, model=JUDGE_MODEL, max_tokens=1024)
    except Exception as e:
        return _fallback_evaluation(nurse_utterance, turn_number, str(e))

    mood_delta = result.get("mood_delta", {})
    for dim in MOOD_DIMENSIONS:
        if dim in mood_delta:
            mood_delta[dim] = max(-0.15, min(0.15, float(mood_delta[dim])))
        else:
            mood_delta[dim] = 0.0
    result["mood_delta"] = mood_delta

    return result


def evaluate_conversation_outcome(
    turns: list[dict],
    patient_mood_before: dict[str, float],
    patient_mood_after: dict[str, float],
    gt_aes: list[dict],
    detected_aes: list[str],
) -> dict:
    """전체 대화의 최종 성과를 평가한다.

    Returns:
        {
            "mood_improvement": float,  # 7차원 종합 mood 변화
            "information_completeness": float,  # GT 대비 정보 수집 완성도
            "rapport_quality": float,  # 라포르 형성 품질
            "clinical_value": float,  # 임상적 가치
            "overall_grade": str,  # A/B/C/D/F
        }
    """
    positive_dims = {"energy", "cognitive_clarity", "trust_in_ai"}
    negative_dims = {"anxiety", "depression", "irritability", "defensiveness"}

    mood_improvement = 0.0
    for dim in MOOD_DIMENSIONS:
        delta = patient_mood_after.get(dim, 0.5) - patient_mood_before.get(dim, 0.5)
        if dim in positive_dims:
            mood_improvement += delta
        elif dim in negative_dims:
            mood_improvement -= delta
    mood_improvement /= len(MOOD_DIMENSIONS)

    gt_ae_names = {ae.get("ae", "").lower() for ae in gt_aes}
    detected_lower = {a.lower() for a in detected_aes}
    if gt_ae_names:
        info_completeness = sum(
            1 for g in gt_ae_names if any(g in d or d in g for d in detected_lower)
        ) / len(gt_ae_names)
    else:
        info_completeness = 1.0

    trust_delta = patient_mood_after.get("trust_in_ai", 0.5) - patient_mood_before.get("trust_in_ai", 0.5)
    def_delta = patient_mood_before.get("defensiveness", 0.5) - patient_mood_after.get("defensiveness", 0.5)
    rapport_quality = (trust_delta + def_delta) / 2

    clinical_value = info_completeness * 0.6 + max(rapport_quality, 0) * 0.4

    score = mood_improvement * 0.25 + info_completeness * 0.40 + rapport_quality * 0.15 + clinical_value * 0.20
    if score > 0.6:
        grade = "A"
    elif score > 0.4:
        grade = "B"
    elif score > 0.2:
        grade = "C"
    elif score > 0.0:
        grade = "D"
    else:
        grade = "F"

    return {
        "mood_improvement": round(mood_improvement, 4),
        "information_completeness": round(info_completeness, 4),
        "rapport_quality": round(rapport_quality, 4),
        "clinical_value": round(clinical_value, 4),
        "composite_score": round(score, 4),
        "overall_grade": grade,
    }


def _fallback_evaluation(nurse_utterance: dict, turn_number: int, error: str) -> dict:
    """Judge LLM 실패 시 heuristic fallback."""
    approach = nurse_utterance.get("approach_style", "neutral")
    _approach_quality = {"empathetic": 0.7, "concerned": 0.5, "neutral": 0.3, "urgent": 0.4}
    quality = _approach_quality.get(approach, 0.3)

    return {
        "mood_delta": {
            "anxiety": -0.02 * quality,
            "depression": 0.0,
            "irritability": -0.01 * quality,
            "energy": 0.0,
            "cognitive_clarity": 0.0,
            "trust_in_ai": 0.03 * quality,
            "defensiveness": -0.03 * quality,
        },
        "oars_scores": {
            "open_questions": quality * 0.5,
            "affirmations": quality * 0.5,
            "reflective_listening": quality * 0.5,
            "summarizing": quality * 0.3,
        },
        "threat_level": 0.3,
        "empathy_quality": quality,
        "information_yield": 0.3,
        "overall_quality": quality,
        "rationale": f"Fallback evaluation (judge error: {error[:100]})",
        "should_extend": False,
        "_fallback": True,
    }
