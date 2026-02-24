# MedGemma Impact Challenge — Project Write-Up (DRAFT v1)

---

## Project Name

**CareScope: Simulating the Impact of AI-Powered Patient Monitoring on Clinical Trial Safety**

---

## Your Team

*(To be filled in)*

---

## Problem Statement

### The Hidden Cost of Detection Delays in Clinical Trials

In oncology clinical trials, patients receive cytotoxic or immunomodulatory drugs that carry significant risk of Serious Adverse Events (SAEs). The standard monitoring model—clinic visits every 10–21 days with patient self-reporting in between—creates a structural blind spot. Between visits, the only safety signal comes from patients themselves, and the evidence shows this is deeply unreliable:

- **Physician–patient AE grade agreement is ~50%** (Basch et al., 2006), meaning half of all AE severities are misjudged.
- **Physicians undergrade 57% of AEs** (Di Maio et al., 2015), systematically underestimating toxicity.
- **Grade 1–2 AE self-reporting rates are only 2–8%** — mild symptoms that, left unmanaged, escalate to Grade 3+ toxicity requiring hospitalization, dose discontinuation, or causing preventable death.

The consequence is a cascade: late detection → grade escalation → dose holds/discontinuation → reduced treatment duration → compromised efficacy data → trial failure risk. For the patient, it means preventable suffering. For the sponsor, it means degraded safety profiles and unreliable data.

### Why This Problem Is Hard to Study

Clinical trial data is among the most restricted in medicine. Public datasets are sparse, incomplete, and lack the daily granularity needed to measure detection delays. No real-world dataset exists where the same patients are observed both with and without continuous AI monitoring — the counterfactual is fundamentally unobservable.

### Impact Potential

If an AI monitoring system could reduce AE detection delay from 7–12 days to 0–1 days, the downstream effects would be substantial:

- **Grade escalation prevention**: Catching Grade 2 rash before it becomes Grade 3 SJS/TEN (a dermatologic emergency)
- **Treatment continuity**: Fewer dose holds and discontinuations → longer treatment duration → better efficacy signal
- **Patient retention**: Reduced dropout from unmanaged toxicity
- **Regulatory quality**: Earlier, more accurate AE documentation for safety databases

We estimate this could improve treatment completion rates by 10–20% and reduce Grade 3+ AE burden by 15–30% across common oncology regimens — directly impacting both patient outcomes and trial success probability.

---

## Overall Solution

### How We Use HAI-DEF Models

We built a platform with four integrated applications of Google Health AI models, each addressing a distinct clinical trial challenge:

### 1. Anti-Hallucination MedGemma (Notebook 1)

**Model**: MedGemma 4B, iteratively fine-tuned with RLFR (Reinforcement Learning from Feature Rewards) over 4 DPO rounds.

**Problem**: In regulatory documents (SAE reports, CRFs), a single hallucinated AE term or incorrect grade can trigger unnecessary trial holds, dose modifications, or regulatory queries. Standard LLMs fabricate drug names, invent AE terms not in MedDRA, and confabulate causal relationships.

**Result**: T4 Precision improved from 0.198 → 0.354; Grade MAE dropped from 0.25 → 0.0 (perfect grade estimation). This model serves as the reliability backbone for all downstream text generation, including SAE report narratives (B5 clinical narrative, C7 dechallenge, C8 rechallenge assessment).

### 2. Multimodal SAE Detection (Notebook 2)

**Models**: MedGemma 4B Vision (zero-shot + fine-tuned), MedSigLIP-448 (zero-shot + fine-tuned), HeAR (audio embeddings)

**Problem**: Many AEs have visual or auditory manifestations (maculopapular rash, peripheral edema, pallor, dry/wet cough) that patients minimize or fail to report, but that are detectable through a video call.

**Solution**: Three complementary detection channels:
- **MedGemma Vision**: Open-ended visual assessment of dermatologic and physical AEs across 21 categories (7 AE types × 3 CTCAE grades)
- **MedSigLIP**: Efficient visual AE classification from video frames (runs on mobile)
- **HeAR**: 2-stage cough classifier (dry → pneumonitis indicator; wet → infection/edema) from audio embeddings

### 3. Care AI Nurse Agent (Notebook 4)

**Model**: MedGemma 4B as a virtual nurse conducting daily structured video calls

