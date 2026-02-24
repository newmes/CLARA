"""reward.py — 복합 보상 함수 (Nurse 대화 품질 평가)

ExtendedCareAgent의 care_record를 입력받아 단일 scalar reward를 산출한다.
이 reward는 SFT 데이터 필터링과 DPO preference pair 생성에 사용된다.

Reward 구성요소:
  1. information_gain:  GT 대비 AE 감지율 + 새 정보 발견
  2. mood_improvement:  7차원 mood 종합 개선도
  3. conversation_sustain: 조기종료 없이 대화를 유지한 비율
  4. severity_accuracy: AE 심각도 판단 정확도
  5. clinical_safety:   과잉/과소 에스컬레이션 페널티
  6. empathy_quality:   Judge가 평가한 공감 품질 (평균)
"""

from __future__ import annotations

import math
from src.engine.mood import MOOD_DIMENSIONS

# Reward 가중치 (합 = 1.0)
WEIGHTS = {
    "information_gain": 0.25,
    "mood_improvement": 0.20,
    "conversation_sustain": 0.10,
    "severity_accuracy": 0.15,
    "clinical_safety": 0.10,
    "empathy_quality": 0.20,
}


def compute_reward(care_record: dict, gt_aes: list[dict]) -> dict:
    """care_record + GT에서 복합 reward를 산출한다.

    Args:
        care_record: ExtendedCareAgent.conduct_extended_call() 출력
        gt_aes: Ground Truth AE 목록

    Returns:
        {
            "reward": float (0-1 scale),
            "components": {name: float for each component},
            "details": {name: explanation},
        }
    """
    components = {}

    # 1. Information Gain
    components["information_gain"] = _score_information_gain(care_record, gt_aes)

    # 2. Mood Improvement
    components["mood_improvement"] = _score_mood_improvement(care_record)

    # 3. Conversation Sustain
    components["conversation_sustain"] = _score_conversation_sustain(care_record)

    # 4. Severity Accuracy
    components["severity_accuracy"] = _score_severity_accuracy(care_record, gt_aes)

    # 5. Clinical Safety
    components["clinical_safety"] = _score_clinical_safety(care_record, gt_aes)

    # 6. Empathy Quality
    components["empathy_quality"] = _score_empathy_quality(care_record)

    reward = sum(WEIGHTS[k] * components[k] for k in WEIGHTS)

    return {
        "reward": round(reward, 4),
        "components": {k: round(v, 4) for k, v in components.items()},
        "weights": WEIGHTS,
    }


def compute_preference(record_a: dict, record_b: dict, gt_aes: list[dict]) -> dict:
    """두 care_record를 비교하여 DPO preference pair를 생성한다.

    Returns:
        {
            "chosen": "a" | "b",
            "reward_a": float,
            "reward_b": float,
            "margin": float,
            "component_comparison": {...},
        }
    """
    ra = compute_reward(record_a, gt_aes)
    rb = compute_reward(record_b, gt_aes)

    margin = ra["reward"] - rb["reward"]
    chosen = "a" if margin > 0 else "b"

    comp_comparison = {}
    for k in WEIGHTS:
        va = ra["components"][k]
        vb = rb["components"][k]
        comp_comparison[k] = {
            "a": va, "b": vb, "delta": round(va - vb, 4),
            "winner": "a" if va > vb else "b" if vb > va else "tie",
        }

    return {
        "chosen": chosen,
        "reward_a": ra["reward"],
        "reward_b": rb["reward"],
        "margin": round(abs(margin), 4),
        "component_comparison": comp_comparison,
    }


# ─── Component Scorers ───────────────────────────────────

