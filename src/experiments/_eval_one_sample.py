"""One-sample detailed comparison: baseline vs SFT vs DPO conversation analysis."""
import json, sys, time, torch
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agents.care_agent import CareAgent
from src.agents.llm_client import generate_json as gemini_generate_json
from src.engine.mood import MoodState, compute_interaction_quality, compute_grade_distortion
from src.engine.sampler import Sampler
from src.experiments.compare_care_models import load_patient_and_day
from src.experiments.generate_sft_selfplay import MOOD_SCENARIOS, measure_patient_behavior

PATIENT_MODEL = "gemini-2.0-flash"

def _patient(sp, up, **kw):
    kw.pop("model", None)
    return gemini_generate_json(sp, up, model=PATIENT_MODEL, **kw)

run_id = "20260220_075422_Padcev___Pembrolizumab_10pt_126d"
patient, rule_set, day_data, last_hr = load_patient_and_day(run_id, "PT-007", 125)

obj = day_data.get("objective", {})
active_aes = obj.get("active_aes", [])
ae_labels = [ae.get("ae", "?") + " G" + str(ae.get("grade", "?")) for ae in active_aes]
print(f"GT AEs: {ae_labels}")
print(f"Total GT AEs: {len(active_aes)}")

mood_scenario = "cooperative"
persona_type = patient.get("persona", {}).get("type", "stoic_minimizer")

def setup():
    mood = MoodState(persona_type=persona_type, seed=99)
    for dim, val in MOOD_SCENARIOS[mood_scenario].items():
        if dim in mood.state:
            mood.state[dim] = val
    sampler = Sampler(seed=99)
    quality = compute_interaction_quality(mood)
    grade_distortion = compute_grade_distortion(mood)
    agent = CareAgent(patient=patient, rule_set=rule_set, mood=mood, sampler=sampler, model="medgemma-1.5-4b-it")
    agent._last_hospital_record = last_hr
    return agent, mood, quality, grade_distortion

# T1: Patient initial report (same for all models)
agent, mood, quality, grade_distortion = setup()
with patch("src.agents.care_agent.generate_json", side_effect=_patient):
    t1 = agent._patient_initial_report(125, day_data, quality, grade_distortion)

print(f"\n{'='*70}")
print("T1: PATIENT INITIAL REPORT")
print(f"{'='*70}")
reported = t1.get("reported_symptoms", [])
omitted = t1.get("omitted_symptoms", [])
print(f"Reported symptoms: {[s.get('symptom', '?') for s in reported]}")
print(f"Omitted symptoms: {omitted}")
for s in reported:
    desc = s.get("description", "")[:100]
    print(f"  - {s.get('symptom','?')}: {desc}")
print(f"General feeling: {t1.get('general_feeling', '?')}")

from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel
import gc

model_id = "google/medgemma-1.5-4b-it"
ckpt_dir = Path("data/training_selfplay_v2/checkpoints")