**Problem**: The 10–21 day monitoring gap between clinic visits. Patients with stoic personalities underreport 37% of symptoms; anxious patients overreport, creating noise.

**Solution**: A 4-turn daily video call protocol:
1. **Patient report** (filtered through 7-dimensional mood model: anxiety, fatigue, irritability, defensiveness, trust, depression, hopefulness)
2. **Nurse follow-up** (informed by visual detection + patient history, but never by ground truth)
3. **Patient response** (mood-adjusted cooperation)
4. **Clinical assessment** with 6-level escalation ladder (no_action → monitor → recommend_conmed → early_visit → escalate → emergency)

Six prompt strategies were evaluated; a "Deployment-Ready" strategy (drug context + visual + audio + adaptive speaking style) achieved optimal Pareto performance balancing AE recall and patient comfort.

### 4. SAE Report Generation (Notebook 5)

**Model**: Anti-hallucination MedGemma 4B generating MedWatch FDA 3500A reports + E2B(R3) XML

**Problem**: SAE reports are legal documents submitted to regulators. Manual preparation takes 4–8 hours per event. Any factual error has regulatory consequences.

**Solution**: CRF data → automated MedWatch 3500A with AI-generated clinical narrative (B5), dechallenge (C7), and rechallenge (C8) assessments, plus MedDRA coding and E2B(R3) XML export — all grounded in the anti-hallucination model to minimize fabrication risk.

---

## Technical Details

### The Core Challenge: No Ground Truth Exists

To validate that Care AI monitoring actually prevents SAE escalation, we need to compare the same patient cohort with and without AI — a counterfactual that cannot exist in reality. We solved this with a **clinically realistic simulation engine** that generates synthetic but statistically faithful clinical trial data.

### Simulation Architecture: "LLM Sets Rules, Code Rolls Dice"

Our 3-phase pipeline eliminates hardcoded medical knowledge while maintaining statistical rigor:

**Phase 0 — Rule Discovery** (once per drug):
A Gemini-based Rule Agent queries 10 biomedical databases (DailyMed, OpenFDA, ClinicalTrials.gov, PubChem, ChEMBL, PubMed, OnSIDES, PrimeKG, DrugBank, Project Data Sphere) and synthesizes probabilistic simulation parameters: AE incidence rates, onset distributions, grade profiles, dose modification rules, efficacy models, and mortality parameters. A separate LLM critic validates the output. This makes the entire pipeline **drug-agnostic** — changing the drug name generates a completely new simulation.

**Phase 1 — Patient Generation** (once per patient):
An LLM→rand→LLM pattern generates diverse, internally consistent patients. Demographics are sampled from rule-set distributions; comorbidities are LLM-adjusted for demographic coherence (e.g., elderly + diabetic → higher CKD probability → elevated baseline creatinine); personas are assigned from 10 behavioral archetypes (stoic_minimizer, anxious_reporter, shame_avoidant, etc.) that govern reporting behavior throughout the trial.

**Phase 2 — Daily Simulation** (per patient per day):
A hazard-function engine computes daily conditional probabilities for AE onset, grade transitions, resolution, tumor response, ECOG changes, and mortality — all without LLM calls on quiet days. Key mathematical models:
- **AE Onset**: Mixture hazard `h(t) = I·f(t) / (1 − I·F(t−1))` with patient-adjusted incidence
- **Mortality**: Log-linear hazard `exp(log(base) + severity_weight · log(HR))` preventing double-counting between correlated factors (ECOG, AE grade, tumor status)
- **Labs/Vitals**: Ornstein-Uhlenbeck mean-reversion with AE-causal targets (neutropenia → ANC↓, hepatitis → ALT↑)
- **Tumor**: Sigmoid response model with effective-treatment-week tracking

### Ground Truth / Hospital Record Separation

Every simulated day produces two parallel records:
- **Ground Truth (GT)**: Complete patient state (all AEs, true grades, actual labs)
- **Hospital Record (HR)**: Only what the healthcare system observed — detected AEs, stale labs between visits, patient-reported symptoms filtered through mood/persona

Treatment decisions (dose hold, conmed, discontinuation) use **HR only**, never GT. This creates realistic information asymmetry — the exact gap that Care AI bridges.

### Care AI's Mechanism of Action

