"""Generate SFT + DPO Training Data for MedGemma Nurse Fine-tuning.

Pipeline:
  1. 다양한 (patient, day, mood) 조합으로 ExtendedCareAgent 실행
  2. Gemini 2.5 Flash를 Expert Nurse로 사용 → high-quality SFT 데이터
  3. MedGemma를 비교 Nurse로 사용 → preference pairs (DPO)
  4. 각 대화에 reward score 부여 → 상위 N% 필터링

Output:
  - sft_data.jsonl: {system, user, assistant, reward, metadata} per Nurse turn
  - dpo_pairs.jsonl: {prompt, chosen, rejected, reward_chosen, reward_rejected}

Usage:
    export $(cat .env | xargs) && python -m src.experiments.generate_sft_data \
        --run 20260219_050602_Padcev___Pembrolizumab_10pt_126d --gpu 4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.care_agent_extended import ExtendedCareAgent
from src.agents.llm_client import generate_json as gemini_generate_json
from src.engine.mood import MoodState, compute_interaction_quality, MOOD_DIMENSIONS
from src.engine.sampler import Sampler
from src.engine.reward import compute_reward, compute_preference
from src.experiments.compare_care_models import (
    load_medgemma,
    medgemma_generate_json,
    load_patient_and_day,
)

EXPERT_MODEL = "gemini-2.0-flash"

PATIENT_DAY_MATRIX = [
    ("PT-001", 73),
    ("PT-002", 45),
    ("PT-004", 48),
    ("PT-005", 50),
    ("PT-006", 42),
    ("PT-007", 59),
    ("PT-008", 104),
    ("PT-009", 32),
]

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


def make_cross_model_fn(patient_fn, nurse_fn):
    """Patient=Gemini, Nurse=specified model. Extended protocol has variable call count."""
    state = {"call_count": 0}

    def hybrid_fn(system_prompt, user_prompt, **kwargs):
        state["call_count"] += 1
        kwargs.pop("model", None)

        is_nurse = _is_nurse_turn(system_prompt)

        if is_nurse:
            return nurse_fn(system_prompt, user_prompt, **kwargs)
        return patient_fn(system_prompt, user_prompt, **kwargs)

    return hybrid_fn


def _is_nurse_turn(system_prompt: str) -> bool:
    """Determine if this is a nurse turn based on prompt content."""
    nurse_indicators = [
        "You are an AI nurse",
        "AI nurse conducting",
        "AI nurse making",
        "FINAL assessment",
        "EXTENDED follow-up",
    ]
    return any(ind in system_prompt for ind in nurse_indicators)


def run_extended_call(
    patient: dict,
    rule_set: dict,
    day_data: dict,
    last_hr: dict | None,
    mood_scenario: str,
    nurse_fn,
    nurse_label: str,
    seed: int,
    use_dynamic_mood: bool = True,
) -> dict:
    """Run one extended care call and return full record + reward."""
    mood_override = MOOD_SCENARIOS[mood_scenario]
    day = day_data["day"]
    pid = patient.get("patient_id", "?")
    persona_type = patient.get("persona", {}).get("type", "stoic_minimizer")

    mood = MoodState(persona_type=persona_type, seed=seed)
    for dim, val in mood_override.items():
        if dim in mood.state:
            mood.state[dim] = val

    sampler = Sampler(seed=seed)
    agent = ExtendedCareAgent(
        patient=patient,
        rule_set=rule_set,
        mood=mood,
        sampler=sampler,
        model=nurse_label,
        use_dynamic_mood=use_dynamic_mood,
    )

    cross_fn = make_cross_model_fn(gemini_generate_json, nurse_fn)

    t0 = time.time()
    with patch("src.agents.care_agent.generate_json", side_effect=cross_fn), \
         patch("src.agents.care_agent_extended.generate_json", side_effect=cross_fn):
        care_record = agent.conduct_extended_call(
            day=day,
            day_result=day_data,
            day_results=[day_data],
            last_hospital_record=last_hr,
        )
    elapsed = time.time() - t0

    gt_aes = day_data.get("objective", {}).get("active_aes", [])
    reward_info = compute_reward(care_record, gt_aes)

    return {
        "care_record": care_record,
        "reward": reward_info,
        "elapsed_sec": round(elapsed, 2),
        "patient_id": pid,
        "day": day,
        "mood_scenario": mood_scenario,
        "nurse_model": nurse_label,
    }


def extract_sft_examples(result: dict, day_data: dict) -> list[dict]:
    """care_record에서 Nurse 턴별 SFT 학습 예제를 추출한다."""
    care_record = result["care_record"]
    turns = care_record.get("turns", [])
    reward_score = result["reward"]["reward"]

    examples = []
    for i, turn in enumerate(turns):
        if turn["role"] != "nurse":
            continue

        prev_patient = None
        for j in range(i - 1, -1, -1):
            if turns[j]["role"] == "patient":
                prev_patient = turns[j]["content"]
                break

        if prev_patient is None:
            continue

        nurse_hidden = {"omitted_symptoms", "_turn", "_fallback"}
        patient_visible = {
            k: v for k, v in prev_patient.items() if k not in nurse_hidden
        }

        example = {
            "turn_number": turn["turn"],
            "system_context": {
                "patient_id": result["patient_id"],
                "day": result["day"],
                "mood_scenario": result["mood_scenario"],
                "mood_state": care_record.get("mood_before", {}),
            },
            "user_input": {
                "patient_said": patient_visible,
                "conversation_history_length": i,
            },
            "assistant_output": turn["content"],
            "reward": reward_score,
            "nurse_model": result["nurse_model"],
            "turn_evaluation": None,
        }

        evaluations = care_record.get("turn_evaluations", [])
        for ev in evaluations:
            if ev.get("turn_number") == turn["turn"]:
                example["turn_evaluation"] = {
                    "overall_quality": ev.get("overall_quality", 0),
                    "empathy_quality": ev.get("empathy_quality", 0),
                    "information_yield": ev.get("information_yield", 0),
                    "oars_scores": ev.get("oars_scores", {}),
                }
                break

        examples.append(example)

    return examples


def extract_dpo_pair(
    expert_result: dict,
    baseline_result: dict,
    gt_aes: list[dict],
) -> dict | None:
    """Expert vs Baseline에서 DPO preference pair를 생성한다."""
    pref = compute_preference(
        expert_result["care_record"],
        baseline_result["care_record"],
        gt_aes,
    )

    if pref["margin"] < 0.02:
        return None

    expert_turns = expert_result["care_record"].get("turns", [])
    baseline_turns = baseline_result["care_record"].get("turns", [])

    expert_nurse_turns = [t for t in expert_turns if t["role"] == "nurse"]
    baseline_nurse_turns = [t for t in baseline_turns if t["role"] == "nurse"]

    if not expert_nurse_turns or not baseline_nurse_turns:
        return None

    chosen_turns = expert_nurse_turns if pref["chosen"] == "a" else baseline_nurse_turns
    rejected_turns = baseline_nurse_turns if pref["chosen"] == "a" else expert_nurse_turns

    return {
        "patient_id": expert_result["patient_id"],
        "day": expert_result["day"],
        "mood_scenario": expert_result["mood_scenario"],
        "chosen_model": expert_result["nurse_model"] if pref["chosen"] == "a" else baseline_result["nurse_model"],
        "rejected_model": baseline_result["nurse_model"] if pref["chosen"] == "a" else expert_result["nurse_model"],
        "chosen_reward": pref["reward_a"] if pref["chosen"] == "a" else pref["reward_b"],
        "rejected_reward": pref["reward_b"] if pref["chosen"] == "a" else pref["reward_a"],
        "margin": pref["margin"],
        "chosen_nurse_turns": [t["content"] for t in chosen_turns],
        "rejected_nurse_turns": [t["content"] for t in rejected_turns],
        "component_comparison": pref["component_comparison"],
    }


def main():
    parser = argparse.ArgumentParser(description="Generate SFT + DPO training data")
    parser.add_argument("--run", required=True)
    parser.add_argument("--gpu", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenarios", nargs="+", default=list(MOOD_SCENARIOS.keys()))
    parser.add_argument("--patients", nargs="+", default=None)
    parser.add_argument("--skip-medgemma", action="store_true",
                        help="Only generate expert (Gemini) data, skip DPO pairs")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()

    matrix = PATIENT_DAY_MATRIX
    if args.patients:
        matrix = [(p, d) for p, d in matrix if p in args.patients]

    out_dir = Path(args.output_dir) if args.output_dir else (
        PROJECT_ROOT / "data" / "training"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    total_expert = len(matrix) * len(args.scenarios)
    total_baseline = 0 if args.skip_medgemma else total_expert
    total = total_expert + total_baseline

    print(f"{'='*80}")
    print(f"  SFT + DPO Training Data Generation")
    print(f"  Expert model:   Gemini 2.5 Flash")
    print(f"  Baseline model: MedGemma 1.5 4B {'(skipped)' if args.skip_medgemma else ''}")
    print(f"  Patients: {len(matrix)}  Scenarios: {len(args.scenarios)}")
    print(f"  Total calls: {total} (expert={total_expert}, baseline={total_baseline})")
    print(f"  Dynamic mood: ON (Gemini 2.5 Flash as Judge)")
    print(f"  Output: {out_dir}")
    print(f"{'='*80}")

    if not args.skip_medgemma:
        load_medgemma(gpu_id=args.gpu)

    sft_examples: list[dict] = []
    dpo_pairs: list[dict] = []
    all_results: list[dict] = []
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
            seed = args.seed + hash(f"{pid}_{sc_name}") % 10000

            # ═══ Expert (Gemini 2.5 Flash) ═══
            completed += 1
            print(f"\n  [{completed}/{total}] {pid} d{day} {sc_name:>12} × Expert  ", end="", flush=True)

            def expert_nurse_fn(system_prompt, user_prompt, **kwargs):
                return gemini_generate_json(system_prompt, user_prompt, **kwargs)

            try:
                expert_result = run_extended_call(
                    patient, rule_set, day_data, last_hr,
                    sc_name, expert_nurse_fn, EXPERT_MODEL,
                    seed, use_dynamic_mood=True,
                )
                r = expert_result["reward"]
                cr = expert_result["care_record"]
                print(f"→ reward={r['reward']:.3f} turns={cr['n_turns']} "
                      f"grade={cr.get('conversation_outcome', {}).get('overall_grade', '?')} "
                      f"({expert_result['elapsed_sec']:.1f}s)")

                expert_sft = extract_sft_examples(expert_result, day_data)
                sft_examples.extend(expert_sft)
                all_results.append(expert_result)
            except Exception as e:
                print(f"⚠ ERROR: {e}")
                import traceback; traceback.print_exc()
                expert_result = None

            # ═══ Baseline (MedGemma) — for DPO pairs ═══
            if not args.skip_medgemma and expert_result:
                completed += 1
                print(f"  [{completed}/{total}] {pid} d{day} {sc_name:>12} × MedGemma", end="", flush=True)

                try:
                    baseline_result = run_extended_call(
                        patient, rule_set, day_data, last_hr,
                        sc_name, medgemma_generate_json, "medgemma-1.5-4b-it",
                        seed, use_dynamic_mood=True,
                    )
                    r = baseline_result["reward"]
                    cr = baseline_result["care_record"]
                    print(f"→ reward={r['reward']:.3f} turns={cr['n_turns']} "
                          f"grade={cr.get('conversation_outcome', {}).get('overall_grade', '?')} "
                          f"({baseline_result['elapsed_sec']:.1f}s)")

                    baseline_sft = extract_sft_examples(baseline_result, day_data)
                    sft_examples.extend(baseline_sft)
                    all_results.append(baseline_result)

                    pair = extract_dpo_pair(expert_result, baseline_result, gt_aes)
                    if pair:
                        dpo_pairs.append(pair)
                        print(f"    DPO: chosen={pair['chosen_model'][:10]} margin={pair['margin']:.3f}")
                    else:
                        print(f"    DPO: margin too small, skipped")
                except Exception as e:
                    print(f"⚠ ERROR: {e}")
                    import traceback; traceback.print_exc()

    # ═══ Save ═══
    print(f"\n{'='*80}")
    print(f"  DATA GENERATION COMPLETE")
    print(f"{'='*80}")
    print(f"  SFT examples: {len(sft_examples)}")
    print(f"  DPO pairs:    {len(dpo_pairs)}")

    # Filter SFT: only top reward examples
    if sft_examples:
        rewards = [e["reward"] for e in sft_examples]
        threshold = sorted(rewards)[max(0, int(len(rewards) * 0.3))]
        high_quality = [e for e in sft_examples if e["reward"] >= threshold]
        print(f"  SFT after filtering (top 70%): {len(high_quality)}")

        sft_path = out_dir / "sft_data.jsonl"
        with open(sft_path, "w") as f:
            for ex in high_quality:
                f.write(json.dumps(ex, ensure_ascii=False, default=str) + "\n")
        print(f"  → {sft_path}")

    if dpo_pairs:
        dpo_path = out_dir / "dpo_pairs.jsonl"
        with open(dpo_path, "w") as f:
            for pair in dpo_pairs:
                f.write(json.dumps(pair, ensure_ascii=False, default=str) + "\n")
        print(f"  → {dpo_path}")

    # Summary stats
    expert_rewards = [r["reward"]["reward"] for r in all_results if EXPERT_MODEL in r["nurse_model"]]
    baseline_rewards = [r["reward"]["reward"] for r in all_results if "medgemma" in r["nurse_model"]]

    if expert_rewards:
        print(f"\n  Expert rewards:   mean={sum(expert_rewards)/len(expert_rewards):.3f} "
              f"min={min(expert_rewards):.3f} max={max(expert_rewards):.3f}")
    if baseline_rewards:
        print(f"  Baseline rewards: mean={sum(baseline_rewards)/len(baseline_rewards):.3f} "
              f"min={min(baseline_rewards):.3f} max={max(baseline_rewards):.3f}")

    if dpo_pairs:
        margins = [p["margin"] for p in dpo_pairs]
        expert_wins = sum(1 for p in dpo_pairs if "gemini" in p["chosen_model"].lower() or "2.5" in p["chosen_model"])
        print(f"\n  DPO: Expert wins {expert_wins}/{len(dpo_pairs)} "
              f"(avg margin={sum(margins)/len(margins):.3f})")

    meta_path = out_dir / "generation_meta.json"
    with open(meta_path, "w") as f:
        json.dump({
            "expert_model": EXPERT_MODEL,
            "baseline_model": "medgemma-1.5-4b-it",
            "n_patients": len(matrix),
            "n_scenarios": len(args.scenarios),
            "n_sft_examples": len(sft_examples),
            "n_sft_filtered": len(high_quality) if sft_examples else 0,
            "n_dpo_pairs": len(dpo_pairs),
            "expert_rewards": expert_rewards,
            "baseline_rewards": baseline_rewards,
        }, f, indent=2, default=str)
    print(f"  → {meta_path}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
