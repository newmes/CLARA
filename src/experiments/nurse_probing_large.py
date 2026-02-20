"""Large-Scale Nurse Probing Experiment.

Runs cross-model comparison (Gemini-Patient × {Gemini,MedGemma}-Nurse)
across multiple patients, days, and mood scenarios for statistical analysis.

Pipeline integrity fixes applied:
  - T1 omitted_symptoms is NOT leaked to T2/T4 Nurse (fixed in care_agent.py)
  - Cross-model routing verified: odd calls → patient, even calls → nurse
  - Same seed ensures identical early_termination/video_cooperation rolls

Usage:
    export $(cat .env | xargs) && python -m src.experiments.nurse_probing_large \
        --run 20260219_050602_Padcev___Pembrolizumab_10pt_126d --gpu 4
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.care_agent import CareAgent
from src.agents.llm_client import generate_json as gemini_generate_json
from src.engine.mood import MoodState, compute_interaction_quality
from src.engine.sampler import Sampler
from src.experiments.compare_care_models import (
    load_medgemma,
    medgemma_generate_json,
    load_patient_and_day,
)


# ─── Test Matrix ──────────────────────────────────────────────

PATIENT_DAY_MATRIX = [
    ("PT-001", 73),   # 3 AEs, max G2 (neuropathy, fatigue, rash)
    ("PT-002", 45),   # 2 AEs, max G4 (neuropathy, hyperglycemia)
    ("PT-004", 48),   # 3 AEs, max G3 (alopecia, nausea, neuropathy)
    ("PT-005", 50),   # 4 AEs, max G2 (neuropathy, rash, alopecia, fatigue)
    ("PT-006", 42),   # 3 AEs, max G3 (appetite, diarrhea, neuropathy)
    ("PT-007", 59),   # 4 AEs, max G3 (fatigue, neuropathy, appetite, rash)
    ("PT-008", 104),  # 2 AEs, max G4 (fatigue, neuropathy)
    ("PT-009", 32),   # 3 AEs, max G2 (appetite, pruritus, rash)
]

SCENARIOS = {
    "cooperative": {
        "label": "Cooperative Baseline",
        "description": "Open patient, high trust — calibration baseline",
        "mood_override": {
            "anxiety": 0.20,
            "depression": 0.15,
            "irritability": 0.10,
            "energy": 0.70,
            "cognitive_clarity": 0.80,
            "trust_in_ai": 0.75,
            "defensiveness": 0.15,
        },
    },
    "stoic": {
        "label": "Stoic Minimizer",
        "description": "Hides symptoms, short answers, avoids showing on camera",
        "mood_override": {
            "anxiety": 0.15,
            "depression": 0.20,
            "irritability": 0.30,
            "energy": 0.50,
            "cognitive_clarity": 0.70,
            "trust_in_ai": 0.25,
            "defensiveness": 0.70,
        },
    },
    "hostile": {
        "label": "Hostile / Irritable",
        "description": "May hang up early, aggressive, rejects probing",
        "mood_override": {
            "anxiety": 0.20,
            "depression": 0.15,
            "irritability": 0.75,
            "energy": 0.30,
            "cognitive_clarity": 0.65,
            "trust_in_ai": 0.12,
            "defensiveness": 0.55,
        },
    },
    "shame": {
        "label": "Shame-Avoidant",
        "description": "Avoids skin/urinary topics, deflects specific AEs",
        "mood_override": {
            "anxiety": 0.50,
            "depression": 0.45,
            "irritability": 0.20,
            "energy": 0.40,
            "cognitive_clarity": 0.60,
            "trust_in_ai": 0.30,
            "defensiveness": 0.65,
        },
    },
}


# ─── Cross-Model Dispatch ─────────────────────────────────────

def make_cross_model_fn(patient_fn, nurse_fn):
    """Route T1/T3 to patient_fn and T2/T4 to nurse_fn."""
    state = {"call_count": 0}

    def hybrid_fn(system_prompt, user_prompt, **kwargs):
        state["call_count"] += 1
        kwargs.pop("model", None)
        if state["call_count"] % 2 == 0:
            return nurse_fn(system_prompt, user_prompt, **kwargs)
        return patient_fn(system_prompt, user_prompt, **kwargs)

    return hybrid_fn


# ─── Single Run ───────────────────────────────────────────────

def run_single(
    patient: dict,
    rule_set: dict,
    day_data: dict,
    last_hr: dict | None,
    scenario_name: str,
    nurse_fn,
    nurse_label: str,
    seed: int,
) -> dict:
    """Execute one care call and return scores + metadata."""
    scenario = SCENARIOS[scenario_name]
    day = day_data["day"]
    pid = patient.get("patient_id", "?")
    persona_type = patient.get("persona", {}).get("type", "stoic_minimizer")

    mood = MoodState(persona_type=persona_type, seed=seed)
    for dim, val in scenario["mood_override"].items():
        if dim in mood.state:
            mood.state[dim] = val

    sampler = Sampler(seed=seed)
    agent = CareAgent(
        patient=patient,
        rule_set=rule_set,
        mood=mood,
        sampler=sampler,
        model=nurse_label,
    )

    cross_fn = make_cross_model_fn(gemini_generate_json, nurse_fn)

    t0 = time.time()
    with patch("src.agents.care_agent.generate_json", side_effect=cross_fn):
        care_record = agent.conduct_video_call(
            day=day,
            day_result=day_data,
            day_results=[day_data],
            last_hospital_record=last_hr,
        )
    elapsed = time.time() - t0

    scores = score_extraction(care_record, day_data)
    scores["elapsed_sec"] = round(elapsed, 2)
    scores["patient_id"] = pid
    scores["day"] = day
    scores["scenario"] = scenario_name
    scores["nurse_model"] = nurse_label

    return {
        "scores": scores,
        "care_record": care_record,
        "elapsed_sec": round(elapsed, 2),
    }


# ─── Scoring ──────────────────────────────────────────────────

def score_extraction(care_record: dict, day_data: dict) -> dict:
    gt_aes = day_data.get("objective", {}).get("active_aes", [])
    gt_ae_names = {ae.get("ae", "").lower() for ae in gt_aes}
    gt_max_grade = max((ae.get("grade", 0) for ae in gt_aes), default=0)
    gt_ae_visual = sum(1 for ae in gt_aes if ae.get("visual"))

    turns = care_record.get("turns", [])
    terminated = care_record.get("terminated_early", False)

    t1 = next((t["content"] for t in turns if t.get("turn") == 1), {})
    reported_t1 = t1.get("reported_symptoms", [])
    omitted_t1 = t1.get("omitted_symptoms", [])
    n_reported_t1 = len(reported_t1)
    n_omitted_t1 = len(omitted_t1)

    t2 = next((t["content"] for t in turns if t.get("turn") == 2), {})
    questions = t2.get("questions", [])
    visual_req = t2.get("visual_request", {})
    targeted_aes = {q.get("target_ae", "").lower() for q in questions if q.get("target_ae")}

    probe_hit_rate = 0.0
    if gt_ae_names and targeted_aes:
        hits = sum(1 for g in gt_ae_names if any(g in t or t in g for t in targeted_aes))
        probe_hit_rate = hits / len(gt_ae_names)

    t3 = next((t["content"] for t in turns if t.get("turn") == 3), {})
    new_info_revealed = t3.get("new_info_revealed", False)
    responses = t3.get("responses", [])
    n_revealed = sum(1 for r in responses if r.get("revealed_symptom"))
    honesty_levels = [r.get("honesty_level", "evasive") for r in responses]
    cooperated_visual = t3.get("visual_response", {}).get("cooperated", False)

    honesty_score = 0.0
    if honesty_levels:
        _hmap = {"full": 1.0, "partial": 0.5, "evasive": 0.2, "denied": 0.0}
        honesty_score = sum(_hmap.get(h, 0.2) for h in honesty_levels) / len(honesty_levels)

    detection = care_record.get("detection", {})
    detected_aes = {a.lower() for a in detection.get("aes_detected", [])}

    ae_recall = 0.0
    if gt_ae_names:
        ae_recall = sum(1 for g in gt_ae_names if any(g in d or d in g for d in detected_aes)) / len(gt_ae_names)
    else:
        ae_recall = 1.0

    assessment = care_record.get("nurse_assessment", {})
    severity = assessment.get("severity_level", "green")
    sev_map = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
    expected_sev = 0 if gt_max_grade == 0 else 1 if gt_max_grade <= 2 else 2 if gt_max_grade == 3 else 3
    sev_accuracy = 1.0 - abs(sev_map.get(severity, 0) - expected_sev) / 3.0

    actions = care_record.get("actions", [])
    action_names = [a.get("action", "") for a in actions]
    over_escalation = any(
        a in ("recommend_hospital_visit", "escalate_to_physician") for a in action_names
    ) and gt_max_grade <= 2
    under_escalation = any(a == "no_action" for a in action_names) and gt_max_grade >= 3
    has_conmed = any(a == "recommend_conmed" for a in action_names)
    has_early_visit = any(a == "recommend_early_visit" for a in action_names)

    json_errors = sum(
        1 for t in turns if t.get("content", {}).get("_parse_error")
    )

    extraction_score = (
        ae_recall * 0.30
        + probe_hit_rate * 0.15
        + honesty_score * 0.15
        + (0.10 if new_info_revealed else 0.0)
        + (0.10 if cooperated_visual else 0.0)
        + sev_accuracy * 0.20
    )

    return {
        "terminated_early": terminated,
        "n_turns": len(turns),
        "t1_reported": n_reported_t1,
        "t1_omitted": n_omitted_t1,
        "t2_n_questions": len(questions),
        "t2_probe_hit_rate": round(probe_hit_rate, 3),
        "t2_visual_requested": visual_req.get("requested", False),
        "t3_new_info_revealed": new_info_revealed,
        "t3_n_revealed": n_revealed,
        "t3_honesty_score": round(honesty_score, 3),
        "t3_visual_cooperated": cooperated_visual,
        "t4_ae_recall": round(ae_recall, 3),
        "t4_severity": severity,
        "t4_severity_accuracy": round(sev_accuracy, 3),
        "t4_n_detected": len(detected_aes),
        "t4_over_escalation": over_escalation,
        "t4_under_escalation": under_escalation,
        "t4_has_conmed": has_conmed,
        "t4_has_early_visit": has_early_visit,
        "json_errors": json_errors,
        "extraction_score": round(extraction_score, 3),
        "gt_ae_count": len(gt_ae_names),
        "gt_max_grade": gt_max_grade,
        "gt_ae_visual": gt_ae_visual,
    }


# ─── Statistics ───────────────────────────────────────────────

def mean_std(vals: list[float]) -> tuple[float, float]:
    if not vals:
        return 0.0, 0.0
    n = len(vals)
    mu = sum(vals) / n
    if n < 2:
        return mu, 0.0
    var = sum((x - mu) ** 2 for x in vals) / (n - 1)
    return mu, math.sqrt(var)


def ci_95(vals: list[float]) -> tuple[float, float, float]:
    """Mean and 95% CI (t-distribution approximation for small n)."""
    mu, sd = mean_std(vals)
    n = len(vals)
    if n < 2:
        return mu, mu, mu
    t_crit = {2: 12.71, 3: 4.30, 4: 3.18, 5: 2.78, 6: 2.57, 7: 2.45, 8: 2.36,
              9: 2.31, 10: 2.26, 12: 2.18, 15: 2.14, 20: 2.09, 30: 2.04}
    tc = t_crit.get(n, 1.96)
    margin = tc * sd / math.sqrt(n)
    return mu, mu - margin, mu + margin


def welch_t_test(a: list[float], b: list[float]) -> tuple[float, float]:
    """Two-sample Welch's t-test. Returns (t_stat, approx p-value)."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return 0.0, 1.0
    mu1, s1 = mean_std(a)
    mu2, s2 = mean_std(b)
    se = math.sqrt(s1**2 / n1 + s2**2 / n2)
    if se < 1e-10:
        return 0.0, 1.0
    t_stat = (mu1 - mu2) / se
    df_num = (s1**2 / n1 + s2**2 / n2) ** 2
    df_den = (s1**2 / n1) ** 2 / (n1 - 1) + (s2**2 / n2) ** 2 / (n2 - 1)
    df = df_num / df_den if df_den > 0 else 1
    p = _approx_t_to_p(abs(t_stat), df)
    return round(t_stat, 3), round(p, 4)


