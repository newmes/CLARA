"""Compare Base MedGemma-4B vs Anti-Hallucination MedGemma-4B

2 models on 2 GPUs in parallel. No Claude API — only Gemini for patient LLM.

Usage:
    python -m src.experiments.eval_medgemma_compare \
        --rule-sets data/rule_set_calibrated_ev302.json data/rule_set_ep_sclc.json data/rule_set_darbepoetin_sclc.json \
        --n-scenarios 21 --gpus 2,7 --seed 99
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Process, Queue
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

N_REPEAT = 3

# ═══════════════════════════════════════════════════════════
# Model loading (local MedGemma)
# ═══════════════════════════════════════════════════════════

def _make_local_nurse_fn(
    model_path: str,
    gpu_id: int,
    tokenizer_path: str | None = None,
    adapter_path: str | None = None,
):
    """Load a MedGemma model onto a specific GPU and return a generate function.
    
    If adapter_path is given, loads base model + LoRA adapter (merged).
    If the model doesn't bundle a tokenizer, pass tokenizer_path.
    """
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM

    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    tok_src = tokenizer_path or model_path
    print(f"    Loading {model_path} on GPU {gpu_id} (tok={tok_src})", flush=True)
    if adapter_path:
        print(f"    + adapter: {adapter_path}", flush=True)
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(tok_src)
    model = AutoModelForCausalLM.from_pretrained(
        model_path, torch_dtype=torch.bfloat16, device_map={"": 0},
    )
    if adapter_path:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter_path)
        model = model.merge_and_unload()
    model.eval()
    print(f"    Loaded ({time.time()-t0:.1f}s, {torch.cuda.memory_allocated(0)/1e9:.1f}GB)", flush=True)

    def _generate(system_prompt: str, user_prompt: str, **kwargs) -> dict:
        chat = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt + "\n\nRespond with valid JSON only."},
        ]
        input_text = tokenizer.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(input_text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            outputs = model.generate(
                **inputs, max_new_tokens=4096,
                temperature=0.7, top_p=0.9, do_sample=True,
            )
        generated = outputs[0][inputs["input_ids"].shape[1]:]
        raw = tokenizer.decode(generated, skip_special_tokens=True)

        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in json_str:
            json_str = json_str.split("```", 1)[1].split("```", 1)[0]
        start = json_str.find("{")
        if start >= 0:
            depth, end = 0, start
            for i, c in enumerate(json_str[start:], start):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            json_str = json_str[start:end]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {"_raw": raw[:500], "_parse_error": True}

    return _generate


# ═══════════════════════════════════════════════════════════
# Reuse v3 pipeline functions
# ═══════════════════════════════════════════════════════════

from src.experiments.generate_sft_data_v3 import (
    synthesize_scenarios,
    build_drug_ae_profile,
    classify_ae_channels,
    generate_t1_patient,
    build_nurse_system_prompt,
    build_nurse_user_prompt,
    generate_t3_patient,
    measure_patient_behavior,
    compute_dual_objective,
    MOOD_SCENARIOS,
)


# ═══════════════════════════════════════════════════════════
# Single scenario evaluation
# ═══════════════════════════════════════════════════════════

def eval_one_scenario(scenario: dict, nurse_fn: callable, seed: int) -> dict:
    """Run T1→T2→T3×N for a scenario and return scores."""
    from src.engine.mood import MoodState, compute_interaction_quality, compute_grade_distortion

    mood = MoodState(persona_type=scenario["patient_persona_type"], seed=seed)
    mood_vals = scenario["patient_mood"]
    if isinstance(mood_vals, str):
        mood_vals = MOOD_SCENARIOS.get(mood_vals, MOOD_SCENARIOS["cooperative"])
    for dim, val in mood_vals.items():
        if dim in mood.state:
            mood.state[dim] = val

    quality = compute_interaction_quality(mood)
    grade_distortion = compute_grade_distortion(mood)

    # T1: Patient speaks (Gemini)
    t1 = generate_t1_patient(scenario, mood, quality, grade_distortion)

    t1_visible = {
        "greeting": t1.get("greeting", ""),
        "reported_symptoms": t1.get("reported_symptoms", []),
        "general_wellbeing": t1.get("general_wellbeing", ""),
        "mood_expression": t1.get("mood_expression", ""),
        "video_visible": t1.get("video_visible", []),
    }

    # T2: Nurse responds (local MedGemma)
    sys_prompt = build_nurse_system_prompt(scenario, quality)
    usr_prompt = build_nurse_user_prompt(scenario, t1_visible)
    t0 = time.time()
    t2 = nurse_fn(sys_prompt, usr_prompt)
    nurse_time = time.time() - t0

    # T3 × N_REPEAT: Patient responds to nurse (Gemini)
    gt_nv = scenario["gt_non_visual_aes"]
    behaviors = []
    all_detected = []
    for trial in range(N_REPEAT):
        t3 = generate_t3_patient(scenario, t2, mood, quality, grade_distortion)
        beh = measure_patient_behavior(t3)
        behaviors.append(beh)

        detected = []
        for resp in t3.get("responses", []):
            for ae in resp.get("revealed_aes", []):
                if isinstance(ae, dict):
                    detected.append(ae.get("ae_term", ae.get("ae", "")))
                elif isinstance(ae, str):
                    detected.append(ae)
        all_detected.extend(detected)

    avg_behavior = {}
    for key in behaviors[0]:
        vals = [b[key] for b in behaviors]
        if isinstance(vals[0], (int, float)):
            avg_behavior[key] = round(sum(vals) / len(vals), 3)
        else:
            avg_behavior[key] = vals[0]

    unique_detected = list(set(all_detected))
    dual = compute_dual_objective(avg_behavior, gt_nv, unique_detected)

    return {
        "scenario_id": scenario["scenario_id"],
        "drug_name": scenario["drug_name"],
        "n_nv_aes": len(gt_nv),
        "mood": scenario.get("patient_mood", "?"),
        "persona": scenario.get("patient_persona_type", "?"),
        "ae_score": dual["ae_score"],
        "mood_score": dual["mood_score"],
        "pareto_score": dual["pareto_score"],
        "ae_recall": dual["ae_recall"],
        "detected_aes": unique_detected,
        "behavior": avg_behavior,
        "nurse_time_s": round(nurse_time, 1),
        "t1": t1,
        "t2": t2,
    }


# ═══════════════════════════════════════════════════════════
# Worker process
# ═══════════════════════════════════════════════════════════

def _worker(
    model_name: str,
    model_path: str,
    gpu_id: int,
    scenarios: list[dict],
    seed: int,
    result_queue: Queue,
    tokenizer_path: str | None = None,
    adapter_path: str | None = None,
):
    """Worker process: load model on GPU, evaluate all scenarios."""
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    sys.path.insert(0, str(PROJECT_ROOT))

    nurse_fn = _make_local_nurse_fn(
        model_path, gpu_id,
        tokenizer_path=tokenizer_path,
        adapter_path=adapter_path,
    )

    results = []
    t_start = time.time()
    for i, sc in enumerate(scenarios):
        try:
            r = eval_one_scenario(sc, nurse_fn, seed)
            results.append(r)
            print(
                f"  [{model_name}] [{i+1:>2}/{len(scenarios)}] "
                f"{sc['drug_name'][:20]:20s} AEs={r['n_nv_aes']} "
                f"AE={r['ae_score']:.2f} Mood={r['mood_score']:.2f} "
                f"Par={r['pareto_score']:.2f} ({r['nurse_time_s']}s)",
                flush=True,
            )
        except Exception as e:
            import traceback
            print(f"  [{model_name}] [{i+1:>2}/{len(scenarios)}] ERROR: {e}", flush=True)
            traceback.print_exc()
            results.append({"error": str(e), "scenario_id": sc["scenario_id"]})

    elapsed = time.time() - t_start
    print(
        f"  [{model_name}] Done: {len(scenarios)} scenarios in {elapsed:.0f}s "
        f"({elapsed/max(len(scenarios),1):.1f}s/sc)",
        flush=True,
    )
    result_queue.put((model_name, results))


# ═══════════════════════════════════════════════════════════
# Summary printer
# ═══════════════════════════════════════════════════════════

def print_summary(scenarios, all_results, model_names, out_dir: Path):
    n = len(scenarios)
    print(f"\n{'='*90}")
    print(f"  COMPARISON: {' vs '.join(model_names)} ({n} scenarios)")
    print(f"{'='*90}")

    header = f"  {'Scenario':<35} {'AEs':>3}"
    for mn in model_names:
        header += f"  {'AE':>5} {'Mood':>5} {'Par':>5}"
    print(header)
    print(f"  {'-'*85}")

    for i, sc in enumerate(scenarios):
        label = f"{sc['drug_name'][:18]:18s} {sc['scenario_id'][-4:]}"
        n_aes = len(sc.get("gt_non_visual_aes", []))
        row = f"  {label:<35} {n_aes:>3}"

        pareto_vals = []
        for mn in model_names:
            r = all_results[mn][i]
            pareto_vals.append(r.get("pareto_score", -1) if "error" not in r else -1)

        best_par = max(pareto_vals)
        for j, mn in enumerate(model_names):
            r = all_results[mn][i]
            if "error" in r:
                row += "   ERR   ERR   ERR"
            else:
                marker = " *" if pareto_vals[j] == best_par and pareto_vals.count(best_par) == 1 else "  "
                row += f"  {r['ae_score']:>5.2f} {r['mood_score']:>5.2f} {r['pareto_score']:>4.2f}{marker}"
        print(row)

    # Averages
    print(f"\n  {'AVERAGE':<35} {'':>3}", end="")
    avg_data = {}
    for mn in model_names:
        valid = [r for r in all_results[mn] if "error" not in r]
        ae = sum(r["ae_score"] for r in valid) / max(len(valid), 1)
        mood = sum(r["mood_score"] for r in valid) / max(len(valid), 1)
        par = sum(r["pareto_score"] for r in valid) / max(len(valid), 1)
        avg_data[mn] = {"ae": ae, "mood": mood, "par": par, "n": len(valid)}
        print(f"  {ae:>5.3f} {mood:>5.3f} {par:>5.3f}  ", end="")
    print()

    # Per-drug averages
    drugs = sorted(set(sc["drug_name"] for sc in scenarios))
    for drug in drugs:
        idxs = [i for i, sc in enumerate(scenarios) if sc["drug_name"] == drug]
        print(f"  {drug[:35]:<35} {'':>3}", end="")
        for mn in model_names:
            valid = [all_results[mn][i] for i in idxs if "error" not in all_results[mn][i]]
            if valid:
                ae = sum(r["ae_score"] for r in valid) / len(valid)
                mood = sum(r["mood_score"] for r in valid) / len(valid)
                par = sum(r["pareto_score"] for r in valid) / len(valid)
                print(f"  {ae:>5.3f} {mood:>5.3f} {par:>5.3f}  ", end="")
            else:
                print(f"  {'N/A':>5} {'N/A':>5} {'N/A':>5}  ", end="")
        print()

    # Per AE-count
    ae_buckets = sorted(set(len(sc.get("gt_non_visual_aes", [])) for sc in scenarios))
    for bucket in ae_buckets:
        idxs = [i for i, sc in enumerate(scenarios) if len(sc.get("gt_non_visual_aes", [])) == bucket]
        print(f"  {'NV_AEs='+str(bucket):<35} {'':>3}", end="")
        for mn in model_names:
            valid = [all_results[mn][i] for i in idxs if "error" not in all_results[mn][i]]
            if valid:
                ae = sum(r["ae_score"] for r in valid) / len(valid)
                mood = sum(r["mood_score"] for r in valid) / len(valid)
                par = sum(r["pareto_score"] for r in valid) / len(valid)
                print(f"  {ae:>5.3f} {mood:>5.3f} {par:>5.3f}  ", end="")
            else:
                print(f"  {'N/A':>5} {'N/A':>5} {'N/A':>5}  ", end="")
        print()

    # Wins
    print(f"\n  Wins (Pareto):", end="")
    for mn in model_names:
        wins = sum(
            1 for i in range(n)
            if "error" not in all_results[mn][i]
            and all_results[mn][i]["pareto_score"] > max(
                (all_results[omn][i].get("pareto_score", 0)
                 for omn in model_names if omn != mn and "error" not in all_results[omn][i]),
                default=0,
            )
        )
        print(f"  {mn}={wins}", end="")
    ties = sum(
        1 for i in range(n)
        if all("error" not in all_results[mn][i] for mn in model_names)
        and len(set(round(all_results[mn][i]["pareto_score"], 3) for mn in model_names)) == 1
    )
    print(f"  ties={ties}")

    # Delta
    if len(model_names) == 2:
        m1, m2 = model_names
        d = avg_data[m2]["par"] - avg_data[m1]["par"]
        print(f"\n  Δ Pareto ({m2} - {m1}): {d:+.3f}")

    # Save
    out_file = out_dir / "eval_results.json"
    with open(out_file, "w") as f:
        json.dump({
            "model_names": model_names,
            "averages": avg_data,
            "per_scenario": {
                mn: [
                    {k: v for k, v in r.items() if k not in ("t1", "t2")}
                    for r in all_results[mn]
                ]
                for mn in model_names
            },
        }, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Results saved → {out_file}")

    # Save detail with conversations
    detail_file = out_dir / "eval_detail.json"
    with open(detail_file, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"  Detail saved → {detail_file}")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Compare MedGemma models")
    parser.add_argument("--rule-sets", nargs="+", required=True)
    parser.add_argument("--n-scenarios", type=int, default=21, help="Total scenarios (distributed across drugs)")
    parser.add_argument("--gpus", default="2,7", help="GPU IDs: baseline,anti-halluc")
    parser.add_argument("--seed", type=int, default=99)
    parser.add_argument("--repeats", type=int, default=3, help="T3 repeat count")
    parser.add_argument("--output-dir", default="data/eval_medgemma_compare")
    parser.add_argument("--model-a-path", default="google/medgemma-1.5-4b-it", help="Model A path")
    parser.add_argument("--model-b-path", default="/data2/workspace/samuel-huggingface/huggingface/hub/iter_004", help="Model B path")
    parser.add_argument("--model-a-name", default="baseline")
    parser.add_argument("--model-b-name", default="anti-halluc")
    parser.add_argument("--model-a-adapter", default=None, help="LoRA adapter for model A")
    parser.add_argument("--model-b-adapter", default=None, help="LoRA adapter for model B")
    parser.add_argument("--model-b-tokenizer", default=None, help="Tokenizer override for model B")
    args = parser.parse_args()

    global N_REPEAT
    N_REPEAT = args.repeats

    gpu_ids = [int(g) for g in args.gpus.split(",")]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_scenarios = []
    per_drug = max(1, args.n_scenarios // len(args.rule_sets))
    remainder = args.n_scenarios - per_drug * len(args.rule_sets)

    for idx, rs_path in enumerate(args.rule_sets):
        with open(rs_path) as f:
            rule_set = json.load(f)
        n = per_drug + (1 if idx < remainder else 0)
        scenarios = synthesize_scenarios(rule_set, n, seed=args.seed + idx * 100)
        all_scenarios.extend(scenarios)

    model_names = [args.model_a_name, args.model_b_name]
    model_paths = [args.model_a_path, args.model_b_path]
    adapter_paths = [args.model_a_adapter, args.model_b_adapter]
    tokenizer_paths = [None, args.model_b_tokenizer]

    print(f"\n{'='*70}")
    print(f"  MedGemma Comparison: {len(all_scenarios)} scenarios × 2 models (PARALLEL)")
    print(f"  {model_names[0]}: {model_paths[0]}" + (f" + adapter {adapter_paths[0]}" if adapter_paths[0] else ""))
    print(f"  {model_names[1]}: {model_paths[1]}" + (f" + adapter {adapter_paths[1]}" if adapter_paths[1] else ""))
    print(f"  GPUs: {model_names[0]}→{gpu_ids[0]}, {model_names[1]}→{gpu_ids[1]}")
    print(f"  N_REPEAT={N_REPEAT}, seed={args.seed}")
    print(f"{'='*70}")

    drugs_summary = {}
    for sc in all_scenarios:
        d = sc["drug_name"]
        drugs_summary.setdefault(d, []).append(sc)
    for d, scs in drugs_summary.items():
        ae_counts = [len(sc["gt_non_visual_aes"]) for sc in scs]
        print(f"  {d}: {len(scs)} scenarios, AE counts={ae_counts}")

    print(f"\n  Launching 2 workers in parallel...\n")

    result_queue = Queue()
    workers = []
    for i, (name, path) in enumerate(zip(model_names, model_paths)):
        p = Process(
            target=_worker,
            args=(name, path, gpu_ids[i], all_scenarios, args.seed, result_queue),
            kwargs={
                "tokenizer_path": tokenizer_paths[i],
                "adapter_path": adapter_paths[i],
            },
        )
        p.start()
        workers.append(p)

    all_results = {}
    for _ in range(len(workers)):
        name, results = result_queue.get()
        all_results[name] = results

    for p in workers:
        p.join()

    print_summary(all_scenarios, all_results, model_names, out_dir)


if __name__ == "__main__":
    main()
