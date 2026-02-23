"""Train MedGemma Nurse — SFT + DPO Fine-tuning Pipeline

2-Step training:
  Step 1 (SFT): Supervised fine-tuning on expert Nurse turns from Gemini 2.5 Flash
  Step 2 (DPO): Direct Preference Optimization on (chosen, rejected) pairs

Uses LoRA for parameter-efficient fine-tuning (~1% of parameters).

Usage:
    # Step 1: SFT
    python -m src.experiments.train_medgemma_nurse \
        --step sft --data-dir data/training --gpu 4 --epochs 3

    # Step 2: DPO (after SFT)
    python -m src.experiments.train_medgemma_nurse \
        --step dpo --data-dir data/training --gpu 4 --epochs 1 \
        --base-model data/training/checkpoints/sft_final

    # Both steps sequentially
    python -m src.experiments.train_medgemma_nurse \
        --step both --data-dir data/training --gpu 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _patch_medgemma_forward(model):
    """Patch Gemma3 to auto-inject token_type_ids (all zeros for text-only).
    MedGemma (Gemma 3) requires this but HF Trainer/DPOTrainer don't provide it.
    Patches at the Gemma3Model level to work through PEFT/Accelerate wrappers.
    """
    import torch as _torch
    from transformers.models.gemma3 import modeling_gemma3

    _orig_create_mask = modeling_gemma3.create_causal_mask_mapping

    def _patched_create_mask(config, inputs_embeds, attention_mask=None,
                             cache_position=None, past_key_values=None,
                             position_ids=None, token_type_ids=None,
                             pixel_values=None, is_training=False,
                             is_first_iteration=None, **kwargs):
        if token_type_ids is None and inputs_embeds is not None:
            token_type_ids = _torch.zeros(inputs_embeds.shape[:2], dtype=_torch.long, device=inputs_embeds.device)
        return _orig_create_mask(
            config, inputs_embeds, attention_mask, cache_position, past_key_values,
            position_ids, token_type_ids, pixel_values, is_training, is_first_iteration, **kwargs
        )

    modeling_gemma3.create_causal_mask_mapping = _patched_create_mask

    _orig_model_forward = modeling_gemma3.Gemma3Model.forward

    def _patched_model_forward(self, *args, **kwargs):
        if "token_type_ids" not in kwargs or kwargs["token_type_ids"] is None:
            input_ids = kwargs.get("input_ids")
            if input_ids is None and args:
                input_ids = args[0]
            if input_ids is not None:
                kwargs["token_type_ids"] = _torch.zeros_like(input_ids)
        return _orig_model_forward(self, *args, **kwargs)

    modeling_gemma3.Gemma3Model.forward = _patched_model_forward
    return model


def build_sft_prompt(example: dict) -> dict:
    """Convert an SFT example to chat format for training.

    v3 format: includes drug_ae_profile + visual_assessment in system prompt,
    matching the inference-time prompt structure from build_nurse_system_prompt().
    Also supports legacy formats.
    """
    if "context" in example and "expert_response" in example:
        ctx = example["context"]
        patient_said = ctx.get("patient_said", {})
        nurse_output = example["expert_response"]
        turn = example.get("turn", 2)
        turn_type = example.get("turn_type", "followup")
        mood = ctx.get("patient_mood", {})
    else:
        ctx = example.get("system_context", {})
        patient_said = example.get("user_input", {}).get("patient_said", {})
        nurse_output = example.get("assistant_output", {})
        turn = example.get("turn_number", example.get("turn", 2))
        turn_type = example.get("turn_type", "followup")
        mood = ctx.get("mood_state", ctx.get("patient_mood", {}))

    drug_name = ctx.get("drug_name", "")
    indication = ctx.get("indication", "")
    vis_assess = ctx.get("visual_assessment", {})
    drug_profile = ctx.get("drug_ae_profile", [])

    vis_findings = vis_assess.get("findings", []) if isinstance(vis_assess, dict) else []
    vis_text = json.dumps(vis_findings, ensure_ascii=False, indent=2) if vis_findings else "No significant visual findings."
    gen_obs = "; ".join(vis_assess.get("general_observations", [])) if isinstance(vis_assess, dict) else ""

    profile_lines = []
    for ae in (drug_profile[:6] if drug_profile else []):
        if isinstance(ae, dict):
            profile_lines.append(f"  - {ae.get('ae_term','')} ({ae.get('incidence_pct','')}): {ae.get('common_symptoms','')}")
    profile_text = "\n".join(profile_lines) if profile_lines else "Not available."

    if drug_name:
        system_msg = (
            f"You are an AI nurse conducting Turn 2 of a daily video call with a cancer patient.\n"
            f"You've just heard the patient's initial report and received visual analysis from a separate system.\n\n"
            f"CLINICAL CONTEXT:\n- Drug: {drug_name}\n- Indication: {indication}\n\n"
            f"VISUAL ASSESSMENT (from MedGemma-Vision front-end):\n{vis_text}\nGeneral: {gen_obs}\n\n"
            f"NON-VISUAL AE PROFILE FOR THIS DRUG (these require conversation to detect):\n{profile_text}\n\n"
            f"YOUR OBJECTIVES (dual):\n"
            f"  (a) DETECT non-visual AEs through conversation — ask about specific symptoms from the drug profile\n"
            f"  (b) MAINTAIN patient comfort — be warm, empathetic, build trust\n\n"
            f"STRATEGY:\n"
            f"1. Acknowledge what the patient shared (empathy first)\n"
            f"2. If visual findings exist, acknowledge them naturally\n"
            f"3. Ask about TOP non-visual AEs for this drug — use open-ended, non-threatening language\n"
            f"4. Maximum 3 targeted questions (don't overwhelm)\n"
            f"5. Use OARS: Open questions, Affirmations, Reflective listening, Summarizing\n\n"
            f"Output JSON only."
        )
    else:
        mood_str = ", ".join(f"{k}={v:.2f}" for k, v in mood.items() if isinstance(v, (int, float)))
        system_msg = (
            f"You are an AI nurse conducting turn {turn} ({turn_type}) of a daily video call with a cancer patient. "
            f"Use motivational interviewing (OARS). Be warm, empathetic, non-threatening. "
            f"Detect adverse events early while maintaining the patient's trust and comfort. "
            f"Patient mood: {mood_str}. Output JSON only."
        )

    day = ctx.get("treatment_day", "")
    user_msg = f"DAY {day} — TURN 2\n\nPATIENT'S INITIAL REPORT (T1):\n{json.dumps(patient_said, ensure_ascii=False, indent=2)}"
    assistant_msg = json.dumps(nurse_output, ensure_ascii=False, indent=2)

    return {
        "system": system_msg,
        "user": user_msg,
        "assistant": assistant_msg,
    }


def build_dpo_prompt(pair: dict) -> dict:
    """Convert a DPO pair to the format expected by TRL's DPOTrainer.

    Supports both legacy (chosen_nurse_turns) and v2 (chosen/rejected direct) formats.
    Computes margin weight from branch_scores when available.
    """
    ctx = pair.get("prompt", pair.get("context", {}))
    mood_scenario = pair.get("mood_scenario", "unknown")
    turn = pair.get("turn", 2)
    turn_type = pair.get("turn_type", "followup")

    mood = ctx.get("patient_mood", {}) if isinstance(ctx, dict) else {}
    mood_str = ", ".join(f"{k}={v:.2f}" for k, v in mood.items() if isinstance(v, (int, float)))

    patient_said = ctx.get("patient_said", {}) if isinstance(ctx, dict) else {}

    prompt_text = (
        f"<start_of_turn>system\n"
        f"You are an AI nurse conducting turn {turn} ({turn_type}) of a daily video call with a cancer patient. "
        f"Use motivational interviewing (OARS). Be warm, empathetic, non-threatening. "
        f"Detect adverse events early while maintaining the patient's trust and comfort. "
        f"Patient mood: {mood_str}. Output JSON only.<end_of_turn>\n"
        f"<start_of_turn>user\n"
        f"Patient said:\n{json.dumps(patient_said, ensure_ascii=False, indent=2)}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )

    chosen = pair.get("chosen", pair.get("chosen_nurse_turns", {}))
    rejected = pair.get("rejected", pair.get("rejected_nurse_turns", {}))

    chosen_text = json.dumps(chosen, ensure_ascii=False, indent=2) if isinstance(chosen, dict) else str(chosen)
    rejected_text = json.dumps(rejected, ensure_ascii=False, indent=2) if isinstance(rejected, dict) else str(rejected)

    scores = pair.get("branch_scores", {})
    margin = compute_dpo_margin(scores)

    return {
        "prompt": prompt_text,
        "chosen": chosen_text + "<end_of_turn>",
        "rejected": rejected_text + "<end_of_turn>",
        "margin": margin,
    }


MARGIN_NOISE_THRESHOLD = 0.03
MARGIN_SCALE = 0.15


def compute_dpo_margin(branch_scores: dict) -> float:
    """Pareto/T4 score 차이 기반 DPO sample weight 계산.

    T2: pareto_score 기반, T4: t4_score 기반.
    Returns 0.0 for noisy pairs (should be filtered), 0.0-1.0 for valid pairs.
    """
    a = branch_scores.get("a") or branch_scores.get("medgemma") or {}
    b = branch_scores.get("b") or branch_scores.get("expert") or {}
    if not isinstance(a, dict) or not isinstance(b, dict):
        return 0.0

    if "t4_score" in a or "t4_score" in b:
        sa = a.get("t4_score", 0)
        sb = b.get("t4_score", 0)
    else:
        sa = a.get("pareto_score", 0)
        sb = b.get("pareto_score", 0)

    margin = abs(sb - sa)
    if margin < MARGIN_NOISE_THRESHOLD:
        return 0.0
    return min(margin / MARGIN_SCALE, 1.0)


def filter_dpo_pairs(pairs: list[dict]) -> list[dict]:
    """Remove noisy DPO pairs and attach margin weights."""
    filtered = []
    n_dropped = 0
    for p in pairs:
        if p.get("margin", 1.0) < 0.01:
            n_dropped += 1
            continue
        filtered.append(p)
    if n_dropped:
        print(f"  Filtered {n_dropped} noisy pairs (margin < {MARGIN_NOISE_THRESHOLD})")
    return filtered


def run_sft(data_dir: Path, gpu_id: int, epochs: int, batch_size: int, lr: float, output_dir: Path):
    """Step 1: Supervised Fine-Tuning with LoRA."""
    import torch
    from datasets import Dataset
    from transformers import AutoTokenizer, AutoModelForCausalLM, TrainingArguments
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTTrainer, SFTConfig

    sft_path = data_dir / "sft_data.jsonl"
    if not sft_path.exists():
        print(f"Error: {sft_path} not found. Run generate_sft_data.py first.")
        return

    print(f"\n{'='*60}")
    print(f"  Step 1: SFT — Supervised Fine-Tuning")
    print(f"  Data: {sft_path}")
    print(f"  GPU: cuda:{gpu_id}")
    print(f"  Epochs: {epochs}, Batch: {batch_size}, LR: {lr}")
    print(f"{'='*60}")

    examples = []
    with open(sft_path) as f:
        for line in f:
            ex = json.loads(line)
            formatted = build_sft_prompt(ex)
            examples.append(formatted)
    print(f"  Loaded {len(examples)} SFT examples")

    model_id = "google/medgemma-1.5-4b-it"

    print(f"\n  Loading {model_id}...")
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def format_and_tokenize(example):
        text = (
            f"<start_of_turn>system\n{example['system']}<end_of_turn>\n"
            f"<start_of_turn>user\n{example['user']}<end_of_turn>\n"
            f"<start_of_turn>model\n{example['assistant']}<end_of_turn>"
        )
        tokens = tokenizer(text, truncation=True, max_length=1536)
        tokens["token_type_ids"] = [0] * len(tokens["input_ids"])
        tokens["labels"] = tokens["input_ids"].copy()
        return tokens

    dataset = Dataset.from_list(examples).map(
        format_and_tokenize, remove_columns=["system", "user", "assistant"]
    )
    print(f"  Tokenized {len(dataset)} examples (avg {sum(len(x) for x in dataset['input_ids'])/len(dataset):.0f} tokens)")

    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        device_map={"": 0},
    )

    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=32,
        lora_alpha=64,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        bias="none",
    )

    model.gradient_checkpointing_enable()
    model = get_peft_model(model, lora_config)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  LoRA params: {trainable:,} / {total:,} ({trainable/total*100:.2f}%)")

    ckpt_dir = output_dir / "checkpoints"
    sft_output = ckpt_dir / "sft_final"

    def medgemma_collator(features):
        import torch as _torch
        batch = {}
        for key in features[0]:
            vals = [f[key] for f in features]
            max_len = max(len(v) for v in vals)
            pad_val = tokenizer.pad_token_id if key == "input_ids" else (0 if key == "token_type_ids" else -100)
            padded = [v + [pad_val] * (max_len - len(v)) for v in vals]
            batch[key] = _torch.tensor(padded)
        batch["attention_mask"] = (batch["input_ids"] != tokenizer.pad_token_id).long()
        return batch

    eff_batch = batch_size
    grad_accum = max(1, 4 // eff_batch)

    training_args = TrainingArguments(
        output_dir=str(ckpt_dir / "sft"),
        num_train_epochs=epochs,
        per_device_train_batch_size=eff_batch,
        gradient_accumulation_steps=grad_accum,
        learning_rate=lr,
        warmup_ratio=0.1,
        weight_decay=0.01,
        bf16=True,
        logging_steps=5,
        save_strategy="epoch",
        save_total_limit=2,
        gradient_checkpointing=True,
        report_to="none",
    )

    from transformers import Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        data_collator=medgemma_collator,
    )

    print(f"\n  Training SFT ({epochs} epochs)...")
    trainer.train()

    model.save_pretrained(str(sft_output))
    tokenizer.save_pretrained(str(sft_output))
    print(f"\n  SFT model saved → {sft_output}")

    return str(sft_output)


def run_dpo(
    data_dir: Path, gpu_id: int, epochs: int, batch_size: int,
    lr: float, output_dir: Path, base_model: str | None = None,
):
    """Step 2: Direct Preference Optimization."""
    import torch
    from datasets import Dataset
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import PeftModel, LoraConfig, TaskType
    from trl import DPOTrainer, DPOConfig

    dpo_path = data_dir / "dpo_pairs.jsonl"
    if not dpo_path.exists():
        print(f"Error: {dpo_path} not found.")
        return

    sft_path = base_model or str(output_dir / "checkpoints" / "sft_final")
    if not Path(sft_path).exists():
        print(f"Error: SFT model not found at {sft_path}. Run SFT first.")
        return

    print(f"\n{'='*60}")
    print(f"  Step 2: DPO — Direct Preference Optimization")
    print(f"  Data: {dpo_path}")
    print(f"  Base: {sft_path}")
    print(f"  GPU: cuda:{gpu_id}")
    print(f"{'='*60}")

    raw_pairs = []
    with open(dpo_path) as f:
        for line in f:
            pair = json.loads(line)
            formatted = build_dpo_prompt(pair)
            raw_pairs.append(formatted)
    print(f"  Loaded {len(raw_pairs)} DPO pairs")

    pairs = filter_dpo_pairs(raw_pairs)
    print(f"  After filtering: {len(pairs)} pairs")

    margins = [p["margin"] for p in pairs]
    if margins:
        avg_m = sum(margins) / len(margins)
        print(f"  Margin stats: avg={avg_m:.3f} min={min(margins):.3f} max={max(margins):.3f}")

    dataset = Dataset.from_list(pairs)

    model_id = "google/medgemma-1.5-4b-it"

    print(f"\n  Loading base model + SFT adapter...")
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base_model_obj = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        device_map={"": 0},
    )
    model = PeftModel.from_pretrained(base_model_obj, sft_path, is_trainable=True)

    ckpt_dir = output_dir / "checkpoints"
    dpo_output = ckpt_dir / "dpo_final"

    dpo_config = DPOConfig(
        output_dir=str(ckpt_dir / "dpo"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=max(1, 4 // batch_size),
        learning_rate=lr,
        warmup_ratio=0.1,
        bf16=True,
        logging_steps=5,
        save_strategy="epoch",
        save_total_limit=1,
        beta=0.1,
        max_length=2048,
        max_prompt_length=512,
        gradient_checkpointing=True,
        report_to="none",
    )

    _patch_medgemma_forward(model)

    model.config.model_type = "gemma3_text_only"

    trainer = DPOTrainer(
        model=model,
        ref_model=None,
        args=dpo_config,
        train_dataset=dataset,
        processing_class=tokenizer,
    )

    print(f"\n  Training DPO ({epochs} epochs)...")
    trainer.train()

    model.save_pretrained(str(dpo_output))
    tokenizer.save_pretrained(str(dpo_output))
    print(f"\n  DPO model saved → {dpo_output}")

    return str(dpo_output)


def main():
    parser = argparse.ArgumentParser(description="Train MedGemma Nurse (SFT + DPO)")
    parser.add_argument("--step", choices=["sft", "dpo", "both"], default="both")
    parser.add_argument("--data-dir", type=str, default="data/training")
    parser.add_argument("--gpu", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--base-model", type=str, default=None,
                        help="Path to SFT checkpoint for DPO step")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir) if args.output_dir else data_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'='*60}")
    print(f"  MedGemma Nurse Fine-Tuning")
    print(f"  Step: {args.step}")
    print(f"  Data: {data_dir}")
    print(f"  GPU: cuda:{args.gpu}")
    print(f"{'='*60}")

    sft_model_path = args.base_model

    if args.step in ("sft", "both"):
        sft_model_path = run_sft(
            data_dir=data_dir,
            gpu_id=args.gpu,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            output_dir=output_dir,
        )

    if args.step in ("dpo", "both"):
        dpo_lr = args.lr * 0.1
        run_dpo(
            data_dir=data_dir,
            gpu_id=args.gpu,
            epochs=max(1, args.epochs // 2),
            batch_size=max(1, args.batch_size // 2),
            lr=dpo_lr,
            output_dir=output_dir,
            base_model=sft_model_path,
        )

    print(f"\n{'='*60}")
    print(f"  Training complete!")
    print(f"  Checkpoints: {output_dir / 'checkpoints'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
