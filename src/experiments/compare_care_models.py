"""Compare Care Agent quality: Gemini (cloud) vs MedGemma 1.5 4B (local).

Runs the same patient's day through both models and produces a side-by-side
comparison of the 4-turn video call quality.

Usage:
    python -m src.experiments.compare_care_models \
        --run 20260219_050602_Padcev___Pembrolizumab_10pt_126d \
        --patient PT-001 \
        --day 73 \
        --gpu 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import textwrap
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.care_agent import CareAgent
from src.agents.llm_client import generate_json as gemini_generate_json
from src.engine.mood import MoodState
from src.engine.sampler import Sampler

# Severity text → numeric grade mapping
_SEV_TO_GRADE = {"MILD": 1, "MODERATE": 2, "SEVERE": 3, "LIFE THREATENING": 4, "DEATH": 5}


# ─── Local MedGemma Inference ─────────────────────────────────

_local_model = None
_local_tokenizer = None


def load_medgemma(gpu_id: int = 4):
    """Load MedGemma 1.5 4B-IT onto specified GPU."""
    global _local_model, _local_tokenizer
    if _local_model is not None:
        return

    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM

    model_id = "google/medgemma-1.5-4b-it"
    device = f"cuda:{gpu_id}"

    print(f"\n{'='*60}")
    print(f"Loading {model_id} → {device}")
    print(f"{'='*60}")
    t0 = time.time()

    _local_tokenizer = AutoTokenizer.from_pretrained(model_id)
    _local_model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        device_map=device,
    )
    _local_model.eval()

    elapsed = time.time() - t0
    mem = torch.cuda.memory_allocated(gpu_id) / 1e9
    print(f"Loaded in {elapsed:.1f}s, GPU memory: {mem:.1f} GB\n")


def medgemma_generate_json(
    system_prompt: str,
    user_prompt: str,
    model: str = "",
    max_tokens: int = 8192,
    caller: str | None = None,
) -> dict:
    """Drop-in replacement for generate_json using local MedGemma."""
    import torch

    chat = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt + "\n\nRespond with valid JSON only."},
    ]
    input_text = _local_tokenizer.apply_chat_template(
        chat, tokenize=False, add_generation_prompt=True,
    )
    inputs = _local_tokenizer(input_text, return_tensors="pt").to(_local_model.device)

    t0 = time.time()
    with torch.no_grad():
        outputs = _local_model.generate(
            **inputs,
            max_new_tokens=min(max_tokens, 4096),
            temperature=0.7,
            top_p=0.9,
            do_sample=True,
        )
    elapsed = time.time() - t0

    generated = outputs[0][inputs["input_ids"].shape[1]:]
    raw = _local_tokenizer.decode(generated, skip_special_tokens=True)

    # Extract JSON from response (may be wrapped in markdown fences)
    json_str = raw.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[1]
        json_str = json_str.split("```", 1)[0]
    elif "```" in json_str:
        json_str = json_str.split("```", 1)[1]
        json_str = json_str.split("```", 1)[0]

    # Try to find the outermost JSON object
    start = json_str.find("{")
    if start >= 0:
        json_str = json_str[start:]
        # Balance braces
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

    token_count = len(generated)
    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        print(f"  [MedGemma] JSON parse failed, raw={raw[:300]}...")
        result = {"_parse_error": True, "_raw": raw[:500]}

    result["_mm_meta"] = {
        "latency_sec": round(elapsed, 2),
        "tokens_generated": token_count,
    }
    return result


# ─── Load Simulation Data ─────────────────────────────────────

def _cdisc_ae_to_internal(ae_cdisc: dict, current_day: int) -> dict:
    """Convert a CDISC-format AE record to CareAgent's internal format."""
    grade = ae_cdisc.get("_grade", _SEV_TO_GRADE.get(ae_cdisc.get("AESEV", ""), 1))
    start_day = ae_cdisc.get("AESTDAT", current_day)
    visual_aes = {"rash_maculopapular", "alopecia", "hand_foot_syndrome", "stomatitis",
                  "skin_hyperpigmentation", "nail_changes", "conjunctivitis"}
    term = ae_cdisc.get("AETERM", "")
    return {
        "ae": term,
        "ae_term": term,
        "grade": grade,
        "days_active": current_day - start_day + 1,
        "visual": term in visual_aes,
    }


