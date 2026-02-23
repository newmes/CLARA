"""Evaluate SFT checkpoints on OOD test set — no Claude, fast scoring.

Pipeline per sample:
  1. Load scenario from test sft_data.jsonl (patient_said + visual_assessment + drug context)
  2. Generate T2 (nurse response) using each checkpoint
  3. Generate T3 (patient response via Gemini)
  4. Score: AE detection + mood proxy → pareto

Usage:
    python -m src.experiments.eval_checkpoints \
        --test-data data/training_v3_test_ood/sft_data.jsonl \
        --checkpoints baseline epoch4 epoch5 \
        --gpu 6 --seed 42
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from src.experiments.generate_sft_data_v3 import (
    build_nurse_system_prompt,
    build_nurse_user_prompt,
    generate_t3_patient,
    measure_patient_behavior,
    compute_dual_objective,
    MOOD_SCENARIOS,
)
from src.engine.mood import MoodState, compute_interaction_quality, compute_grade_distortion
from config.defaults import normalize_ae_term

MEDGEMMA_BASE = "google/medgemma-4b-it"
N_REPEAT = 1


def load_model(model_path: str, adapter_path: str | None, gpu_id: int):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM

    device = f"cuda:{gpu_id}"
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForCausalLM.from_pretrained(
        model_path, dtype=torch.bfloat16, device_map={"": device},
    )
    if adapter_path:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter_path)
        model = model.merge_and_unload()
    model.eval()
    return model, tokenizer


def make_nurse_fn(model, tokenizer):
    import torch, re
    def _fn(system_prompt: str, user_prompt: str, **kwargs) -> dict:
        chat = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        text = tokenizer.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            out = model.generate(
                **inputs, max_new_tokens=512,
                temperature=0.7, top_p=0.9, do_sample=True,
            )
        raw = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        try:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            return json.loads(match.group()) if match else {"_raw": raw[:500]}
        except (json.JSONDecodeError, AttributeError):
            return {"_raw": raw[:500]}
    return _fn


def reconstruct_scenario(sft_example: dict) -> dict:
    """Reconstruct scenario dict from SFT example context."""
    ctx = sft_example["context"]
    gt_nv = sft_example.get("gt_non_visual_aes", [])
    mood_raw = ctx.get("patient_mood", {})
    return {
        "drug_name": ctx["drug_name"],
        "indication": ctx["indication"],
        "visual_assessment": ctx["visual_assessment"],
        "drug_ae_profile": ctx["drug_ae_profile"],
        "treatment_day": ctx["treatment_day"],
        "gt_non_visual_aes": gt_nv,
        "gt_visual_aes": [],
        "patient_demographics": {"age": 60, "sex": "M"},
        "patient_persona_type": "stoic_minimizer",
        "patient_mood": mood_raw,
    }


def eval_single(scenario: dict, t1_visible: dict, nurse_fn, quality: dict, grade_distortion: int, mood: MoodState):
    """Run T2 → T3 → Score for one sample."""
    sys_prompt = build_nurse_system_prompt(scenario, quality)
    usr_prompt = build_nurse_user_prompt(scenario, t1_visible)

    t0 = time.time()
    t2 = nurse_fn(sys_prompt, usr_prompt)
    t2["_turn"] = 2
    t2_time = time.time() - t0

    t3_scores = []
    detected_all = set()
    for _ in range(N_REPEAT):
        t3 = generate_t3_patient(scenario, t2, mood, quality, grade_distortion)
        behavior = measure_patient_behavior(t3)
        responses = t3.get("responses", [])
        detected = [r.get("revealed_symptom", "") for r in responses if r.get("revealed_symptom")]
        nurse_targets = [q.get("target_ae", "") for q in t2.get("questions", []) if isinstance(q, dict) and q.get("target_ae")]
        all_detected = detected + nurse_targets
        detected_all.update(all_detected)
        dual = compute_dual_objective(behavior, scenario["gt_non_visual_aes"], list(detected_all))
        t3_scores.append(dual)

    avg_ae = sum(d["ae_score"] for d in t3_scores) / len(t3_scores)
    avg_mood = sum(d["mood_score"] for d in t3_scores) / len(t3_scores)
    avg_pareto = sum(d["pareto_score"] for d in t3_scores) / len(t3_scores)

    return {
        "ae_score": round(avg_ae, 3),
        "mood_score": round(avg_mood, 3),
        "pareto_score": round(avg_pareto, 3),
        "t2_time_s": round(t2_time, 1),
        "detected_aes": list(detected_all),
        "n_questions": len(t2.get("questions", [])),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-data", required=True)
    parser.add_argument("--checkpoints", nargs="+", required=True,
                        help="List of 'name:adapter_path' or 'baseline' for no adapter")
    parser.add_argument("--gpu", type=int, default=6)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-samples", type=int, default=None)
    args = parser.parse_args()

    test_data = []
    with open(args.test_data) as f:
        for line in f:
            test_data.append(json.loads(line))
    if args.max_samples:
        test_data = test_data[:args.max_samples]
    print(f"Test samples: {len(test_data)}")

    checkpoints = []
    for spec in args.checkpoints:
        if spec == "baseline":
            checkpoints.append(("baseline", None))
        elif ":" in spec:
            name, path = spec.split(":", 1)
            checkpoints.append((name, path))
        else:
            checkpoints.append((spec, spec))

    all_results = {}

    for ckpt_name, adapter_path in checkpoints:
        print(f"\n{'='*60}")
        print(f"  Evaluating: {ckpt_name}")
        print(f"{'='*60}")

        model, tokenizer = load_model(MEDGEMMA_BASE, adapter_path, args.gpu)
        nurse_fn = make_nurse_fn(model, tokenizer)

        results = []
        for i, sft_ex in enumerate(test_data):
            scenario = reconstruct_scenario(sft_ex)
            t1_visible = sft_ex["context"]["patient_said"]
            mood_raw = scenario["patient_mood"]

            mood = MoodState(persona_type=scenario["patient_persona_type"], seed=args.seed + i)
            for dim, val in mood_raw.items():
                if dim in mood.state:
                    mood.state[dim] = val

            quality = compute_interaction_quality(mood)
            grade_distortion = compute_grade_distortion(mood)

            r = eval_single(scenario, t1_visible, nurse_fn, quality, grade_distortion, mood)
            results.append(r)

            n_gt = len(scenario["gt_non_visual_aes"])
            print(f"  [{i+1}/{len(test_data)}] AE={r['ae_score']:.2f} Mood={r['mood_score']:.2f} "
                  f"Pareto={r['pareto_score']:.2f} (GT:{n_gt} Det:{len(r['detected_aes'])}) "
                  f"{r['t2_time_s']:.1f}s")

        avg_ae = sum(r["ae_score"] for r in results) / len(results)
        avg_mood = sum(r["mood_score"] for r in results) / len(results)
        avg_pareto = sum(r["pareto_score"] for r in results) / len(results)
        avg_time = sum(r["t2_time_s"] for r in results) / len(results)
        all_results[ckpt_name] = {
            "avg_ae": round(avg_ae, 3),
            "avg_mood": round(avg_mood, 3),
            "avg_pareto": round(avg_pareto, 3),
            "avg_time": round(avg_time, 1),
            "n_samples": len(results),
            "details": results,
        }

        print(f"\n  >>> {ckpt_name}: AE={avg_ae:.3f}  Mood={avg_mood:.3f}  Pareto={avg_pareto:.3f}  ({avg_time:.1f}s/sample)")

        del model, tokenizer
        import torch; torch.cuda.empty_cache()
        import gc; gc.collect()

    print(f"\n\n{'='*60}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*60}")
    print(f"{'Model':<20} {'AE':>8} {'Mood':>8} {'Pareto':>8} {'Time':>8}")
    print("-" * 55)
    for name, r in all_results.items():
        print(f"{name:<20} {r['avg_ae']:>8.3f} {r['avg_mood']:>8.3f} {r['avg_pareto']:>8.3f} {r['avg_time']:>7.1f}s")

    out_path = Path(args.test_data).parent / "eval_results.json"
    with open(out_path, "w") as f:
        json.dump({k: {kk: vv for kk, vv in v.items() if kk != "details"} for k, v in all_results.items()}, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
