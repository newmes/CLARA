"""Test script to run Doc Agent inference locally."""
import json
import os
import re
import time

os.environ["CUDA_VISIBLE_DEVICES"] = "4"

import torch
print(f"PyTorch: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"GPU Memory: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

# Config
MODEL_ID = "/data2/workspace/vital/models/medgemma-4b-ft-antihallu"

from transformers import AutoTokenizer, AutoModelForCausalLM

print(f"\nLoading model: {MODEL_ID}")
t0 = time.time()
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True,
)
model.eval()
print(f"Model loaded in {time.time()-t0:.1f}s | {sum(p.numel() for p in model.parameters())/1e9:.2f}B params | {torch.cuda.memory_allocated()/1e9:.2f} GB VRAM")

# Sample patient
PATIENT = {
    "dm": {"SUBJID": "PT-AE-001", "AGE": 52, "SEX": "Male"},
    "ae": {"AETERM": "Febrile neutropenia", "AESTDAT": "2025-10-18", "AEENDAT": "2025-11-02",
           "AESEV": "SEVERE", "AESER": "Y", "AETOXGR": "3", "AEREL": "Related",
           "AEACN": "DRUG INTERRUPTED", "AEOUT": "RECOVERED/RESOLVED",
           "AESDTH": "N", "AESLIFE": "N", "AESHOSP": "Y", "AESDISAB": "N", "AESCONG": "N", "AESMIE": "Y",
           "AEHOSPSTDAT": "2025-10-18", "AEHOSPENDAT": "2025-11-02"},
    "ec": [
        {"ECDSTXT": "5.4 mg/kg", "ECDOSFRQ": "Q3W", "ECROUTE": "Intravenous",
         "ECSTDAT": "2025-09-22", "ECENDAT": "2025-10-18",
         "ECDOSADJ": "Drug interrupted due to febrile neutropenia"},
        {"ECDSTXT": "5.4 mg/kg", "ECDOSFRQ": "Q3W", "ECROUTE": "Intravenous",
         "ECSTDAT": "2025-11-10", "ECENDAT": None,
         "ECDOSADJ": "Resumed at same dose after neutropenia resolution"}
    ],
    "lb": {"records": [
        {"LBTESTCD": "ANC", "LBTEST": "Absolute Neutrophil Count", "LBORRES": "0.3",
         "LBORRESU": "10^3/uL", "LBORNRLO": "1.5", "LBORNRHI": "8.0", "LBDAT": "2025-10-18"},
        {"LBTESTCD": "WBC", "LBTEST": "White Blood Cell Count", "LBORRES": "0.8",
         "LBORRESU": "10^3/uL", "LBORNRLO": "4.0", "LBORNRHI": "11.0", "LBDAT": "2025-10-18"},
    ]},
    "cm": {"records": [{"CMTRT": "Filgrastim", "CMINDC": "Febrile neutropenia",
                        "CMDSTXT": "5 mcg/kg SC daily", "CMSTDAT": "2025-10-19",
                        "CMENDAT": "2025-10-28", "CMCAT": "AE_TREATMENT"}]},
    "mh": {"records": [{"MHTERM": "HER2-positive metastatic breast cancer",
                        "MHSTDAT": "2025-05-01", "MHENDAT": None, "MHONGO": "Y"}]},
    "vs": {"records": [{"VSTESTCD": "TEMP", "VSORRES": "38.9", "VSORRESU": "C", "VSDAT": "2025-10-18"}],
           "WEIGHT": 88.0, "HEIGHT": 180.0},
    "imaging": {"records": [{"IMG_MODALITY": "CXR", "IMG_REGION": "Chest", "IMG_DAT": "2025-10-18",
                             "IMG_FINDINGS": "Clear lung fields bilaterally.",
                             "IMG_IMPRESSION": "No acute abnormality."}]},
    "microbiology": {"records": [{"MB_SPECIMEN": "Blood", "MB_TEST": "Culture", "MB_DAT": "2025-10-15",
                                  "MB_RESULT": "No growth (48hr)"}]},
    "consultation": {"records": [{"CONSULT_SPECIALTY": "Infectious Disease", "CONSULT_DAT": "2025-10-16",
                                  "CONSULT_IMPRESSION": "Febrile neutropenia. Cultures negative. Recommend antibiotics and G-CSF."}]}
}

from datetime import datetime
def fmt_date(d):
    if not d: return "N/A"
    if isinstance(d, str): d = datetime.strptime(d, "%Y-%m-%d")
    return d.strftime("%d-%b-%Y").upper()