def _adapt_day_data(day_data: dict) -> dict:
    """Adapt CDISC-format stored day data into the internal format CareAgent expects."""
    d = dict(day_data)
    day = d.get("day", 1)
    ae_cdisc = d.get("AE", [])

    # Build objective.active_aes from CDISC AE records
    active_aes = [_cdisc_ae_to_internal(ae, day) for ae in ae_cdisc if ae.get("AEONGO", True)]

    obj = dict(d.get("objective", {}))
    obj["active_aes"] = active_aes

    # Vitals from VS domain (stored as a flat dict with PREFIX_VSORRES keys)
    vs = d.get("VS", {})
    vitals = obj.get("vitals", {})
    if isinstance(vs, dict):
        vitals["BT"] = vs.get("TEMP_VSORRES", vitals.get("BT"))
        vitals["weight_kg"] = vs.get("WEIGHT_VSORRES", vitals.get("weight_kg"))
    obj["vitals"] = vitals

    d["objective"] = obj
    return d


def load_patient_and_day(
    run_id: str, patient_id: str, day: int
) -> tuple[dict, dict, dict, dict | None]:
    """Load patient profile, rule_set, day ground truth, and last hospital record."""
    runs_dir = PROJECT_ROOT / "data" / "runs"
    run_path = runs_dir / run_id
    if not run_path.exists():
        run_path = runs_dir / "old" / run_id
    if not run_path.exists():
        raise FileNotFoundError(f"Run not found: {run_id} (checked runs/ and runs/old/)")

    patient_file = run_path / "patients" / f"{patient_id}.json"
    with open(patient_file) as f:
        patient = json.load(f)

    rule_file = run_path / "rule_set.json"
    if not rule_file.exists():
        meta_file = run_path / "run_meta.json"
        if meta_file.exists():
            with open(meta_file) as f:
                meta = json.load(f)
            drug_name = meta.get("drug_name", "")
            indication = meta.get("indication", "")
        else:
            drug_name, indication = "", ""

        data_dir = PROJECT_ROOT / "data"
        candidates = [
            data_dir / "rule_set.json",
            data_dir / "rule_set_calibrated_ev302.json",
            data_dir / "rule_set_ep_sclc.json",
            data_dir / "rule_set_darbepoetin_sclc.json",
        ]
        rule_file = None
        for c in candidates:
            if c.exists():
                with open(c) as f:
                    rs = json.load(f)
                if drug_name and drug_name.lower() in rs.get("drug_name", "").lower():
                    rule_file = c
                    break
                if "etoposide" in drug_name.lower() and "etoposide" in rs.get("drug_name", "").lower():
                    rule_file = c
                    break
                if "darbepoetin" in drug_name.lower() and "darbepoetin" in rs.get("drug_name", "").lower():
                    rule_file = c
                    break
        if rule_file is None:
            rule_file = data_dir / "rule_set.json"

    with open(rule_file) as f:
        rule_set = json.load(f)

    # Use care_ai JSONL (has richer data including mood_state)
    sim_file = run_path / "simulations" / f"{patient_id}_care_ai.jsonl"
    if not sim_file.exists():
        sim_file = run_path / "simulations" / f"{patient_id}_natural.jsonl"

    day_data = None
    with open(sim_file) as f:
        for line in f:
            d = json.loads(line)
            if d.get("day") == day:
                day_data = d
                break

    if not day_data:
        raise ValueError(f"Day {day} not found in {sim_file}")

    day_data = _adapt_day_data(day_data)

    # Find last hospital record before this day
    last_hr = None
    hr_file = run_path / "simulations" / f"{patient_id}_care_ai_hospital.jsonl"
    if not hr_file.exists():
        hr_file = run_path / "simulations" / f"{patient_id}_natural_hospital.jsonl"
    if hr_file.exists():
        with open(hr_file) as f:
            for line in f:
                d = json.loads(line)
                if d.get("day", 0) < day and d.get("hospital_record"):
                    last_hr = d["hospital_record"]

    return patient, rule_set, day_data, last_hr


# ─── Run Care Agent with a specific LLM backend ──────────────