def load_and_run(name, adapter_path=None):
    print(f"\n{'#'*70}")
    print(f"  T2: {name.upper()} NURSE RESPONSE")
    print(f"{'#'*70}")

    tok = AutoTokenizer.from_pretrained(model_id)
    mdl = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.bfloat16, device_map={"": 0})
    if adapter_path:
        print(f"  + adapter: {Path(adapter_path).name}")
        mdl = PeftModel.from_pretrained(mdl, adapter_path)
        mdl = mdl.merge_and_unload()
    mdl.eval()

    def _gen(sp, up, **kw):
        kw.pop("model", None)
        kw.pop("caller", None)
        chat = [
            {"role": "system", "content": sp},
            {"role": "user", "content": up + "\n\nRespond with valid JSON only."},
        ]
        inp_text = tok.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        inputs = tok(inp_text, return_tensors="pt").to(mdl.device)
        with torch.no_grad():
            out = mdl.generate(**inputs, max_new_tokens=4096, temperature=0.7, top_p=0.9, do_sample=True)
        raw = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        js = raw.strip()
        if "```json" in js:
            js = js.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in js:
            js = js.split("```", 1)[1].split("```", 1)[0]
        start = js.find("{")
        if start >= 0:
            depth, end = 0, start
            for i, c in enumerate(js[start:], start):
                if c == "{": depth += 1
                elif c == "}": depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            js = js[start:end]
        try:
            return json.loads(js)
        except Exception:
            return {"_raw": raw[:500]}

    ag, mood_local, qual, gd = setup()
    with patch("src.agents.care_agent.generate_json", side_effect=_gen):
        t2 = ag._nurse_followup_questions(125, t1, qual)

    print(f"\n  Approach: {t2.get('approach_style', '?')}")
    print(f"  Tone: {t2.get('tone', '?')}")
    qs = t2.get("questions", [])
    for i, q in enumerate(qs):
        q_text = q.get("question", q.get("text", str(q)[:120]))
        print(f"  Q{i+1}: {q_text}")
        if q.get("purpose"):
            print(f"      (purpose: {q['purpose'][:80]})")

    # T3: Patient responds to this nurse
    with patch("src.agents.care_agent.generate_json", side_effect=_patient):
        t3 = ag._patient_followup_response(125, day_data, t2, qual, gd)

    beh = measure_patient_behavior(t3)
    print(f"\n  --- Patient reaction to {name.upper()} nurse ---")
    print(f"  honesty={beh['honesty_rate']:.2f}  reveal={beh['reveal_rate']:.2f}  openness={beh['emotional_openness']:.2f}")
    print(f"  mood_proxy={beh['mood_proxy']:.3f}")
    print(f"  emotional_reaction: {beh['emotional_reaction']}")
    print(f"  new_info_revealed: {beh['new_info_revealed']}")
    print(f"  cooperated_visual: {beh['cooperated_visual']}")

    resps = t3.get("responses", [])
    for r in resps:
        sym = r.get("revealed_symptom", "")
        hon = r.get("honesty_level", "?")
        verbal = r.get("verbal_response", "")[:100]
        print(f"    [{hon}] {verbal}")
        if sym:
            print(f"      >> REVEALED: {sym}")

    del mdl, tok
    torch.cuda.empty_cache()
    gc.collect()
    return t2, t3, beh


t2_base, t3_base, beh_base = load_and_run("baseline", None)
t2_sft, t3_sft, beh_sft = load_and_run("sft", str(ckpt_dir / "sft_final"))
t2_dpo, t3_dpo, beh_dpo = load_and_run("dpo", str(ckpt_dir / "dpo_final"))

print(f"\n\n{'='*70}")
print("  SUMMARY: PT-007 day 125 (cooperative mood)")
print(f"{'='*70}")
print(f"  {'Metric':<25} {'Baseline':>10} {'SFT':>10} {'DPO':>10}")
print(f"  {'-'*55}")
print(f"  {'honesty_rate':<25} {beh_base['honesty_rate']:>10.2f} {beh_sft['honesty_rate']:>10.2f} {beh_dpo['honesty_rate']:>10.2f}")
print(f"  {'reveal_rate':<25} {beh_base['reveal_rate']:>10.2f} {beh_sft['reveal_rate']:>10.2f} {beh_dpo['reveal_rate']:>10.2f}")
print(f"  {'emotional_openness':<25} {beh_base['emotional_openness']:>10.2f} {beh_sft['emotional_openness']:>10.2f} {beh_dpo['emotional_openness']:>10.2f}")
print(f"  {'mood_proxy':<25} {beh_base['mood_proxy']:>10.3f} {beh_sft['mood_proxy']:>10.3f} {beh_dpo['mood_proxy']:>10.3f}")
print(f"  {'emotional_reaction':<25} {beh_base['emotional_reaction']:>10} {beh_sft['emotional_reaction']:>10} {beh_dpo['emotional_reaction']:>10}")
print(f"  {'new_info_revealed':<25} {str(beh_base['new_info_revealed']):>10} {str(beh_sft['new_info_revealed']):>10} {str(beh_dpo['new_info_revealed']):>10}")