# B5 prompt
ae = PATIENT["ae"]
dm = PATIENT["dm"]
sae_flags = [l for f,l in [("AESDTH","Death"),("AESLIFE","Life-threatening"),("AESHOSP","Hospitalization"),("AESDISAB","Disability"),("AESCONG","Congenital anomaly"),("AESMIE","Other medically important")] if ae.get(f)=="Y"]
ec_lines = ["Suspect drug: Enfortumab vedotin (Padcev)", "Indication: Metastatic urothelial carcinoma"]
for i, ec in enumerate(PATIENT["ec"], 1):
    ec_lines.append(f"Exposure period {i}: {ec['ECDSTXT']} {ec['ECDOSFRQ']} {ec['ECROUTE']}\n  Start: {fmt_date(ec['ECSTDAT'])}\n  End: {fmt_date(ec['ECENDAT'])}")
    if ec.get("ECDOSADJ"): ec_lines.append(f"  Dose adjustment: {ec['ECDOSADJ']}")
lab_lines = [f"{r['LBTEST']}: {r['LBORRES']} {r['LBORRESU']} (ref: {r['LBORNRLO']}-{r['LBORNRHI']}) [{fmt_date(r['LBDAT'])}]" for r in PATIENT["lb"]["records"]]

b5_prompt = f"""You are a pharmacovigilance medical writer drafting an adverse event narrative for MedWatch FDA Form 3500A, Section B5.

Write a concise clinical narrative in chronological order based on the following patient data.

## Report Type
Initial

## Patient Demographics (DM)
Subject ID: {dm['SUBJID']}
Age: {dm['AGE']} years
Sex: {dm['SEX']}

## Study Drug Exposure (EC)
{chr(10).join(ec_lines)}

## Adverse Event (AE)
Term: {ae['AETERM']}
Onset: {fmt_date(ae['AESTDAT'])}
End: {fmt_date(ae.get('AEENDAT'))}
Severity: {ae['AESEV']}
CTCAE Grade: {ae['AETOXGR']}
Serious: {ae['AESER']}
Causality: {ae['AEREL']}
Action taken: {ae['AEACN']}
Outcome: {ae['AEOUT']}
SAE criteria: {', '.join(sae_flags)}
Hospitalization: {fmt_date(ae.get('AEHOSPSTDAT'))} to {fmt_date(ae.get('AEHOSPENDAT'))}

## Laboratory Results (LB)
{chr(10).join(lab_lines)}

## Medical History (MH)
- HER2-positive metastatic breast cancer (ongoing)

## Vital Signs (VS)
Weight: 88.0 kg
Height: 180.0 cm
TEMP: 38.9 C

## Concomitant Medications (CM)
- Filgrastim 5 mcg/kg SC daily (for Febrile neutropenia)

## Death Details (DD)
N/A (patient alive)

## ILD Clinical Findings
No ILD-related clinical findings

## Imaging Studies
CXR Chest [{fmt_date('2025-10-18')}]: Clear lung fields bilaterally.

## Microbiology Results
Blood Culture [{fmt_date('2025-10-15')}]: No growth (48hr)

## Specialist Consultations
Infectious Disease [{fmt_date('2025-10-16')}]: Febrile neutropenia. Cultures negative. Recommend antibiotics and G-CSF.

## Rules
1. STRUCTURE: Opening "The subject (Subject [SUBJID]) is a [age]-year-old [sex] with a medical history of [conditions]..." then chronological.
2. FORMAT: Plain prose, past tense, DD-MMM-YYYY dates, lab values with units+ranges, 250-500 words. Use "the subject" (not "patient").
3. DRUG NAME: Use EXACT drug name from Suspect drug field.
4. DO NOT: Make causality judgments, add missing info, use markdown, reference AI systems, include race/ethnicity, expose CRF codes."""

# C7 prompt
ec_mod_date = fmt_date(PATIENT["ec"][0]["ECENDAT"])
c7_prompt = f"""You are a pharmacovigilance specialist assessing dechallenge for MedWatch FDA Form 3500A, Section C7.

## Drug Action Taken (AEACN)
{ae['AEACN']}

## AE Outcome (AEOUT)
{ae['AEOUT']}

## Timeline Summary
Drug start: {fmt_date(PATIENT['ec'][0]['ECSTDAT'])}
Drug action date: {ec_mod_date}
AE onset: {fmt_date(ae['AESTDAT'])}
AE end: {fmt_date(ae.get('AEENDAT'))}
AE outcome: {ae['AEOUT']}

## Rules
1. DRUG WITHDRAWN/INTERRUPTED/DOSE REDUCED + resolved -> "Yes"
2. Not resolved -> "No" | Fatal -> "No"
3. DOSE NOT CHANGED -> "Does not apply"

Return ONLY JSON: {{"c7_answer": "Yes"|"No"|"Does not apply"|"Unknown", "c7_rationale": "<one sentence>"}}
Use clinical language. Use "the subject" not "patient"."""

