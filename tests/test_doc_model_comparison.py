"""One-off comparison: MedGemma 27B vs 4B-base vs 4B-antihallu for Doc Agent B5/C7/C8.

Usage: python test_model_comparison.py
GPU 3 must be free. Models loaded one at a time.
"""
import gc
import json
import os
import time
import torch
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "3"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

MODELS = {
    "27B": {
        "path": "/data2/huggingface/hub/models--google--medgemma-27b-it/snapshots/2d3e00ea38b50018bf5dd3aa1009457cd2d5a48f",
        "load_in_4bit": True,
    },
    "4B-base": {
        "path": "/data2/workspace/samuel-huggingface/huggingface/hub/models--google--medgemma-1.5-4b-it/snapshots/e9792da5fb8ee651083d345ec4bce07c3c9f1641",
        "load_in_4bit": False,
    },
    "4B-antihallu": {
        "path": "/data2/workspace/samuel-huggingface/huggingface/hub/iter_004",
        "tokenizer_path": "/data2/huggingface/hub/models--google--medgemma-4b-it/snapshots/290cda5eeccbee130f987c4ad74a59ae6f196408",
        "load_in_4bit": False,
    },
}

# ── Test patient: PT-003 hyperglycemia Grade 3 ──

B5_PROMPT = """You are a pharmacovigilance medical writer drafting an adverse event narrative for MedWatch FDA Form 3500A, Section B5.

Write a concise clinical narrative in chronological order based on the following patient data.

## Report Type
Initial
- If "Initial": Write the full narrative from onset

## Patient Demographics (DM)
Subject ID: PT-003, Age: 66, Sex: Male

## Study Drug Exposure (EC)
Drug: Enfortumab vedotin (Padcev), Dose: 118.8 mg IV on 06-JAN-2026
Drug: Pembrolizumab (Keytruda), Dose: 200.0 mg IV on 06-JAN-2026

## Adverse Event (AE)
Term: Hyperglycemia, Start: 26-JAN-2026, Severity: SEVERE, Grade: 3, Serious: Yes (Hospitalization)
Action taken: DRUG INTERRUPTED, Outcome: NOT RECOVERED/NOT RESOLVED
Causality: RELATED

## Laboratory Results (LB)
Glucose: 285 mg/dL (ref: 70-100) on 26-JAN-2026
HbA1c: 8.2% (ref: <5.7%) on 26-JAN-2026

## Medical History (MH)
Hypertension, Chronic kidney disease stage 3

## Vital Signs (VS)
BP: 142/88 mmHg, HR: 82 bpm, Temp: 36.8 C on 26-JAN-2026

## Concomitant Medications (CM)
Amlodipine 5mg PO daily, Metformin 500mg PO BID

## Death Details (DD) — if applicable
N/A

## ILD Clinical Findings — if applicable
N/A

## Imaging Studies
N/A

## Pulmonary Function Tests (PFT)
N/A

## Microbiology Results
N/A

## Specialist Consultations
N/A

## Rules

1. STRUCTURE: Follow this order:
   - Opening sentence MUST follow this exact format: "The subject (Subject [SUBJID]) is a [age]-year-old [sex] with a medical history of [conditions]..."
   - Study drug regimen (drug name, dose, route, frequency, start date)
   - Adverse event onset (date, symptoms, initial presentation)
   - Diagnostic workup
   - Treatment of AE (medications, dose modifications, interventions)
   - Outcome (resolved, ongoing, fatal — with dates)

2. FORMAT:
   - Write in plain prose paragraphs only
   - Use past tense
   - Use exact dates (DD-MMM-YYYY) when available
   - Include lab values with units and reference ranges
   - Keep to 250-500 words
   - Use "the subject" consistently (ICH convention)

3. DO NOT:
   - Make causality judgments
   - Add information not present in the input data
   - Use any markdown formatting (no headers, no bold/italic markers, no bullet points)
   - Expose CRF field codes"""

C7_PROMPT = """You are a pharmacovigilance specialist assessing dechallenge for MedWatch FDA Form 3500A, Section C7.

Based on the following data, determine whether the adverse event abated after the suspect drug was stopped or dose reduced.

## Drug Action Taken (AEACN)
DRUG INTERRUPTED

## AE Outcome (AEOUT)
NOT RECOVERED/NOT RESOLVED

## AE Start Date
26-JAN-2026

## AE End Date (if available)
N/A (ongoing)

## Drug Stop/Modification Date
26-JAN-2026

## Timeline Summary
Drug start: 06-JAN-2026
Drug action date: 26-JAN-2026
AE onset: 26-JAN-2026
AE end: N/A (ongoing)
AE outcome: NOT RECOVERED/NOT RESOLVED

## Rules

Determine C7 (Dechallenge) using this logic:

1. If AEACN = "DOSE NOT CHANGED" or "NOT APPLICABLE":
   Output: "Does not apply"

2. If AEACN in ("DRUG WITHDRAWN", "DRUG INTERRUPTED", "DOSE REDUCED"):
   a. If AEOUT in ("RECOVERED/RESOLVED", "RECOVERING/RESOLVING", "RECOVERED/RESOLVED WITH SEQUELAE"):
      Output: "Yes — reaction abated after {action}"
   b. If AEOUT in ("NOT RECOVERED/NOT RESOLVED"):
      Output: "No — reaction did not abate after {action}"
   c. If AEOUT = "FATAL":
      Output: "No — patient died"
   d. If AEOUT = "UNKNOWN":
      Output: "Unknown"

## Output Format

Return JSON only:
{
  "c7_answer": "Yes" | "No" | "Does not apply" | "Unknown",
  "c7_rationale": "<one sentence explanation with dates>"
}

Use clinical language only. Do NOT expose CRF field codes."""

