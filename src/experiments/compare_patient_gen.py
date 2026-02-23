"""Compare Patient Generation quality: Gemini (cloud) vs MedGemma 1.5 4B (local).

Generates the same patient (same seed → same demographics) through both models
and compares the quality of comorbidity reasoning, baseline labs, and persona.

Usage:
    python -m src.experiments.compare_patient_gen \
        --run 20260219_050602_Padcev___Pembrolizumab_10pt_126d \
        --patient-num 1 \
        --gpu 4
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

from src.agents.patient_agent import generate_patient
from src.agents.llm_client import generate_json as gemini_generate_json
from src.engine.sampler import Sampler


# ─── Local MedGemma Inference ─────────────────────────────────
# Reuse the loader and generator from compare_care_models

_local_model = None
_local_tokenizer = None


def load_medgemma(gpu_id: int = 4):
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
        dtype=torch.bfloat16,
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

    json_str = raw.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[1]
        json_str = json_str.split("```", 1)[0]
    elif "```" in json_str:
        json_str = json_str.split("```", 1)[1]
        json_str = json_str.split("```", 1)[0]

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

    token_count = len(generated)
    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        print(f"  [MedGemma] JSON parse failed. Raw ({len(raw)} chars): {raw[:400]}...")
        result = {"_parse_error": True, "_raw": raw[:500]}

    result["_mm_meta"] = {
        "latency_sec": round(elapsed, 2),
        "tokens_generated": token_count,
    }
    return result


# ─── Generate Patient with a specific backend ────────────────

def generate_patient_with_backend(
    backend_name: str,
    generate_fn,
    rule_set: dict,
    patient_number: int,
    seed: int,
) -> tuple[dict, float]:
    """Generate one patient using the specified LLM backend."""
    sampler = Sampler(seed=seed + patient_number)

    print(f"\n{'─'*60}")
    print(f"Generating patient with: {backend_name}")
    print(f"Patient #{patient_number}, seed={seed + patient_number}")
    print(f"{'─'*60}")

    t0 = time.time()

    # Patch all LLM calls in patient_agent and prob_engine
    with patch("src.agents.patient_agent.generate_json", side_effect=generate_fn), \
         patch("src.engine.prob_engine.generate_json", side_effect=generate_fn):
        patient = generate_patient(
            rule_set=rule_set,
            patient_number=patient_number,
            total_patients=1,
            sampler=sampler,
            model=backend_name,
        )

    elapsed = time.time() - t0
    print(f"  Total generation time: {elapsed:.1f}s")
    return patient, elapsed


# ─── Quality Scoring ─────────────────────────────────────────

def score_patient(patient: dict, rule_set: dict) -> dict:
    """Evaluate quality of generated patient data."""
    emr = patient.get("emr", {})
    demo = emr.get("demographics", {})
    labs = emr.get("baseline_labs", {})
    vitals = emr.get("baseline_vitals", {})
    comorbidities = emr.get("medical_history", [])
    persona = patient.get("persona", {})
    diagnosis = emr.get("diagnosis", {})

    issues = []
    warnings = []

    # 1. Demographics completeness
    for field in ["age", "sex", "race"]:
        if not demo.get(field):
            issues.append(f"Missing demographics.{field}")

    # 2. Lab completeness and ranges
    required_labs = ["ANC", "hemoglobin", "platelets", "creatinine", "ALT", "AST"]
    labs_present = 0
    for lab_name in required_labs:
        found = False
        for key, val in labs.items():
            if lab_name.lower() in key.lower():
                found = True
                if isinstance(val, dict):
                    v = val.get("value")
                else:
                    v = val
                if v is not None:
                    labs_present += 1
                    # Basic range checks
                    try:
                        v = float(v)
                        if lab_name == "ANC" and v < 1.5:
                            warnings.append(f"ANC={v} below trial eligibility (≥1.5)")
                        if lab_name == "platelets" and v < 100:
                            warnings.append(f"Platelets={v} below trial eligibility (≥100)")
                        if "hemoglobin" in lab_name.lower() and v < 5:
                            issues.append(f"Hemoglobin={v} unrealistically low")
                        if "creatinine" in lab_name.lower() and v > 10:
                            issues.append(f"Creatinine={v} unrealistically high")
                    except (ValueError, TypeError):
                        pass
                break
        if not found:
            warnings.append(f"Missing lab: {lab_name}")

    lab_completeness = labs_present / len(required_labs) if required_labs else 1.0

    # 3. Vitals completeness and sanity
    vitals_ok = True
    bt = vitals.get("BT") or vitals.get("body_temperature") or vitals.get("temperature")
    if bt is not None:
        try:
            bt = float(bt) if not isinstance(bt, dict) else float(bt.get("value", 0))
            if bt > 45 or bt < 30:
                issues.append(f"BT={bt}°C out of any plausible range")
                vitals_ok = False
            elif bt > 42:
                warnings.append(f"BT={bt}°C very high (possible Fahrenheit error)")
        except (ValueError, TypeError):
            pass

    weight = vitals.get("weight_kg") or vitals.get("weight")
    if weight is not None:
        try:
            w = float(weight) if not isinstance(weight, dict) else float(weight.get("value", 0))
            if w < 30 or w > 200:
                issues.append(f"Weight={w}kg out of plausible range")
                vitals_ok = False
        except (ValueError, TypeError):
            pass

    # 4. Comorbidity-lab consistency
    consistency_score = 1.0
    cond_names = {c.get("condition", "").lower() for c in comorbidities}

    if any("diabetes" in c for c in cond_names):
        glucose = _find_lab(labs, "glucose")
        hba1c = _find_lab(labs, "hba1c")
        if glucose is not None and glucose < 100:
            warnings.append(f"Diabetes present but glucose={glucose} normal")
            consistency_score -= 0.15
        if hba1c is not None and hba1c < 6.0:
            warnings.append(f"Diabetes present but HbA1c={hba1c} normal")
            consistency_score -= 0.15

    if any("ckd" in c or "kidney" in c for c in cond_names):
        cr = _find_lab(labs, "creatinine")
        egfr = _find_lab(labs, "egfr")
        if cr is not None and cr < 1.2:
            warnings.append(f"CKD present but creatinine={cr} normal")
            consistency_score -= 0.15
        if egfr is not None and egfr >= 60:
            warnings.append(f"CKD present but eGFR={egfr} normal")
            consistency_score -= 0.15

    if any("hypertension" in c for c in cond_names):
        sbp = vitals.get("SBP")
        if sbp is not None:
            try:
                sbp_val = float(sbp) if not isinstance(sbp, dict) else float(sbp.get("value", 0))
                if sbp_val < 120:
                    warnings.append(f"Hypertension present but SBP={sbp_val} — likely on meds (acceptable)")
            except (ValueError, TypeError):
                pass

    # 5. Persona quality
    persona_ok = bool(persona.get("type")) and bool(persona.get("description"))
    disclosure = persona.get("disclosure_tendencies", {})
    disclosure_complete = sum(1 for v in disclosure.values() if v) / max(len(disclosure), 1)

    # 6. JSON parse errors
    has_parse_error = patient.get("_parse_error", False)
    for section in [labs, vitals, persona, diagnosis]:
        if isinstance(section, dict) and section.get("_parse_error"):
            has_parse_error = True

    return {
        "lab_completeness": round(lab_completeness, 2),
        "vitals_ok": vitals_ok,
        "consistency_score": round(max(consistency_score, 0), 2),
        "persona_ok": persona_ok,
        "disclosure_completeness": round(disclosure_complete, 2),
        "comorbidity_count": len(comorbidities),
        "issues": issues,
        "warnings": warnings,
        "json_parse_error": has_parse_error,
    }


def _find_lab(labs: dict, keyword: str):
    """Find a lab value by keyword match."""
    for key, val in labs.items():
        if keyword.lower() in key.lower():
            if isinstance(val, dict):
                v = val.get("value")
            else:
                v = val
            try:
                return float(v)
            except (ValueError, TypeError):
                return None
    return None


# ─── Display ─────────────────────────────────────────────────

def display_comparison(
    gemini_patient: dict,
    medgemma_patient: dict,
    gemini_scores: dict,
    medgemma_scores: dict,
    gemini_time: float,
    medgemma_time: float,
    reference_patient: dict | None = None,
):
    W = 80
    SEP = "═" * W

    print(f"\n\n{SEP}")
    print(f"{'PATIENT GENERATION COMPARISON':^{W}}")
    print(f"{'Gemini 2.0 Flash vs MedGemma 1.5 4B':^{W}}")
    print(f"{SEP}")

    print(f"\n⏱  Latency:")
    print(f"   Gemini (cloud):   {gemini_time:.1f}s")
    print(f"   MedGemma (local): {medgemma_time:.1f}s")

    # Demographics (should be identical — sampled by code)
    g_demo = gemini_patient.get("emr", {}).get("demographics", {})
    m_demo = medgemma_patient.get("emr", {}).get("demographics", {})
    print(f"\n{'─'*W}")
    print(f"  DEMOGRAPHICS (code-sampled, should be identical)")
    print(f"{'─'*W}")
    print(f"    Age: {g_demo.get('age')} | Sex: {g_demo.get('sex')} | Race: {g_demo.get('race')}")

    # Comorbidities
    print(f"\n{'─'*W}")
    print(f"  COMORBIDITIES (LLM probability adjustment + sampling)")
    print(f"{'─'*W}")
    g_comorb = gemini_patient.get("emr", {}).get("medical_history", [])
    m_comorb = medgemma_patient.get("emr", {}).get("medical_history", [])
    g_names = [c.get("condition", "?") for c in g_comorb]
    m_names = [c.get("condition", "?") for c in m_comorb]
    print(f"  [Gemini]   ({len(g_comorb)}): {g_names}")
    print(f"  [MedGemma] ({len(m_comorb)}): {m_names}")

    # Baseline Labs
    print(f"\n{'─'*W}")
    print(f"  BASELINE LABS")
    print(f"{'─'*W}")
    g_labs = gemini_patient.get("emr", {}).get("baseline_labs", {})
    m_labs = medgemma_patient.get("emr", {}).get("baseline_labs", {})
    all_lab_keys = sorted(set(list(g_labs.keys()) + list(m_labs.keys())))
    print(f"  {'Lab':<25} {'Gemini':>15} {'MedGemma':>15}")
    print(f"  {'─'*55}")
    for k in all_lab_keys:
        gv = _fmt_lab(g_labs.get(k))
        mv = _fmt_lab(m_labs.get(k))
        print(f"  {k:<25} {gv:>15} {mv:>15}")

    # Baseline Vitals
    print(f"\n{'─'*W}")
    print(f"  BASELINE VITALS")
    print(f"{'─'*W}")
    g_vit = gemini_patient.get("emr", {}).get("baseline_vitals", {})
    m_vit = medgemma_patient.get("emr", {}).get("baseline_vitals", {})
    all_vit_keys = sorted(set(list(g_vit.keys()) + list(m_vit.keys())))
    print(f"  {'Vital':<25} {'Gemini':>15} {'MedGemma':>15}")
    print(f"  {'─'*55}")
    for k in all_vit_keys:
        gv = _fmt_lab(g_vit.get(k))
        mv = _fmt_lab(m_vit.get(k))
        print(f"  {k:<25} {gv:>15} {mv:>15}")

    # Persona
    print(f"\n{'─'*W}")
    print(f"  PERSONA")
    print(f"{'─'*W}")
    g_persona = gemini_patient.get("persona", {})
    m_persona = medgemma_patient.get("persona", {})
    print(f"\n  [Gemini] type={g_persona.get('type')}")
    g_desc = g_persona.get("description", "—")
    print(textwrap.fill(g_desc, width=W-4, initial_indent="    ", subsequent_indent="    "))
    print(f"\n  [MedGemma] type={m_persona.get('type')}")
    m_desc = m_persona.get("description", "—")
    print(textwrap.fill(m_desc, width=W-4, initial_indent="    ", subsequent_indent="    "))

    # Disclosure tendencies
    g_disc = g_persona.get("disclosure_tendencies", {})
    m_disc = m_persona.get("disclosure_tendencies", {})
    if g_disc or m_disc:
        all_disc = sorted(set(list(g_disc.keys()) + list(m_disc.keys())))
        print(f"\n  {'Disclosure':<20} {'Gemini':>20} {'MedGemma':>20}")
        print(f"  {'─'*60}")
        for k in all_disc:
            print(f"  {k:<20} {str(g_disc.get(k, '—')):>20} {str(m_disc.get(k, '—')):>20}")

    # Disease baseline
    print(f"\n{'─'*W}")
    print(f"  DISEASE BASELINE")
    print(f"{'─'*W}")
    g_dx = gemini_patient.get("emr", {}).get("diagnosis", {})
    m_dx = medgemma_patient.get("emr", {}).get("diagnosis", {})
    print(f"  [Gemini]   stage={g_dx.get('stage')}, lesions={len(g_dx.get('target_lesions', []))}")
    print(f"  [MedGemma] stage={m_dx.get('stage')}, lesions={len(m_dx.get('target_lesions', []))}")

    # Score summary
    print(f"\n{SEP}")
    print(f"{'QUALITY SCORES':^{W}}")
    print(f"{SEP}")
    print(f"  {'Metric':<30} {'Gemini':>12} {'MedGemma':>12}")
    print(f"  {'─'*56}")
    for key in ["lab_completeness", "vitals_ok", "consistency_score",
                 "persona_ok", "disclosure_completeness", "comorbidity_count",
                 "json_parse_error"]:
        gv = gemini_scores.get(key, "—")
        mv = medgemma_scores.get(key, "—")
        print(f"  {key:<30} {str(gv):>12} {str(mv):>12}")
    print(f"  {'─'*56}")
    print(f"  {'latency (sec)':<30} {gemini_time:>12.1f} {medgemma_time:>12.1f}")
    print(f"{SEP}")

    # Issues
    if gemini_scores.get("issues") or gemini_scores.get("warnings"):
        print(f"\n  Gemini issues:   {gemini_scores.get('issues', [])}")
        print(f"  Gemini warnings: {gemini_scores.get('warnings', [])}")
    if medgemma_scores.get("issues") or medgemma_scores.get("warnings"):
        print(f"\n  MedGemma issues:   {medgemma_scores.get('issues', [])}")
        print(f"  MedGemma warnings: {medgemma_scores.get('warnings', [])}")

    # Reference comparison
    if reference_patient:
        print(f"\n{'─'*W}")
        print(f"  REFERENCE (existing patient from simulation)")
        print(f"{'─'*W}")
        r = reference_patient.get("emr", {})
        print(f"  Age: {r.get('demographics',{}).get('age')}, "
              f"Comorbidities: {[c.get('condition') for c in r.get('medical_history', [])]}")
        print(f"  Persona: {reference_patient.get('persona',{}).get('type')}")


def _fmt_lab(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, dict):
        v = val.get("value", "?")
        u = val.get("unit", "")
        return f"{v} {u}".strip()
    return str(val)


# ─── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Compare Gemini vs MedGemma for Patient Generation")
    parser.add_argument("--run", required=True, help="Run ID (for rule_set and reference patient)")
    parser.add_argument("--patient-num", type=int, default=1, help="Patient number to generate")
    parser.add_argument("--gpu", type=int, default=4, help="GPU ID for MedGemma")
    parser.add_argument("--seed", type=int, default=0, help="Base random seed")
    parser.add_argument("--skip-gemini", action="store_true")
    parser.add_argument("--skip-medgemma", action="store_true")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    run_path = PROJECT_ROOT / "data" / "runs" / args.run
    if not run_path.exists():
        print(f"ERROR: Run not found: {run_path}")
        sys.exit(1)

    with open(run_path / "rule_set.json") as f:
        rule_set = json.load(f)

    # Load reference patient for comparison
    pid = f"PT-{args.patient_num:03d}"
    ref_patient = None
    ref_file = run_path / "patients" / f"{pid}.json"
    if ref_file.exists():
        with open(ref_file) as f:
            ref_patient = json.load(f)

    print(f"{'='*60}")
    print(f"Patient Generation Model Comparison")
    print(f"  Run:     {args.run}")
    print(f"  Drug:    {rule_set.get('drug_name', '?')}")
    print(f"  Patient: #{args.patient_num}")
    print(f"  Seed:    {args.seed}")
    print(f"  GPU:     cuda:{args.gpu}")
    print(f"{'='*60}")

    results = {
        "run_id": args.run,
        "patient_num": args.patient_num,
        "seed": args.seed,
        "drug": rule_set.get("drug_name"),
    }

    # ── Gemini ──
    gemini_patient, gemini_time = None, 0.0
    gemini_scores = {}
    if not args.skip_gemini:
        gemini_patient, gemini_time = generate_patient_with_backend(
            "gemini-2.0-flash", gemini_generate_json,
            rule_set, args.patient_num, args.seed,
        )
        gemini_scores = score_patient(gemini_patient, rule_set)
        results["gemini"] = {
            "patient": gemini_patient,
            "elapsed_sec": round(gemini_time, 2),
            "scores": gemini_scores,
        }
        print(f"\n  Gemini scores: {json.dumps(gemini_scores, indent=2)}")

    # ── MedGemma ──
    medgemma_patient, medgemma_time = None, 0.0
    medgemma_scores = {}
    if not args.skip_medgemma:
        load_medgemma(gpu_id=args.gpu)
        medgemma_patient, medgemma_time = generate_patient_with_backend(
            "medgemma-1.5-4b-it", medgemma_generate_json,
            rule_set, args.patient_num, args.seed,
        )
        medgemma_scores = score_patient(medgemma_patient, rule_set)
        results["medgemma"] = {
            "patient": medgemma_patient,
            "elapsed_sec": round(medgemma_time, 2),
            "scores": medgemma_scores,
        }
        print(f"\n  MedGemma scores: {json.dumps(medgemma_scores, indent=2)}")

    # ── Display ──
    if gemini_patient and medgemma_patient:
        display_comparison(
            gemini_patient, medgemma_patient,
            gemini_scores, medgemma_scores,
            gemini_time, medgemma_time,
            reference_patient=ref_patient,
        )

    # ── Save ──
    out_path = Path(args.output) if args.output else (
        PROJECT_ROOT / "data" / "experiments" / f"patient_compare_{pid}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved → {out_path}")


if __name__ == "__main__":
    main()