def run_care_agent_with_backend(
    backend_name: str,
    generate_fn,
    patient: dict,
    rule_set: dict,
    day_data: dict,
    last_hr: dict | None,
    seed: int = 42,
) -> tuple[dict, float]:
    """Run the CareAgent 4-turn call using specified LLM backend.

    Returns (care_record, total_elapsed_sec).
    """
    day = day_data["day"]

    persona_type = patient.get("persona", {}).get("type", "stoic")
    mood = MoodState(persona_type=persona_type, seed=seed)

    # If stored mood_state exists, restore it
    mood_data = day_data.get("mood_state", day_data.get("mood"))
    if mood_data and isinstance(mood_data, dict):
        for dim, val in mood_data.items():
            if dim in mood.state:
                mood.state[dim] = val

    sampler = Sampler(seed=seed)

    agent = CareAgent(
        patient=patient,
        rule_set=rule_set,
        mood=mood,
        sampler=sampler,
        model=backend_name,
    )

    print(f"\n{'─'*60}")
    print(f"Running Care Agent with: {backend_name}")
    print(f"Patient: {patient.get('patient_id')}, Day: {day}")
    print(f"{'─'*60}")

    t0 = time.time()

    with patch("src.agents.care_agent.generate_json", side_effect=generate_fn):
        care_record = agent.conduct_video_call(
            day=day,
            day_result=day_data,
            day_results=[day_data],
            last_hospital_record=last_hr,
        )

    elapsed = time.time() - t0
    print(f"  Completed in {elapsed:.1f}s")
    return care_record, elapsed


# ─── Comparison Display ──────────────────────────────────────

def display_comparison(
    gemini_record: dict,
    medgemma_record: dict,
    gemini_time: float,
    medgemma_time: float,
):
    """Print side-by-side comparison of both care records."""
    W = 80
    SEP = "═" * W

    print(f"\n\n{SEP}")
    print(f"{'COMPARISON: Gemini vs MedGemma 4B':^{W}}")
    print(SEP)

    # Timing
    print(f"\n⏱  Latency:")
    print(f"   Gemini (cloud):   {gemini_time:.1f}s")
    print(f"   MedGemma (local): {medgemma_time:.1f}s")

    # Turn-by-turn comparison
    for turn_num in [1, 2, 3, 4]:
        print(f"\n{'─'*W}")
        role = "Patient" if turn_num in (1, 3) else "Nurse"
        print(f"  T{turn_num} [{role}]")
        print(f"{'─'*W}")

        g_turn = _find_turn(gemini_record, turn_num)
        m_turn = _find_turn(medgemma_record, turn_num)

        print(f"\n  [Gemini]")
        _print_turn_content(g_turn, indent=4)
        print(f"\n  [MedGemma 4B]")
        _print_turn_content(m_turn, indent=4)

    # Assessment comparison
    print(f"\n{'─'*W}")
    print(f"  NURSE ASSESSMENT COMPARISON")
    print(f"{'─'*W}")

    g_assess = gemini_record.get("nurse_assessment", {})
    m_assess = medgemma_record.get("nurse_assessment", {})

    print(f"\n  Severity:  Gemini={g_assess.get('severity_level', '?'):8s}  "
          f"MedGemma={m_assess.get('severity_level', '?')}")

    print(f"\n  [Gemini summary]")
    print(textwrap.fill(g_assess.get("summary", "—"), width=W-4, initial_indent="    ", subsequent_indent="    "))
    print(f"\n  [MedGemma summary]")
    print(textwrap.fill(m_assess.get("summary", "—"), width=W-4, initial_indent="    ", subsequent_indent="    "))

    # Actions comparison
    print(f"\n  Actions:")
    g_actions = gemini_record.get("actions", [])
    m_actions = medgemma_record.get("actions", [])
    print(f"    Gemini:   {[a.get('action', '?') for a in g_actions]}")
    print(f"    MedGemma: {[a.get('action', '?') for a in m_actions]}")

    # Detection comparison
    g_det = gemini_record.get("detection", {})
    m_det = medgemma_record.get("detection", {})
    print(f"\n  AEs Detected:")
    print(f"    Gemini:   {g_det.get('aes_detected', [])}")
    print(f"    MedGemma: {m_det.get('aes_detected', [])}")

    # Ground truth for reference
    print(f"\n{'─'*W}")
    print(f"  GROUND TRUTH (for evaluation)")
    print(f"{'─'*W}")

    print(SEP)


