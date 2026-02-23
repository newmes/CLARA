"""Evaluate Nurse models: Gemini 2.0 Flash vs MedGemma Baseline vs SFT.

Parallel evaluation: 3 processes run simultaneously.
  - Process 1 (CPU): Gemini 2.0 Flash (API only)
  - Process 2 (GPU A): MedGemma Baseline
  - Process 3 (GPU B): MedGemma SFT

Usage:
    python -m src.experiments.eval_finetuned \
        --run 20260220_075422_Padcev___Pembrolizumab_10pt_126d \
        --n-scenarios 15 --gpus 6,7
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.care_agent import CareAgent
from src.agents.llm_client import generate_json as gemini_generate_json
from src.engine.mood import MoodState, compute_interaction_quality, compute_grade_distortion
from src.engine.sampler import Sampler
from src.experiments.compare_care_models import load_patient_and_day
from src.experiments.generate_sft_selfplay import (
    MOOD_SCENARIOS,
    _patient_fn,
    run_branch,
    aggregate_branch,
)

N_REPEAT = 1


def _gemini_nurse_fn(system_prompt: str, user_prompt: str, **kwargs) -> dict:
    kwargs.pop("caller", None)
    kwargs.pop("model", None)
    return gemini_generate_json(
        system_prompt, user_prompt,
        model="gemini-2.0-flash", caller="eval_nurse_gemini", **kwargs,
    )


def _make_local_nurse_fn(model_id: str, adapter_path: str | None, gpu_id: int):
    """Create a nurse function backed by a local MedGemma model on a specific GPU."""
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import PeftModel

    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    print(f"    Loading {model_id} on GPU {gpu_id}...", end=" ", flush=True)
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=torch.bfloat16, device_map={"": 0},
    )
    if adapter_path:
        print(f"+ adapter {Path(adapter_path).name}...", end=" ", flush=True)
        model = PeftModel.from_pretrained(model, adapter_path)
        model = model.merge_and_unload()
    model.eval()
    print(f"({time.time()-t0:.1f}s, {torch.cuda.memory_allocated(0)/1e9:.1f}GB)")

    def _generate(system_prompt: str, user_prompt: str, **kwargs) -> dict:
        kwargs.pop("model", None)
        kwargs.pop("caller", None)
        chat = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt + "\n\nRespond with valid JSON only."},
        ]
        input_text = tokenizer.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(input_text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            outputs = model.generate(
                **inputs, max_new_tokens=min(kwargs.get("max_tokens", 4096), 4096),
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
                if c == "{": depth += 1
                elif c == "}": depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            json_str = json_str[start:end]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {"_raw": raw[:500], "_parse_error": True}

    return _generate


def _setup_scenario(run_id, sc, seed):
    """Build mood/agent state for a scenario. Returns (agent, active_aes, quality, grade_distortion)."""
    patient, rule_set, day_data, last_hr = load_patient_and_day(run_id, sc["patient_id"], sc["day"])
    persona_type = patient.get("persona", {}).get("type", "stoic_minimizer")
    day = day_data["day"]

    mood = MoodState(persona_type=persona_type, seed=seed)
    for dim, val in MOOD_SCENARIOS[sc["mood_scenario"]].items():
        if dim in mood.state:
            mood.state[dim] = val

    sampler = Sampler(seed=seed)
    quality = compute_interaction_quality(mood)
    grade_distortion = compute_grade_distortion(mood)

    cdisc_aes = day_data.get("AE", [])
    active_aes = [{"ae": ae.get("AETERM", ""), "grade": ae.get("AETOXGR", 0)} for ae in cdisc_aes]
    max_ae_grade = max((ae.get("grade", 0) for ae in active_aes), default=0)
    if isinstance(max_ae_grade, str):
        max_ae_grade = int(max_ae_grade) if max_ae_grade.isdigit() else 0
    if max_ae_grade >= 3:
        mood.apply_defensiveness_override(max_ae_grade)
        quality = compute_interaction_quality(mood)

    agent = CareAgent(
        patient=patient, rule_set=rule_set, mood=mood,
        sampler=sampler, model="medgemma-1.5-4b-it",
    )
    agent._last_hospital_record = last_hr

    return agent, day, day_data, active_aes, quality, grade_distortion


def eval_scenario(run_id, sc, seed, nurse_fn, t1_cache=None):
    """Evaluate a single scenario. If t1_cache provided, use cached T1."""
    agent, day, day_data, active_aes, quality, grade_distortion = _setup_scenario(run_id, sc, seed)

    key = (sc["patient_id"], sc["day"])
    if t1_cache and key in t1_cache:
        t1 = t1_cache[key]
    else:
        with patch("src.agents.care_agent.generate_json", side_effect=_patient_fn):
            t1 = agent._patient_initial_report(day, day_data, quality, grade_distortion)
        if t1_cache is not None:
            t1_cache[key] = t1

    t0 = time.time()
    with patch("src.agents.care_agent.generate_json", side_effect=nurse_fn):
        t2 = agent._nurse_followup_questions(day, t1, quality)
    nurse_time = time.time() - t0

    branch = run_branch(agent, t2, day, day_data, quality, grade_distortion, N_REPEAT)
    agg = aggregate_branch(branch, active_aes)

    return {
        "patient_id": sc["patient_id"], "day": sc["day"],
        "mood_scenario": sc["mood_scenario"], "n_aes": len(active_aes),
        "ae_score": agg["dual_objective"]["ae_score"],
        "mood_score": agg["dual_objective"]["mood_score"],
        "pareto_score": agg["dual_objective"]["pareto_score"],
        "detected_aes": agg["dual_objective"]["detected_aes"],
        "behavior": agg["avg_behavior"],
        "nurse_time_s": round(nurse_time, 1),
    }


def _worker_api(model_name, run_id, scenarios, seed, result_queue):
    """Worker for API-based model (Gemini). No GPU needed."""
    sys.path.insert(0, str(PROJECT_ROOT))
    results = []
    t1_cache = {}
    t_start = time.time()
    for i, sc in enumerate(scenarios):
        try:
            r = eval_scenario(run_id, sc, seed, _gemini_nurse_fn, t1_cache)
            results.append(r)
            print(f"  [{model_name}] [{i+1:>2}/{len(scenarios)}] {sc['patient_id']} d{sc['day']:>3d} "
                  f"AE={r['ae_score']:.2f} Mood={r['mood_score']:.2f} Par={r['pareto_score']:.2f} "
                  f"({r['nurse_time_s']}s)")
        except Exception as e:
            print(f"  [{model_name}] [{i+1:>2}/{len(scenarios)}] ERROR: {e}")
            results.append({"error": str(e), **sc})
    elapsed = time.time() - t_start
    print(f"  [{model_name}] Done: {len(scenarios)} scenarios in {elapsed:.0f}s ({elapsed/len(scenarios):.1f}s/sc)")
    result_queue.put((model_name, results))


def _worker_local(model_name, model_id, adapter_path, gpu_id, run_id, scenarios, seed, result_queue):
    """Worker for local MedGemma model on a specific GPU."""
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    sys.path.insert(0, str(PROJECT_ROOT))
    nurse_fn = _make_local_nurse_fn(model_id, adapter_path, gpu_id)
    results = []
    t1_cache = {}
    t_start = time.time()
    for i, sc in enumerate(scenarios):
        try:
            r = eval_scenario(run_id, sc, seed, nurse_fn, t1_cache)
            results.append(r)
            print(f"  [{model_name}] [{i+1:>2}/{len(scenarios)}] {sc['patient_id']} d{sc['day']:>3d} "
                  f"AE={r['ae_score']:.2f} Mood={r['mood_score']:.2f} Par={r['pareto_score']:.2f} "
                  f"({r['nurse_time_s']}s)")
        except Exception as e:
            print(f"  [{model_name}] [{i+1:>2}/{len(scenarios)}] ERROR: {e}")
            import traceback; traceback.print_exc()
            results.append({"error": str(e), **sc})
    elapsed = time.time() - t_start
    print(f"  [{model_name}] Done: {len(scenarios)} scenarios in {elapsed:.0f}s ({elapsed/len(scenarios):.1f}s/sc)")
    result_queue.put((model_name, results))


def select_eval_scenarios(run_id: str, n: int = 15, seed: int = 99) -> list[dict]:
    """Pick diverse eval scenarios ensuring mix of AE counts and moods."""
    runs_dir = PROJECT_ROOT / "data" / "runs"
    run_path = runs_dir / run_id
    if not run_path.exists():
        run_path = runs_dir / "old" / run_id
    patients_dir = run_path / "patients"
    sim_dir = run_path / "simulations"

    train_keys = set()
    import glob
    import re
    for f in glob.glob(str(PROJECT_ROOT / "data/training_selfplay_v2/selfplay_*.json")):
        m = re.search(r"selfplay_(PT-\d+)_d(\d+)_", f)
        if m:
            train_keys.add((m.group(1), int(m.group(2))))

    all_candidates = []
    moods = list(MOOD_SCENARIOS.keys())
    rng = random.Random(seed)

    for pf in sorted(patients_dir.glob("PT-*.json")):
        pid = pf.stem
        sim_file = sim_dir / f"{pid}_natural.jsonl"
        if not sim_file.exists():
            continue
        with open(sim_file) as f:
            for line in f:
                d = json.loads(line)
                day = d["day"]
                if (pid, day) in train_keys:
                    continue
                n_aes = len(d.get("AE", []))
                all_candidates.append({
                    "patient_id": pid, "day": day, "n_aes": n_aes,
                    "mood_scenario": rng.choice(moods),
                })

    rng.shuffle(all_candidates)
    by_aes = {}
    for c in all_candidates:
        bucket = min(c["n_aes"], 5)
        by_aes.setdefault(bucket, []).append(c)

    selected = []
    buckets = sorted(by_aes.keys())
    round_robin = 0
    while len(selected) < n and buckets:
        b = buckets[round_robin % len(buckets)]
        if by_aes[b]:
            selected.append(by_aes[b].pop(0))
        else:
            buckets.remove(b)
        round_robin += 1

    print(f"  AE distribution: {dict((b, sum(1 for s in selected if min(s['n_aes'],5)==b)) for b in range(6) if any(min(s['n_aes'],5)==b for s in selected))}")
    print(f"  Excluded {len(train_keys)} training (patient,day) pairs")
    return selected[:n]


def print_summary(scenarios, all_results, model_names):
    """Print comparison table."""
    col_labels = f"  {'':33}"
    for mn in model_names:
        col_labels += f"  {'---'+mn[:8]+'---':>17}"
    print(f"\n{'='*80}")
    print(col_labels)
    print(f"  COMPARISON: {' vs '.join(model_names)} ({len(scenarios)} scenarios)")
    print(f"{'='*80}")

    header = f"  {'Scenario':<30} {'AEs':>3}"
    for mn in model_names:
        header += f"  {'AE':>5} {'Mood':>5} {'Par':>5}"
    print(header)
    print(f"  {'-'*77}")

    for i, sc in enumerate(scenarios):
        label = f"{sc['patient_id']} d{sc['day']} {sc['mood_scenario'][:8]}"
        row = f"  {label:<30} {sc['n_aes']:>3}"
        pareto_vals = [all_results[mn][i].get("pareto_score", -1) for mn in model_names]
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
    print(f"\n  {'AVERAGE':<30} {'':>3}", end="")
    for mn in model_names:
        valid = [r for r in all_results[mn] if "error" not in r]
        ae = sum(r["ae_score"] for r in valid) / max(len(valid), 1)
        mood = sum(r["mood_score"] for r in valid) / max(len(valid), 1)
        par = sum(r["pareto_score"] for r in valid) / max(len(valid), 1)
        print(f"  {ae:>5.3f} {mood:>5.3f} {par:>5.3f}  ", end="")
    print()

    # Per-AE-bucket
    ae_buckets = sorted(set(min(sc["n_aes"], 5) for sc in scenarios))
    for bucket in ae_buckets:
        idxs = [i for i, sc in enumerate(scenarios) if min(sc["n_aes"], 5) == bucket]
        print(f"  {'AEs='+str(bucket):<30} {'':>3}", end="")
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
        wins = sum(1 for i in range(len(scenarios))
                   if "error" not in all_results[mn][i]
                   and all_results[mn][i]["pareto_score"] > max(
                       (all_results[omn][i].get("pareto_score", 0)
                        for omn in model_names if omn != mn and "error" not in all_results[omn][i]),
                       default=0))
        print(f"  {mn}={wins}", end="")
    print()

    # Gap analysis
    if all(mn in all_results for mn in ["gemini-2.0", "baseline", "sft"]):
        def avg_par(mn):
            v = [r for r in all_results[mn] if "error" not in r]
            return sum(r["pareto_score"] for r in v) / max(len(v), 1)
        g, b, s = avg_par("gemini-2.0"), avg_par("baseline"), avg_par("sft")
        gap = g - b
        closed = s - b
        pct = (closed / gap * 100) if gap > 0 else 0
        print(f"\n  Gap analysis (Pareto):")
        print(f"    Gemini → Baseline gap: {gap:+.3f}")
        print(f"    SFT improvement:       {closed:+.3f} ({pct:.0f}% of gap closed)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--n-scenarios", type=int, default=15)
    parser.add_argument("--gpus", default="6,7", help="Comma-separated GPU IDs for baseline,sft")
    parser.add_argument("--seed", type=int, default=99)
    args = parser.parse_args()

    gpu_ids = [int(g) for g in args.gpus.split(",")]
    if len(gpu_ids) < 2:
        gpu_ids = [gpu_ids[0], gpu_ids[0]]

    model_id = "google/medgemma-1.5-4b-it"
    ckpt_dir = PROJECT_ROOT / "data" / "training_selfplay_v2" / "checkpoints"
    sft_path = str(ckpt_dir / "sft_final")

    scenarios = select_eval_scenarios(args.run, args.n_scenarios, args.seed)
    model_names = ["gemini-2.0", "baseline", "sft"]

    print(f"\n{'='*70}")
    print(f"  Nurse Eval: {len(scenarios)} scenarios × 3 models (PARALLEL)")
    print(f"  Models: {model_names}")
    print(f"  GPUs: baseline→{gpu_ids[0]}, sft→{gpu_ids[1]}, gemini→API")
    print(f"  N_REPEAT={N_REPEAT}")
    print(f"{'='*70}")
    for i, s in enumerate(scenarios):
        print(f"  [{i+1:>2}] {s['patient_id']} day {s['day']:>3d} mood={s['mood_scenario']:<12} AEs={s['n_aes']}")
    print()

    all_results = {}
    t_start = time.time()

    # 1. Gemini (API only, fast)
    print(f"\n  [gemini-2.0] Running (API only)...")
    t1_cache = {}
    gemini_results = []
    for i, sc in enumerate(scenarios):
        try:
            r = eval_scenario(args.run, sc, args.seed, _gemini_nurse_fn, t1_cache)
            gemini_results.append(r)
            print(f"  [gemini] [{i+1:>2}/{len(scenarios)}] {sc['patient_id']} d{sc['day']:>3d} "
                  f"AE={r['ae_score']:.2f} Mood={r['mood_score']:.2f} Par={r['pareto_score']:.2f} ({r['nurse_time_s']}s)")
        except Exception as e:
            print(f"  [gemini] [{i+1:>2}/{len(scenarios)}] ERROR: {e}")
            gemini_results.append({"error": str(e), **sc})
    all_results["gemini-2.0"] = gemini_results
    print(f"  [gemini] Done in {time.time()-t_start:.0f}s")

    # 2. Baseline (local, GPU)
    gpu_id = gpu_ids[0]
    print(f"\n  [baseline] Loading on GPU {gpu_id}...")
    nurse_fn_base = _make_local_nurse_fn(model_id, None, gpu_id)
    base_results = []
    t_base = time.time()
    for i, sc in enumerate(scenarios):
        try:
            r = eval_scenario(args.run, sc, args.seed, nurse_fn_base, t1_cache)
            base_results.append(r)
            print(f"  [baseline] [{i+1:>2}/{len(scenarios)}] {sc['patient_id']} d{sc['day']:>3d} "
                  f"AE={r['ae_score']:.2f} Mood={r['mood_score']:.2f} Par={r['pareto_score']:.2f} ({r['nurse_time_s']}s)")
        except Exception as e:
            print(f"  [baseline] [{i+1:>2}/{len(scenarios)}] ERROR: {e}")
            base_results.append({"error": str(e), **sc})
    all_results["baseline"] = base_results
    print(f"  [baseline] Done in {time.time()-t_base:.0f}s")

    # Free GPU before loading SFT
    import gc
    del nurse_fn_base
    import torch
    torch.cuda.empty_cache()
    gc.collect()

    # 3. SFT (local, same GPU)
    print(f"\n  [sft] Loading on GPU {gpu_id}...")
    nurse_fn_sft = _make_local_nurse_fn(model_id, sft_path, gpu_id)
    sft_results = []
    t_sft = time.time()
    for i, sc in enumerate(scenarios):
        try:
            r = eval_scenario(args.run, sc, args.seed, nurse_fn_sft, t1_cache)
            sft_results.append(r)
            print(f"  [sft] [{i+1:>2}/{len(scenarios)}] {sc['patient_id']} d{sc['day']:>3d} "
                  f"AE={r['ae_score']:.2f} Mood={r['mood_score']:.2f} Par={r['pareto_score']:.2f} ({r['nurse_time_s']}s)")
        except Exception as e:
            print(f"  [sft] [{i+1:>2}/{len(scenarios)}] ERROR: {e}")
            sft_results.append({"error": str(e), **sc})
    all_results["sft"] = sft_results
    print(f"  [sft] Done in {time.time()-t_sft:.0f}s")

    total_time = time.time() - t_start
    print(f"\n  Total: {total_time:.0f}s")

    print_summary(scenarios, all_results, model_names)

    out_file = PROJECT_ROOT / "data" / "training_selfplay_v2" / "eval_results.json"
    with open(out_file, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Results saved → {out_file}")


if __name__ == "__main__":
    main()