def _score_information_gain(care_record: dict, gt_aes: list[dict]) -> float:
    gt_ae_names = {ae.get("ae", "").lower() for ae in gt_aes}
    if not gt_ae_names:
        return 1.0

    detection = care_record.get("detection", {})
    detected = {a.lower() for a in detection.get("aes_detected", [])}

    ae_recall = sum(
        1 for g in gt_ae_names if any(g in d or d in g for d in detected)
    ) / len(gt_ae_names)

    turns = care_record.get("turns", [])
    new_info_count = sum(
        1 for t in turns
        if t.get("role") == "patient" and t.get("content", {}).get("new_info_revealed")
    )
    new_info_bonus = min(new_info_count * 0.1, 0.3)

    t1 = next((t["content"] for t in turns if t.get("turn") == 1), {})
    omitted = len(t1.get("omitted_symptoms", []))
    revealed = sum(
        1 for t in turns
        if t.get("role") == "patient"
        for r in t.get("content", {}).get("responses", [])
        if r.get("revealed_symptom")
    )
    recovery_rate = revealed / max(omitted, 1)
    recovery_bonus = min(recovery_rate * 0.2, 0.2)

    return min(ae_recall * 0.6 + new_info_bonus + recovery_bonus + 0.1, 1.0)


def _score_mood_improvement(care_record: dict) -> float:
    mood_before = care_record.get("mood_before", {})
    mood_after = care_record.get("mood_after", {})

    if not mood_before or not mood_after:
        return 0.5

    positive_dims = {"energy", "cognitive_clarity", "trust_in_ai"}
    negative_dims = {"anxiety", "depression", "irritability", "defensiveness"}

    improvement = 0.0
    for dim in MOOD_DIMENSIONS:
        delta = mood_after.get(dim, 0.5) - mood_before.get(dim, 0.5)
        if dim in positive_dims:
            improvement += delta
        elif dim in negative_dims:
            improvement -= delta

    normalized = improvement / len(MOOD_DIMENSIONS)
    return _sigmoid(normalized * 10)


def _score_conversation_sustain(care_record: dict) -> float:
    n_turns = care_record.get("n_turns", len(care_record.get("turns", [])))
    terminated = care_record.get("terminated_early", False)

    if terminated and n_turns <= 2:
        return 0.1
    if terminated:
        return 0.3

    if n_turns >= 6:
        return 1.0
    if n_turns >= 4:
        return 0.7
    return 0.5


def _score_severity_accuracy(care_record: dict, gt_aes: list[dict]) -> float:
    gt_max_grade = max((ae.get("grade", 0) for ae in gt_aes), default=0)
    expected_sev = 0 if gt_max_grade == 0 else 1 if gt_max_grade <= 2 else 2 if gt_max_grade == 3 else 3

    assessment = care_record.get("nurse_assessment", {})
    severity = assessment.get("severity_level", "green")
    sev_map = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
    actual_sev = sev_map.get(severity, 0)

    distance = abs(actual_sev - expected_sev)
    return max(1.0 - distance * 0.33, 0.0)


def _score_clinical_safety(care_record: dict, gt_aes: list[dict]) -> float:
    gt_max_grade = max((ae.get("grade", 0) for ae in gt_aes), default=0)
    actions = care_record.get("actions", [])
    action_names = [a.get("action", "") for a in actions]

    score = 1.0

    over_escalation = any(
        a in ("recommend_hospital_visit", "escalate_to_physician") for a in action_names
    ) and gt_max_grade <= 2
    if over_escalation:
        score -= 0.4

    under_escalation = any(a == "no_action" for a in action_names) and gt_max_grade >= 3
    if under_escalation:
        score -= 0.5

    has_conmed = any(a == "recommend_conmed" for a in action_names)
    if has_conmed and 1 <= gt_max_grade <= 2:
        score += 0.1

    return max(min(score, 1.0), 0.0)


def _score_empathy_quality(care_record: dict) -> float:
    evaluations = care_record.get("turn_evaluations", [])
    if not evaluations:
        return 0.5

    empathy_scores = [e.get("empathy_quality", 0.5) for e in evaluations]
    oars_scores = []
    for e in evaluations:
        oars = e.get("oars_scores", {})
        if oars:
            oars_avg = sum(oars.values()) / len(oars)
            oars_scores.append(oars_avg)

    avg_empathy = sum(empathy_scores) / len(empathy_scores) if empathy_scores else 0.5
    avg_oars = sum(oars_scores) / len(oars_scores) if oars_scores else 0.5

    return avg_empathy * 0.6 + avg_oars * 0.4


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