def _find_turn(record: dict, turn_num: int) -> dict:
    for t in record.get("turns", []):
        if t.get("turn") == turn_num:
            return t.get("content", {})
    return {}


def _print_turn_content(content: dict, indent: int = 4):
    pad = " " * indent
    if not content or content.get("_parse_error"):
        raw = content.get("_raw", "")
        print(f"{pad}⚠ Parse error. Raw: {raw[:200]}")
        return

    for key, val in content.items():
        if key.startswith("_"):
            continue
        if isinstance(val, str):
            wrapped = textwrap.fill(val, width=76 - indent, initial_indent=f"{pad}{key}: ", subsequent_indent=pad + " " * (len(key) + 2))
            print(wrapped)
        elif isinstance(val, list) and val:
            print(f"{pad}{key}:")
            for item in val[:5]:
                if isinstance(item, dict):
                    summary = json.dumps(item, ensure_ascii=False)[:120]
                    print(f"{pad}  - {summary}")
                else:
                    print(f"{pad}  - {item}")
        elif isinstance(val, dict):
            print(f"{pad}{key}: {json.dumps(val, ensure_ascii=False)[:120]}")
        elif val is not None:
            print(f"{pad}{key}: {val}")


# ─── Scoring ─────────────────────────────────────────────────

def compute_scores(care_record: dict, day_data: dict) -> dict:
    """Compute quality scores against ground truth."""
    gt_aes = day_data.get("objective", {}).get("active_aes", [])
    if not gt_aes:
        # Fallback to CDISC AE field
        gt_aes = [_cdisc_ae_to_internal(ae, day_data.get("day", 1))
                   for ae in day_data.get("AE", []) if ae.get("AEONGO", True)]

    gt_ae_names = {ae.get("ae", ae.get("ae_term", ae.get("AETERM", ""))).lower() for ae in gt_aes}
    gt_max_grade = max((ae.get("grade", ae.get("_grade", 0)) for ae in gt_aes), default=0)

    detection = care_record.get("detection", {})
    detected = {a.lower() for a in detection.get("aes_detected", [])}

    assessment = care_record.get("nurse_assessment", {})
    sev = assessment.get("severity_level", "green")
    sev_map = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
    sev_score = sev_map.get(sev, 0)

    # Expected severity based on GT grade
    expected_sev = 0 if gt_max_grade == 0 else 1 if gt_max_grade <= 2 else 2 if gt_max_grade == 3 else 3
    sev_accuracy = 1.0 - abs(sev_score - expected_sev) / 3.0

    # AE detection recall
    if gt_ae_names:
        hits = sum(1 for g in gt_ae_names if any(g in d or d in g for d in detected))
        recall = hits / len(gt_ae_names)
    else:
        recall = 1.0 if not detected else 0.5

    # Check for clinical errors (hallucinated dangerous actions)
    actions = care_record.get("actions", [])
    action_names = [a.get("action", "") for a in actions]
    has_overescalation = any(a in ("recommend_hospital_visit", "escalate_to_physician") for a in action_names) and gt_max_grade <= 2
    has_underescalation = any(a in ("no_action",) for a in action_names) and gt_max_grade >= 3

    # JSON validity
    has_parse_error = any(
        t.get("content", {}).get("_parse_error")
        for t in care_record.get("turns", [])
    )

    return {
        "ae_detection_recall": round(recall, 2),
        "severity_accuracy": round(sev_accuracy, 2),
        "severity_chosen": sev,
        "severity_expected": ["green", "yellow", "orange", "red"][expected_sev],
        "over_escalation": has_overescalation,
        "under_escalation": has_underescalation,
        "json_parse_errors": has_parse_error,
        "n_actions": len(actions),
        "action_types": action_names,
        "gt_ae_count": len(gt_ae_names),
        "gt_max_grade": gt_max_grade,
        "detected_ae_count": len(detected),
    }


