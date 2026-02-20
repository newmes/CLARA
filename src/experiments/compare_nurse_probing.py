"""Cross-Model Nurse Probing Experiment.

Tests which model (Gemini vs MedGemma) is better at extracting information
from reluctant/defensive patients by fixing the Patient model (Gemini)
and varying only the Nurse model.

Three patient mood scenarios:
  - Stoic:   high defensiveness, low trust → hides symptoms, minimal answers
  - Hostile: high irritability, very low trust → may hang up early
  - Shame:   high defensiveness on specific topics → avoids skin/urinary symptoms

Usage:
    python -m src.experiments.compare_nurse_probing \
        --run 20260219_050602_Padcev___Pembrolizumab_10pt_126d \
        --patient PT-001 --day 73 --gpu 4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import textwrap
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.care_agent import CareAgent
from src.agents.llm_client import generate_json as gemini_generate_json
from src.engine.mood import MoodState, compute_interaction_quality
from src.engine.sampler import Sampler

# Reuse MedGemma loader from compare_care_models
from src.experiments.compare_care_models import (
    load_medgemma,
    medgemma_generate_json,
    load_patient_and_day,
    _cdisc_ae_to_internal,
)

# ─── Mood Scenarios ───────────────────────────────────────────

SCENARIOS = {
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
        "description": "May hang up early, very short/aggressive, rejects probing",
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


# ─── Cross-Model LLM Dispatch ────────────────────────────────

def make_cross_model_fn(patient_fn, nurse_fn):
    """Create a generate_json function that routes Patient turns to one model
    and Nurse turns to another.

    CareAgent calls generate_json in order: T1(patient), T2(nurse), T3(patient), T4(nurse).
    We override the `model` kwarg to avoid passing local model names to the Gemini API.
    """
    state = {"call_count": 0}

    def hybrid_fn(system_prompt, user_prompt, **kwargs):
        state["call_count"] += 1
        n = state["call_count"]
        is_nurse = n % 2 == 0
        kwargs.pop("model", None)
        if is_nurse:
            return nurse_fn(system_prompt, user_prompt, **kwargs)
        return patient_fn(system_prompt, user_prompt, **kwargs)

    return hybrid_fn


# ─── Run a single scenario with a specific nurse model ────────

def run_scenario(
    scenario_name: str,
    nurse_name: str,
    nurse_fn,
    patient: dict,
    rule_set: dict,
    day_data: dict,
    last_hr: dict | None,
    seed: int,
) -> tuple[dict, float, dict]:
    """Run one care call with fixed Gemini-patient and specified nurse model.

    Returns: (care_record, elapsed_sec, quality_metrics)
    """
    scenario = SCENARIOS[scenario_name]
    day = day_data["day"]
    persona_type = patient.get("persona", {}).get("type", "stoic_minimizer")

    mood = MoodState(persona_type=persona_type, seed=seed)
    for dim, val in scenario["mood_override"].items():
        if dim in mood.state:
            mood.state[dim] = val

    quality = compute_interaction_quality(mood)

    # Use fixed seed so early_termination roll is consistent across nurse models
    sampler = Sampler(seed=seed)

    agent = CareAgent(
        patient=patient,
        rule_set=rule_set,
        mood=mood,
        sampler=sampler,
        model=nurse_name,
    )

    cross_fn = make_cross_model_fn(gemini_generate_json, nurse_fn)

    print(f"\n  [{scenario['label']}] Nurse={nurse_name}")
    print(f"    Mood: def={mood.state['defensiveness']:.2f} irr={mood.state['irritability']:.2f} "
          f"trust={mood.state['trust_in_ai']:.2f} energy={mood.state['energy']:.2f}")
    print(f"    Quality: under_report={quality['under_report_prob']:.2f} "
          f"early_term={quality['early_termination_prob']:.2f} "
          f"video_coop={quality['video_cooperation']:.2f}")

    t0 = time.time()
    with patch("src.agents.care_agent.generate_json", side_effect=cross_fn):
        care_record = agent.conduct_video_call(
            day=day,
            day_result=day_data,
            day_results=[day_data],
            last_hospital_record=last_hr,
        )
    elapsed = time.time() - t0

    return care_record, elapsed, quality


# ─── Extraction Scoring ──────────────────────────────────────

def score_extraction(care_record: dict, day_data: dict) -> dict:
    """Score how well the nurse extracted information from the patient."""
    gt_aes = day_data.get("objective", {}).get("active_aes", [])
    gt_ae_names = {ae.get("ae", "").lower() for ae in gt_aes}
    gt_max_grade = max((ae.get("grade", 0) for ae in gt_aes), default=0)

    turns = care_record.get("turns", [])
    terminated = care_record.get("terminated_early", False)

    # T1: what did patient initially report vs omit?
    t1 = next((t["content"] for t in turns if t.get("turn") == 1), {})
    reported_t1 = t1.get("reported_symptoms", [])
    omitted_t1 = t1.get("omitted_symptoms", [])
    video_visible_t1 = t1.get("video_visible", [])

    n_reported_t1 = len(reported_t1)
    n_omitted_t1 = len(omitted_t1)

    # T2: nurse's probing strategy quality
    t2 = next((t["content"] for t in turns if t.get("turn") == 2), {})
    questions = t2.get("questions", [])
    visual_req = t2.get("visual_request", {})
    targeted_aes = {q.get("target_ae", "").lower() for q in questions if q.get("target_ae")}
    probe_hit_rate = 0.0
    if gt_ae_names and targeted_aes:
        hits = sum(1 for g in gt_ae_names if any(g in t or t in g for t in targeted_aes))
        probe_hit_rate = hits / len(gt_ae_names)

    # T3: did probing reveal new info?
    t3 = next((t["content"] for t in turns if t.get("turn") == 3), {})
    new_info_revealed = t3.get("new_info_revealed", False)
    responses = t3.get("responses", [])
    n_revealed = sum(1 for r in responses if r.get("revealed_symptom"))
    honesty_levels = [r.get("honesty_level", "evasive") for r in responses]
    cooperated_visual = t3.get("visual_response", {}).get("cooperated", False)

    # T4: final detection
    detection = care_record.get("detection", {})
    detected_aes = {a.lower() for a in detection.get("aes_detected", [])}

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

    # Composite extraction score
    extraction_score = (
        ae_recall * 0.35
        + probe_hit_rate * 0.20
        + (0.15 if new_info_revealed else 0.0)
        + (0.10 if cooperated_visual else 0.0)
        + sev_accuracy * 0.20
    )

    return {
        "terminated_early": terminated,
        "n_turns": len(turns),
        # T1
        "t1_reported": n_reported_t1,
        "t1_omitted": n_omitted_t1,
        # T2
        "t2_n_questions": len(questions),
        "t2_probe_hit_rate": round(probe_hit_rate, 2),
        "t2_visual_requested": visual_req.get("requested", False),
        # T3
        "t3_new_info_revealed": new_info_revealed,
        "t3_n_revealed_symptoms": n_revealed,
        "t3_honesty_levels": honesty_levels,
        "t3_visual_cooperated": cooperated_visual,
        # T4
        "t4_ae_recall": round(ae_recall, 2),
        "t4_severity": severity,
        "t4_severity_accuracy": round(sev_accuracy, 2),
        "t4_actions": action_names,
        "t4_n_detected": len(detected_aes),
        # Composite
        "extraction_score": round(extraction_score, 2),
        # Reference
        "gt_ae_count": len(gt_ae_names),
        "gt_max_grade": gt_max_grade,
    }


# ─── Display ─────────────────────────────────────────────────

def display_results(all_results: dict, gt_aes: list):
    W = 82
    SEP = "═" * W

    print(f"\n\n{SEP}")
    print(f"{'NURSE PROBING EXPERIMENT — Cross-Model Comparison':^{W}}")
    print(f"{'Patient: always Gemini | Nurse: Gemini vs MedGemma':^{W}}")
    print(f"{SEP}")

    print(f"\n  Ground Truth AEs: {len(gt_aes)}")
    for ae in gt_aes:
        print(f"    - {ae.get('ae','?')} Grade {ae.get('grade','?')} ({ae.get('days_active','?')}d)")

    for scenario_name, scenario_data in all_results.items():
        sc = SCENARIOS[scenario_name]
        mood = sc["mood_override"]

        print(f"\n{SEP}")
        print(f"  SCENARIO: {sc['label']}")
        print(f"  {sc['description']}")
        print(f"  mood: def={mood['defensiveness']:.2f} irr={mood['irritability']:.2f} "
              f"trust={mood['trust_in_ai']:.2f} energy={mood['energy']:.2f}")
        print(f"{SEP}")

        gemini_sc = scenario_data.get("gemini_nurse", {})
        medgemma_sc = scenario_data.get("medgemma_nurse", {})
        gs = gemini_sc.get("scores", {})
        ms = medgemma_sc.get("scores", {})

        print(f"\n  {'Metric':<35} {'Gemini-Nurse':>14} {'MedGemma-Nurse':>14}")
        print(f"  {'─'*65}")

        rows = [
            ("terminated_early", gs.get("terminated_early"), ms.get("terminated_early")),
            ("n_turns", gs.get("n_turns"), ms.get("n_turns")),
            ("", "", ""),
            ("T1: reported / omitted",
             f"{gs.get('t1_reported',0)} / {gs.get('t1_omitted',0)}",
             f"{ms.get('t1_reported',0)} / {ms.get('t1_omitted',0)}"),
            ("", "", ""),
            ("T2: questions asked", gs.get("t2_n_questions"), ms.get("t2_n_questions")),
            ("T2: probe hit rate", gs.get("t2_probe_hit_rate"), ms.get("t2_probe_hit_rate")),
            ("T2: visual requested", gs.get("t2_visual_requested"), ms.get("t2_visual_requested")),
            ("", "", ""),
            ("T3: new info revealed", gs.get("t3_new_info_revealed"), ms.get("t3_new_info_revealed")),
            ("T3: symptoms revealed", gs.get("t3_n_revealed_symptoms"), ms.get("t3_n_revealed_symptoms")),
            ("T3: visual cooperated", gs.get("t3_visual_cooperated"), ms.get("t3_visual_cooperated")),
            ("T3: honesty levels",
             _fmt_honesty(gs.get("t3_honesty_levels", [])),
             _fmt_honesty(ms.get("t3_honesty_levels", []))),
            ("", "", ""),
            ("T4: AE recall", gs.get("t4_ae_recall"), ms.get("t4_ae_recall")),
            ("T4: severity", gs.get("t4_severity"), ms.get("t4_severity")),
            ("T4: severity accuracy", gs.get("t4_severity_accuracy"), ms.get("t4_severity_accuracy")),
            ("T4: AEs detected", gs.get("t4_n_detected"), ms.get("t4_n_detected")),
            ("T4: actions", _fmt_actions(gs.get("t4_actions", [])), _fmt_actions(ms.get("t4_actions", []))),
            ("", "", ""),
            ("EXTRACTION SCORE", gs.get("extraction_score"), ms.get("extraction_score")),
            ("latency (sec)",
             f"{gemini_sc.get('elapsed_sec',0):.1f}",
             f"{medgemma_sc.get('elapsed_sec',0):.1f}"),
        ]

        for label, gv, mv in rows:
            if not label:
                continue
            print(f"  {label:<35} {str(gv):>14} {str(mv):>14}")

    # Final summary
    print(f"\n{SEP}")
    print(f"{'OVERALL SUMMARY':^{W}}")
    print(f"{SEP}")
    print(f"\n  {'Scenario':<20} {'Gemini-Nurse':>16} {'MedGemma-Nurse':>16} {'Winner':>10}")
    print(f"  {'─'*65}")
    for sn, sd in all_results.items():
        gs = sd.get("gemini_nurse", {}).get("scores", {}).get("extraction_score", 0)
        ms = sd.get("medgemma_nurse", {}).get("scores", {}).get("extraction_score", 0)
        winner = "Gemini" if gs > ms else "MedGemma" if ms > gs else "Tie"
        print(f"  {SCENARIOS[sn]['label']:<20} {gs:>16.2f} {ms:>16.2f} {winner:>10}")
    print(f"{SEP}\n")


def _fmt_honesty(levels: list) -> str:
    if not levels:
        return "—"
    short = {"full": "F", "partial": "P", "evasive": "E", "denied": "D"}
    return ",".join(short.get(l, l[:1]) for l in levels)


def _fmt_actions(actions: list) -> str:
    if not actions:
        return "—"
    short_map = {
        "no_action": "none",
        "monitor_closely": "monitor",
        "recommend_conmed": "conmed",
        "recommend_early_visit": "early_visit",
        "recommend_hospital_visit": "hospital",
        "escalate_to_physician": "escalate",
    }
    return ",".join(short_map.get(a, a) for a in actions)


# ─── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cross-Model Nurse Probing Experiment")
    parser.add_argument("--run", required=True, help="Run ID")
    parser.add_argument("--patient", default="PT-001")
    parser.add_argument("--day", type=int, default=73)
    parser.add_argument("--gpu", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenarios", nargs="+", default=["stoic", "hostile", "shame"],
                        choices=list(SCENARIOS.keys()))
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    print(f"{'='*70}")
    print(f"  Nurse Probing Experiment — Cross-Model Comparison")
    print(f"  Patient model: Gemini 2.0 Flash (fixed)")
    print(f"  Nurse models:  Gemini 2.0 Flash vs MedGemma 1.5 4B")
    print(f"  Run: {args.run}")
    print(f"  Patient: {args.patient}, Day: {args.day}")
    print(f"  Scenarios: {args.scenarios}")
    print(f"{'='*70}")

    patient, rule_set, day_data, last_hr = load_patient_and_day(
        args.run, args.patient, args.day,
    )

    gt_aes = day_data.get("objective", {}).get("active_aes", [])
    print(f"\nGround Truth: {len(gt_aes)} AEs")
    for ae in gt_aes:
        print(f"  - {ae.get('ae','?')} G{ae.get('grade','?')} ({ae.get('days_active','?')}d)")

    load_medgemma(gpu_id=args.gpu)

    all_results = {}

    for scenario_name in args.scenarios:
        print(f"\n{'━'*70}")
        print(f"  SCENARIO: {SCENARIOS[scenario_name]['label']}")
        print(f"{'━'*70}")

        scenario_results = {}

        # A: Gemini-Patient ↔ Gemini-Nurse
        cr_g, t_g, q_g = run_scenario(
            scenario_name, "gemini-2.0-flash", gemini_generate_json,
            patient, rule_set, day_data, last_hr, args.seed,
        )
        scores_g = score_extraction(cr_g, day_data)
        print(f"    → extraction_score={scores_g['extraction_score']:.2f}, "
              f"ae_recall={scores_g['t4_ae_recall']:.2f}, "
              f"terminated={scores_g['terminated_early']}")

        scenario_results["gemini_nurse"] = {
            "care_record": cr_g,
            "scores": scores_g,
            "elapsed_sec": round(t_g, 2),
            "quality": q_g,
        }

        # B: Gemini-Patient ↔ MedGemma-Nurse
        cr_m, t_m, q_m = run_scenario(
            scenario_name, "medgemma-1.5-4b-it", medgemma_generate_json,
            patient, rule_set, day_data, last_hr, args.seed,
        )
        scores_m = score_extraction(cr_m, day_data)
        print(f"    → extraction_score={scores_m['extraction_score']:.2f}, "
              f"ae_recall={scores_m['t4_ae_recall']:.2f}, "
              f"terminated={scores_m['terminated_early']}")

        scenario_results["medgemma_nurse"] = {
            "care_record": cr_m,
            "scores": scores_m,
            "elapsed_sec": round(t_m, 2),
            "quality": q_m,
        }

        all_results[scenario_name] = scenario_results

    # Display
    display_results(all_results, gt_aes)

    # Save
    out_path = Path(args.output) if args.output else (
        PROJECT_ROOT / "data" / "experiments" / f"nurse_probing_{args.patient}_d{args.day}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    save_data = {
        "experiment": "nurse_probing_cross_model",
        "patient_model": "gemini-2.0-flash",
        "nurse_models": ["gemini-2.0-flash", "medgemma-1.5-4b-it"],
        "run_id": args.run,
        "patient_id": args.patient,
        "day": args.day,
        "gt_aes": gt_aes,
        "scenarios": {
            name: {
                "mood": SCENARIOS[name]["mood_override"],
                "gemini_nurse": {
                    "scores": data["gemini_nurse"]["scores"],
                    "elapsed_sec": data["gemini_nurse"]["elapsed_sec"],
                    "care_record": data["gemini_nurse"]["care_record"],
                },
                "medgemma_nurse": {
                    "scores": data["medgemma_nurse"]["scores"],
                    "elapsed_sec": data["medgemma_nurse"]["elapsed_sec"],
                    "care_record": data["medgemma_nurse"]["care_record"],
                },
            }
            for name, data in all_results.items()
        },
    }

    with open(out_path, "w") as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved → {out_path}")


if __name__ == "__main__":
    main()