# C8 prompt
ec_hist = "\n".join([f"Period {i+1}: {ec['ECDSTXT']} {ec['ECDOSFRQ']}, Start: {fmt_date(ec['ECSTDAT'])}, End: {fmt_date(ec['ECENDAT'])}" for i, ec in enumerate(PATIENT["ec"])])
c8_prompt = f"""You are a pharmacovigilance specialist assessing rechallenge for MedWatch FDA Form 3500A, Section C8.

## Exposure History
{ec_hist}

## Adverse Events
- {ae['AETERM']}: onset {fmt_date(ae['AESTDAT'])}, end {fmt_date(ae.get('AEENDAT'))}, outcome {ae['AEOUT']}

## Current AE Term
{ae['AETERM']}

## Rules
1. No reintroduction -> "Does not apply", e2b_code=4
2. Reintroduction + AE recurred -> "Yes, recurred", e2b_code=1
3. Reintroduction + no recurrence -> "Yes, did not recur", e2b_code=2

Return ONLY JSON: {{"c8_answer": "...", "c8_rationale": "...", "e2b_code": 1|2|4}}
Use "the subject" not "patient"."""

# Inference helper
def generate(prompt, max_new_tokens=1024, temperature=0.3, do_sample=True):
    # Gemma3 chat format (manual — FT model may lack chat_template)
    input_text = f"<bos><start_of_turn>user\n{prompt}<end_of_turn>\n<start_of_turn>model\n"
    inputs = tokenizer(input_text, return_tensors="pt").to(model.device)
    input_len = inputs["input_ids"].shape[1]
    print(f"  Input tokens: {input_len}")
    t0 = time.time()
    with torch.no_grad():
        kw = dict(max_new_tokens=max_new_tokens, pad_token_id=tokenizer.eos_token_id)
        if do_sample: kw.update(temperature=temperature, do_sample=True, top_p=0.95)
        else: kw["do_sample"] = False
        outputs = model.generate(**inputs, **kw)
    elapsed = time.time() - t0
    new_tokens = outputs[0][input_len:]
    text = tokenizer.decode(new_tokens, skip_special_tokens=True)
    tps = len(new_tokens) / elapsed if elapsed > 0 else 0
    print(f"  Output tokens: {len(new_tokens)} | {elapsed:.2f}s ({tps:.1f} tok/s)")
    return text, {"output_tokens": len(new_tokens), "elapsed_s": elapsed, "tokens_per_sec": tps}

def clean_narrative(text):
    text = re.sub(r"^#{1,6}\s+.*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    lines = text.split("\n")
    cut = len(lines)
    for i, line in enumerate(lines):
        for pat in [r"^Term:\s", r"^Onset:\s", r"^Severity:\s", r"^Action taken:\s"]:
            if re.match(pat, line.strip(), re.IGNORECASE): cut = i; break
        if cut < len(lines): break
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines[:cut])).strip()

def parse_json(raw):
    text = re.sub(r"<unused\d+>.*?</unused\d+>", "", raw.strip(), flags=re.DOTALL).strip()
    m = re.search(r"\{[^{}]*(?:\"c[78]_answer\"|\"c[78]_rationale\")[^{}]*\}", text, flags=re.DOTALL)
    if m:
        try: return json.loads(m.group())
        except: pass
    try: return json.loads(text)
    except: return {"raw": raw}

# Run
print("\n" + "="*60)
print("[B5] Clinical Narrative")
print("="*60)
b5_raw, b5m = generate(b5_prompt, max_new_tokens=1024, temperature=0.3)
b5 = clean_narrative(b5_raw)
print(f"\n{b5}\n\n[{len(b5.split())} words]")

print("\n" + "="*60)
print("[C7] Dechallenge")
print("="*60)
c7_raw, c7m = generate(c7_prompt, max_new_tokens=512, do_sample=False)
c7 = parse_json(c7_raw)
print(f"\n{json.dumps(c7, indent=2)}")

print("\n" + "="*60)
print("[C8] Rechallenge")
print("="*60)
c8_raw, c8m = generate(c8_prompt, max_new_tokens=512, do_sample=False)
c8 = parse_json(c8_raw)
print(f"\n{json.dumps(c8, indent=2)}")

# Summary
print("\n" + "="*60)
print("SUMMARY")
print("="*60)
total = b5m["elapsed_s"] + c7m["elapsed_s"] + c8m["elapsed_s"]
print(f"B5: {b5m['output_tokens']}tok {b5m['elapsed_s']:.1f}s | C7: {c7m['output_tokens']}tok {c7m['elapsed_s']:.1f}s | C8: {c8m['output_tokens']}tok {c8m['elapsed_s']:.1f}s")
print(f"Total: {total:.1f}s")

# Quality
tl = b5.lower()
checks = [("Opening", tl.startswith("the subject")), ("Subject ID", "pt-ae-001" in tl),
           ("AE term", "febrile neutropenia" in tl), ("No markdown", not re.search(r"[*#`]", b5)),
           ("No CRF codes", not any(c in b5 for c in ["AEACN=","AEOUT="])),
           ("ICH convention", "the subject" in tl and "the patient" not in tl),
           ("Word count", 200 <= len(b5.split()) <= 600),
           ("Has dates", bool(re.search(r"\d{2}-[A-Z]{3}-\d{4}", b5)))]
for n, p in checks: print(f"  {'PASS' if p else 'FAIL'} {n}")
print(f"Score: {sum(p for _,p in checks)}/{len(checks)}")

del model, tokenizer; torch.cuda.empty_cache()
print("\nDone!")