# ─── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Compare Gemini vs MedGemma 4B for Care Agent")
    parser.add_argument("--run", required=True, help="Run ID")
    parser.add_argument("--patient", default="PT-001", help="Patient ID")
    parser.add_argument("--day", type=int, default=73, help="Day number")
    parser.add_argument("--gpu", type=int, default=4, help="GPU ID for MedGemma")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--skip-gemini", action="store_true", help="Skip Gemini run")
    parser.add_argument("--skip-medgemma", action="store_true", help="Skip MedGemma run")
    parser.add_argument("--output", type=str, default=None, help="Save results JSON to file")
    args = parser.parse_args()

    print(f"{'='*60}")
    print(f"Care Agent Model Comparison")
    print(f"  Run:     {args.run}")
    print(f"  Patient: {args.patient}")
    print(f"  Day:     {args.day}")
    print(f"  GPU:     cuda:{args.gpu}")
    print(f"{'='*60}")

    patient, rule_set, day_data, last_hr = load_patient_and_day(
        args.run, args.patient, args.day,
    )

    gt_aes = day_data.get("objective", {}).get("active_aes", [])
    subj = day_data.get("subjective", {})
    print(f"\nGround Truth AEs: {len(gt_aes)}")
    for ae in gt_aes:
        print(f"  - {ae.get('ae', '?')} Grade {ae.get('grade', '?')} ({ae.get('days_active', '?')} days active)")
    print(f"Patient awareness: {subj.get('overall_awareness', '?')}")
    print(f"Perceived symptoms: {subj.get('symptoms_patient_perceives', [])}")

    results = {"run_id": args.run, "patient_id": args.patient, "day": args.day}

    # ── Gemini ──
    gemini_record, gemini_time = None, 0.0
    if not args.skip_gemini:
        gemini_record, gemini_time = run_care_agent_with_backend(
            backend_name="gemini-2.0-flash",
            generate_fn=gemini_generate_json,
            patient=patient,
            rule_set=rule_set,
            day_data=day_data,
            last_hr=last_hr,
            seed=args.seed,
        )
        results["gemini"] = {
            "care_record": gemini_record,
            "elapsed_sec": round(gemini_time, 2),
            "scores": compute_scores(gemini_record, day_data),
        }
        print(f"\n  Gemini scores: {json.dumps(results['gemini']['scores'], indent=2)}")

    # ── MedGemma 4B ──
    medgemma_record, medgemma_time = None, 0.0
    if not args.skip_medgemma:
        load_medgemma(gpu_id=args.gpu)
        medgemma_record, medgemma_time = run_care_agent_with_backend(
            backend_name="medgemma-1.5-4b-it",
            generate_fn=medgemma_generate_json,
            patient=patient,
            rule_set=rule_set,
            day_data=day_data,
            last_hr=last_hr,
            seed=args.seed,
        )
        results["medgemma"] = {
            "care_record": medgemma_record,
            "elapsed_sec": round(medgemma_time, 2),
            "scores": compute_scores(medgemma_record, day_data),
        }
        print(f"\n  MedGemma scores: {json.dumps(results['medgemma']['scores'], indent=2)}")

    # ── Display ──
    if gemini_record and medgemma_record:
        display_comparison(gemini_record, medgemma_record, gemini_time, medgemma_time)

        print(f"\n{'='*60}")
        print(f"{'SCORE SUMMARY':^60}")
        print(f"{'='*60}")
        print(f"{'Metric':<30} {'Gemini':>12} {'MedGemma':>12}")
        print(f"{'─'*60}")
        g_sc = results["gemini"]["scores"]
        m_sc = results["medgemma"]["scores"]
        for key in ["ae_detection_recall", "severity_accuracy", "severity_chosen",
                     "over_escalation", "under_escalation", "json_parse_errors",
                     "n_actions"]:
            gv = g_sc.get(key, "—")
            mv = m_sc.get(key, "—")
            print(f"  {key:<28} {str(gv):>12} {str(mv):>12}")
        print(f"{'─'*60}")
        print(f"  {'latency (sec)':<28} {gemini_time:>12.1f} {medgemma_time:>12.1f}")
        print(f"{'='*60}")

    # ── Save ──
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nResults saved → {out_path}")
    else:
        default_out = PROJECT_ROOT / "data" / "experiments" / f"care_compare_{args.patient}_d{args.day}.json"
        default_out.parent.mkdir(parents=True, exist_ok=True)
        with open(default_out, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nResults saved → {default_out}")


if __name__ == "__main__":
    main()