In the Care AI arm, daily video calls add two detection channels: visual (MedGemma + MedSigLIP) and conversational (structured probing). Detected findings are written into a `care_record` that updates the hospital record. The next day's simulation naturally reflects earlier detection → earlier intervention → different trajectory. No separate branching logic is needed — the same hazard functions produce different outcomes because the input data differs.

### Validation

We validated across 7 drugs (Etoposide+Cisplatin, Paclitaxel+Carboplatin+Bevacizumab, Carboplatin+Etoposide, Darbepoetin alfa, and others) with 100 patients × 126 days each. Statistical validation uses SMD (Standardized Mean Difference), TOST equivalence tests, chi-square goodness-of-fit, and Kolmogorov-Smirnov tests against rule-set expectations, with Benjamini-Hochberg FDR correction.

### Deployment-Ready Application

Beyond simulation, we built a production Care AI API (FastAPI) that accepts MedSigLIP-encoded video frames + patient speech transcription, returns nurse responses with TTS audio, and integrates with a mobile app for real patient monitoring — closing the loop from research validation to clinical deployment.

### Key Results Summary

| Metric | Without Care AI | With Care AI |
|--------|----------------|--------------|
| AE Detection Delay | 7–12 days | 0–1 days |
| Grade 1–2 Detection Rate | 2–8% | 50–70% |
| Grade 3+ AE Events | Baseline | ~13–30% reduction |
| Treatment Duration | Baseline | +10–20% improvement |

---

*This platform demonstrates that MedGemma — as a fine-tuned anti-hallucination model, a multimodal AE detector, a virtual nurse, and a regulatory document generator — can address the full lifecycle of clinical trial safety monitoring, from early detection through regulatory reporting.*

---

# Self-Evaluation

## Strengths
1. **Clear problem–solution mapping**: Each HAI-DEF model addresses a specific, well-defined clinical trial problem
2. **Technical depth**: The simulation architecture (hazard functions, observation model, mood model) is genuinely novel and well-explained
3. **End-to-end story**: From model fine-tuning → simulation validation → production app
4. **Quantitative results**: Concrete metrics (T4 Precision, Grade MAE, detection delay reduction)

## Weaknesses & Areas for Improvement

### 1. Length — Too Long for "3 Pages"
The current draft exceeds 3 pages. The Technical Details section is too granular. The challenge says "less is more" and to "use the video to convey most concepts." Need to cut ~30-40% of technical detail, especially:
- Hazard function formulas (move to video/appendix)
- OU process details
- Detailed mood model dimensions

### 2. Results Table Is Partially Estimated
The "Key Results Summary" table mixes simulation results with projected estimates. The "~13–30% reduction" and "+10–20% improvement" need to be either backed by actual simulation run data or clearly labeled as projections. Currently running simulations should provide concrete numbers.

### 3. Missing Concrete Patient Story
The write-up is abstract. A 2-sentence concrete example would be powerful: "Patient PT-047, a 68-year-old stoic male with baseline diabetes, developed Grade 2 maculopapular rash on Day 14. Without Care AI, it escalated to Grade 3 by Day 22; with Care AI, visual detection on Day 15 led to topical steroid intervention and resolution by Day 21."

### 4. "Effective Use of HAI-DEF Models" Could Be Sharper
The criterion asks specifically about HAI-DEF models. We should make the model names more prominent (MedGemma 4B, MedGemma Vision, MedSigLIP, HeAR) and explicitly state which Google model is used where. Currently some sections bury the model name.

### 5. The Simulation Validation Section Lacks Final Numbers
We say "validated across 7 drugs" but don't show the validation grades (A/B/C). Once the current simulation runs complete, we should include 1-2 concrete validation scores.

### 6. No Comparison to Alternatives
We don't mention why MedGemma specifically (vs GPT-4-Medical, Med-PaLM, etc.). A brief sentence on why MedGemma's medical pre-training makes it uniquely suitable would strengthen the "Effective use" criterion.

### 7. Product Feasibility Could Show More
The FastAPI + mobile app is mentioned in one paragraph. For "Product feasibility," we should briefly show: latency numbers, cost per patient per day, and scalability estimate.

### 8. The Anti-Hallucination Section May Overstate
"Grade MAE 0.0" sounds too good. We should clarify the evaluation set size and conditions, or reviewers may be skeptical.