C8_PROMPT = """You are a pharmacovigilance specialist assessing rechallenge for MedWatch FDA Form 3500A, Section C8.

Based on the following data, determine whether the adverse event reappeared after the suspect drug was reintroduced.

## Exposure History (EC)
1. Enfortumab vedotin 118.8mg IV, start: 06-JAN-2026, end: 26-JAN-2026 (interrupted due to AE)
2. Pembrolizumab 200mg IV, start: 06-JAN-2026 (single exposure)

## All Adverse Events for this patient (AE)
1. Hyperglycemia, Grade 3, onset: 26-JAN-2026, outcome: NOT RECOVERED/NOT RESOLVED

## Current AE Term
Hyperglycemia

## Rules

1. Check EC records for reintroduction pattern (Drug start - Drug stop - Drug restart)
   A rechallenge exists ONLY if there are at least 2 separate exposure periods

2. If NO reintroduction found: "Does not apply — drug was not re-administered"

3. If reintroduction found:
   a. Same/similar AE after reintroduction: "Yes — reaction recurred"
   b. No same/similar AE: "Yes — reaction did not recur"

## Output Format

Return JSON only:
{
  "c8_answer": "Yes, recurred" | "Yes, did not recur" | "Does not apply",
  "c8_rationale": "<one sentence explanation with dates>",
  "e2b_code": 1 | 2 | 4
}

Use clinical language only. Do NOT expose CRF field codes."""


def load_model(model_cfg):
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig

    path = model_cfg["path"]
    tok_path = model_cfg.get("tokenizer_path", path)
    print(f"  Loading tokenizer from {tok_path}...")
    tokenizer = AutoTokenizer.from_pretrained(tok_path, trust_remote_code=True)

    kwargs = {
        "torch_dtype": torch.bfloat16,
        "device_map": "cuda:0",
        "trust_remote_code": True,
    }
    if model_cfg.get("load_in_4bit"):
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
        )

    print(f"  Loading model...")
    model = AutoModelForCausalLM.from_pretrained(path, **kwargs)
    mem = torch.cuda.memory_allocated() / 1024**3
    print(f"  Loaded. GPU memory: {mem:.1f} GB")
    return model, tokenizer


def generate(model, tokenizer, prompt, max_new_tokens=1024, temperature=0.3):
    messages = [{"role": "user", "content": prompt}]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=True,
            top_p=0.9,
        )

    generated = outputs[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(generated, skip_special_tokens=True)


def unload_model(model, tokenizer):
    del model
    del tokenizer
    gc.collect()
    torch.cuda.empty_cache()
    time.sleep(2)
    mem = torch.cuda.memory_allocated() / 1024**3
    print(f"  GPU memory after unload: {mem:.1f} GB")


def main():
    results = {}
    prompts = {"B5": B5_PROMPT, "C7": C7_PROMPT, "C8": C8_PROMPT}

    for model_name, model_cfg in MODELS.items():
        print(f"\n{'='*60}")
        print(f"  MODEL: {model_name}")
        print(f"{'='*60}")

        model, tokenizer = load_model(model_cfg)
        results[model_name] = {}

        for task, prompt in prompts.items():
            print(f"\n  --- {task} ---")
            t0 = time.time()
            max_tokens = 1024 if task == "B5" else 512
            temp = 0.3 if task == "B5" else 0.1
            output = generate(model, tokenizer, prompt, max_new_tokens=max_tokens, temperature=temp)
            elapsed = time.time() - t0
            results[model_name][task] = {"text": output, "time": round(elapsed, 1), "chars": len(output)}
            print(f"  Time: {elapsed:.1f}s | {len(output)} chars")
            print(f"  >>> {output[:200]}...")

        unload_model(model, tokenizer)

    # Save
    out_path = Path("/data2/workspace/vital/data/model_comparison_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved: {out_path}")

    # Print comparison table
    print(f"\n{'='*80}")
    print("  COMPARISON")
    print(f"{'='*80}")
    for task in ["B5", "C7", "C8"]:
        print(f"\n{'─'*80}")
        print(f"  {task}")
        print(f"{'─'*80}")
        for mn in MODELS:
            r = results[mn][task]
            print(f"\n  [{mn}] {r['time']}s, {r['chars']} chars")
            display = r["text"][:500] if task == "B5" else r["text"][:300]
            for line in display.split("\n"):
                print(f"    {line}")
            if len(r["text"]) > (500 if task == "B5" else 300):
                print(f"    ... ({r['chars']} total)")


if __name__ == "__main__":
    main()