def _approx_t_to_p(t: float, df: float) -> float:
    """Rough two-tailed p-value approximation using normal for df > 30."""
    if df > 30:
        z = t
        return 2 * (1 - _norm_cdf(z))
    x = df / (df + t**2)
    p_one = 0.5 * _incomplete_beta(df / 2, 0.5, x)
    return 2 * p_one


def _norm_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _incomplete_beta(a: float, b: float, x: float, steps: int = 200) -> float:
    """Numerical regularized incomplete beta via simple trapezoidal rule."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    dt = x / steps
    total = 0.0
    for i in range(steps):
        t = (i + 0.5) * dt
        total += t ** (a - 1) * (1 - t) ** (b - 1) * dt
    full = math.gamma(a) * math.gamma(b) / math.gamma(a + b)
    return min(total / full, 1.0)


# ─── Display ──────────────────────────────────────────────────

def display_results(all_scores: list[dict]):
    W = 100
    SEP = "=" * W

    gemini_all = [s for s in all_scores if "gemini" in s["nurse_model"]]
    medgemma_all = [s for s in all_scores if "medgemma" in s["nurse_model"]]

    print(f"\n\n{SEP}")
    print(f"{'LARGE-SCALE NURSE PROBING: Gemini vs MedGemma':^{W}}")
    print(f"{'(Patient always Gemini, information leakage fixed)':^{W}}")
    print(f"{SEP}")

    n_patients = len(set(s["patient_id"] for s in all_scores))
    n_scenarios = len(set(s["scenario"] for s in all_scores))
    print(f"\n  Patients: {n_patients}  |  Scenarios: {n_scenarios}  |  "
          f"Total calls: {len(all_scores)} ({len(gemini_all)} Gemini + {len(medgemma_all)} MedGemma)")

    key_metrics = [
        ("extraction_score", "Extraction Score (composite)"),
        ("t4_ae_recall", "AE Recall (T4)"),
        ("t2_probe_hit_rate", "Probe Hit Rate (T2)"),
        ("t3_honesty_score", "Honesty Score (T3)"),
        ("t4_severity_accuracy", "Severity Accuracy (T4)"),
        ("elapsed_sec", "Latency (sec)"),
    ]

    # ── Overall comparison ──
    print(f"\n{'─'*W}")
    print(f"  OVERALL (all scenarios combined)")
    print(f"{'─'*W}")
    print(f"  {'Metric':<35} {'Gemini':>20} {'MedGemma':>20}  {'p-value':>8}")
    print(f"  {'─'*88}")

    for key, label in key_metrics:
        gv = [s[key] for s in gemini_all if key in s and isinstance(s[key], (int, float))]
        mv = [s[key] for s in medgemma_all if key in s and isinstance(s[key], (int, float))]
        gm, gs = mean_std(gv)
        mm, ms = mean_std(mv)
        _, p = welch_t_test(gv, mv)
        sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
        print(f"  {label:<35} {gm:>7.3f} ±{gs:<7.3f}   {mm:>7.3f} ±{ms:<7.3f}  {p:>7.4f} {sig}")

    bool_metrics = [
        ("terminated_early", "Early Termination Rate"),
        ("t3_new_info_revealed", "New Info Revealed (T3)"),
        ("t3_visual_cooperated", "Visual Cooperation (T3)"),
        ("t4_over_escalation", "Over-escalation (T4)"),
        ("t4_under_escalation", "Under-escalation (T4)"),
        ("t4_has_conmed", "Conmed Recommended (T4)"),
        ("t4_has_early_visit", "Early Visit Recommended (T4)"),
    ]

    print(f"\n  {'Boolean Metric':<35} {'Gemini %':>12} {'MedGemma %':>12}")
    print(f"  {'─'*62}")
    for key, label in bool_metrics:
        gv = [1 if s.get(key) else 0 for s in gemini_all]
        mv = [1 if s.get(key) else 0 for s in medgemma_all]
        gp = sum(gv) / len(gv) * 100 if gv else 0
        mp = sum(mv) / len(mv) * 100 if mv else 0
        print(f"  {label:<35} {gp:>10.1f}%  {mp:>10.1f}%")

    json_err_g = sum(s.get("json_errors", 0) for s in gemini_all)
    json_err_m = sum(s.get("json_errors", 0) for s in medgemma_all)
    print(f"\n  JSON parse errors:  Gemini={json_err_g}  MedGemma={json_err_m}")

    # ── Per-scenario breakdown ──
    for sc_name in SCENARIOS:
        sc = SCENARIOS[sc_name]
        g_sc = [s for s in gemini_all if s["scenario"] == sc_name]
        m_sc = [s for s in medgemma_all if s["scenario"] == sc_name]
        if not g_sc and not m_sc:
            continue

        print(f"\n{'─'*W}")
        print(f"  SCENARIO: {sc['label']}  (n={len(g_sc)} per model)")
        print(f"  {sc['description']}")
        mood = sc['mood_override']
        print(f"  mood: def={mood['defensiveness']:.2f} irr={mood['irritability']:.2f} "
              f"trust={mood['trust_in_ai']:.2f}")
        print(f"{'─'*W}")
        print(f"  {'Metric':<35} {'Gemini':>20} {'MedGemma':>20}  {'p-value':>8}")
        print(f"  {'─'*88}")

        for key, label in key_metrics:
            gv = [s[key] for s in g_sc if key in s and isinstance(s[key], (int, float))]
            mv = [s[key] for s in m_sc if key in s and isinstance(s[key], (int, float))]
            gm, gs = mean_std(gv)
            mm, ms = mean_std(mv)
            _, p = welch_t_test(gv, mv)
            sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
            print(f"  {label:<35} {gm:>7.3f} ±{gs:<7.3f}   {mm:>7.3f} ±{ms:<7.3f}  {p:>7.4f} {sig}")

    # ── Per-patient summary ──
    print(f"\n{'─'*W}")
    print(f"  PER-PATIENT EXTRACTION SCORES (averaged over scenarios)")
    print(f"{'─'*W}")
    print(f"  {'Patient':<10} {'AE Profile':<35} {'Gemini':>10} {'MedGemma':>10} {'Delta':>8}")
    print(f"  {'─'*76}")

    patient_ids = sorted(set(s["patient_id"] for s in all_scores))
    for pid in patient_ids:
        g_p = [s for s in gemini_all if s["patient_id"] == pid]
        m_p = [s for s in medgemma_all if s["patient_id"] == pid]
        gm = sum(s["extraction_score"] for s in g_p) / len(g_p) if g_p else 0
        mm = sum(s["extraction_score"] for s in m_p) / len(m_p) if m_p else 0
        n_ae = g_p[0]["gt_ae_count"] if g_p else 0
        max_g = g_p[0]["gt_max_grade"] if g_p else 0
        profile = f"{n_ae} AEs, max G{max_g}"
        delta = gm - mm
        winner = "G" if delta > 0.02 else "M" if delta < -0.02 else "="
        print(f"  {pid:<10} {profile:<35} {gm:>10.3f} {mm:>10.3f} {delta:>+7.3f} {winner}")

    print(f"\n{SEP}")


def display_improvement_analysis(all_scores: list[dict]):
    """Identify specific MedGemma weaknesses and improvement suggestions."""
    W = 100
    SEP = "=" * W

    gemini_all = [s for s in all_scores if "gemini" in s["nurse_model"]]
    medgemma_all = [s for s in all_scores if "medgemma" in s["nurse_model"]]

    print(f"\n{SEP}")
    print(f"{'MEDGEMMA IMPROVEMENT ANALYSIS':^{W}}")
    print(f"{SEP}")

    dimensions = {
        "t4_ae_recall": "AE Detection Recall",
        "t2_probe_hit_rate": "Targeted Probing",
        "t3_honesty_score": "Patient Honesty Elicitation",
        "t4_severity_accuracy": "Severity Assessment",
        "extraction_score": "Overall Extraction",
    }

    print(f"\n  {'Dimension':<30} {'Gap (G-M)':>10} {'Gemini μ':>10} {'MedGemma μ':>10} {'Assessment':>15}")
    print(f"  {'─'*78}")

    gaps = {}
    for key, label in dimensions.items():
        gv = [s[key] for s in gemini_all if isinstance(s.get(key), (int, float))]
        mv = [s[key] for s in medgemma_all if isinstance(s.get(key), (int, float))]
        gm, _ = mean_std(gv)
        mm, _ = mean_std(mv)
        gap = gm - mm
        gaps[key] = gap

        if gap > 0.10:
            assessment = "CRITICAL GAP"
        elif gap > 0.05:
            assessment = "Moderate Gap"
        elif gap > 0.02:
            assessment = "Minor Gap"
        elif gap > -0.02:
            assessment = "Comparable"
        else:
            assessment = "MedGemma Better"
        print(f"  {label:<30} {gap:>+9.3f}  {gm:>10.3f} {mm:>10.3f} {assessment:>15}")

    # Per-scenario gaps
    print(f"\n  Per-Scenario Extraction Score Gap (Gemini - MedGemma):")
    print(f"  {'─'*60}")
    for sc_name in SCENARIOS:
        g_sc = [s["extraction_score"] for s in gemini_all if s["scenario"] == sc_name]
        m_sc = [s["extraction_score"] for s in medgemma_all if s["scenario"] == sc_name]
        if g_sc and m_sc:
            gm, _ = mean_std(g_sc)
            mm, _ = mean_std(m_sc)
            bar_len = int(abs(gm - mm) * 100)
            direction = "▶" * bar_len if gm > mm else "◀" * bar_len
            label = f"Gemini +{gm-mm:.3f}" if gm > mm else f"MedGemma +{mm-gm:.3f}"
            print(f"    {SCENARIOS[sc_name]['label']:<25} {direction} {label}")

    # MedGemma JSON robustness
    mm_parse_errors = sum(s.get("json_errors", 0) for s in medgemma_all)
    mm_total = len(medgemma_all) * 4
    error_rate = mm_parse_errors / mm_total * 100 if mm_total else 0

    print(f"\n  MedGemma JSON Robustness:")
    print(f"    Parse errors: {mm_parse_errors}/{mm_total} turns ({error_rate:.1f}%)")

    # Actionable recommendations
    print(f"\n{'─'*W}")
    print(f"  ACTIONABLE IMPROVEMENT RECOMMENDATIONS FOR MEDGEMMA")
    print(f"{'─'*W}")

    recs = []
    if gaps.get("t4_severity_accuracy", 0) > 0.05:
        recs.append(
            "SEVERITY CALIBRATION: MedGemma tends to misjudge AE severity. "
            "Fine-tune on CTCAE grading examples with explicit grade definitions "
            "in the system prompt. Add few-shot examples mapping symptoms → grades."
        )
    if gaps.get("t2_probe_hit_rate", 0) > 0.05:
        recs.append(
            "TARGETED PROBING: MedGemma's questions less often target actual AEs. "
            "Include drug-specific AE checklists in system prompt and train on "
            "clinical interview transcripts where nurses probe systematically."
        )
    if gaps.get("t3_honesty_score", 0) > 0.05:
        recs.append(
            "RAPPORT BUILDING: MedGemma elicits less honest responses. "
            "Fine-tune on empathetic dialogue datasets. Add motivational "
            "interviewing techniques to the nurse prompt template."
        )
    if gaps.get("t4_ae_recall", 0) > 0.10:
        recs.append(
            "AE DETECTION: Significant recall gap. MedGemma misses AEs that "
            "Gemini catches. Consider RAG with drug label AE sections, or "
            "structured output enforcement to reduce missed detections."
        )
    if error_rate > 5:
        recs.append(
            f"JSON RELIABILITY: {error_rate:.1f}% parse error rate. "
            "Use constrained decoding (e.g., outlines/guidance) or "
            "add JSON schema validation in the generation loop with retry."
        )
    if mm_parse_errors == 0 and not recs:
        recs.append("MedGemma performs comparably to Gemini on this task set.")

    for i, rec in enumerate(recs, 1):
        print(f"\n  {i}. {rec}")

    print(f"\n{SEP}\n")


# ─── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Large-Scale Nurse Probing Experiment")
    parser.add_argument("--run", required=True, help="Run ID")
    parser.add_argument("--gpu", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenarios", nargs="+",
                        default=list(SCENARIOS.keys()),
                        choices=list(SCENARIOS.keys()))
    parser.add_argument("--patients", nargs="+", default=None,
                        help="Limit to specific patient IDs")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    matrix = PATIENT_DAY_MATRIX
    if args.patients:
        matrix = [(p, d) for p, d in matrix if p in args.patients]

    total = len(matrix) * len(args.scenarios) * 2
    print(f"{'='*80}")
    print(f"  Large-Scale Nurse Probing Experiment")
    print(f"  Patient model: Gemini 2.0 Flash (fixed)")
    print(f"  Nurse models:  Gemini 2.0 Flash vs MedGemma 1.5 4B")
    print(f"  Patients: {len(matrix)}  Scenarios: {len(args.scenarios)}")
    print(f"  Total calls: {total}")
    print(f"  Info leak fix: omitted_symptoms filtered from Nurse prompts")
    print(f"{'='*80}")

    load_medgemma(gpu_id=args.gpu)

    all_scores: list[dict] = []
    all_records: list[dict] = []
    completed = 0

    for pid, day in matrix:
        print(f"\n{'━'*80}")
        print(f"  Loading {pid} day {day}...")

        try:
            patient, rule_set, day_data, last_hr = load_patient_and_day(args.run, pid, day)
        except Exception as e:
            print(f"  ⚠ Skip {pid}: {e}")
            continue

        gt_aes = day_data.get("objective", {}).get("active_aes", [])
        ae_summary = ", ".join(f"{a.get('ae','?')} G{a.get('grade','?')}" for a in gt_aes)
        print(f"  GT: {len(gt_aes)} AEs [{ae_summary}]")

        for sc_name in args.scenarios:
            sc = SCENARIOS[sc_name]
            seed = args.seed + hash(f"{pid}_{sc_name}") % 10000

            for nurse_label, nurse_fn in [
                ("gemini-2.0-flash", gemini_generate_json),
                ("medgemma-1.5-4b-it", medgemma_generate_json),
            ]:
                completed += 1
                short = "Gemini" if "gemini" in nurse_label else "MedGemma"
                print(f"\n  [{completed}/{total}] {pid} d{day} {sc['label'][:15]:>15} × {short:<10}", end="", flush=True)

                try:
                    result = run_single(
                        patient, rule_set, day_data, last_hr,
                        sc_name, nurse_fn, nurse_label, seed,
                    )
                    all_scores.append(result["scores"])
                    all_records.append({
                        "patient_id": pid, "day": day, "scenario": sc_name,
                        "nurse_model": nurse_label,
                        "scores": result["scores"],
                        "care_record": result["care_record"],
                        "elapsed_sec": result["elapsed_sec"],
                    })
                    es = result["scores"]["extraction_score"]
                    ar = result["scores"]["t4_ae_recall"]
                    et = result["scores"]["terminated_early"]
                    print(f"  → score={es:.3f} recall={ar:.3f} early_term={et} ({result['elapsed_sec']:.1f}s)")
                except Exception as e:
                    print(f"  ⚠ ERROR: {e}")
                    import traceback; traceback.print_exc()

    # ── Display ──
    if all_scores:
        display_results(all_scores)
        display_improvement_analysis(all_scores)

    # ── Save ──
    out_path = Path(args.output) if args.output else (
        PROJECT_ROOT / "data" / "experiments" / f"nurse_probing_large_{len(matrix)}pt_{len(args.scenarios)}sc.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    save_data = {
        "experiment": "nurse_probing_large_scale",
        "info_leak_fix": True,
        "patient_model": "gemini-2.0-flash",
        "nurse_models": ["gemini-2.0-flash", "medgemma-1.5-4b-it"],
        "n_patients": len(set(s["patient_id"] for s in all_scores)),
        "n_scenarios": len(set(s["scenario"] for s in all_scores)),
        "total_calls": len(all_scores),
        "patient_day_matrix": PATIENT_DAY_MATRIX,
        "scenarios": {k: v["mood_override"] for k, v in SCENARIOS.items()},
        "all_scores": all_scores,
        "records": all_records,
    }

    with open(out_path, "w") as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved → {out_path}")


if __name__ == "__main__":
    main()
