<div align="center">

<img src="docs/assets/logo.svg" width="100" alt="CLARA Logo" />

# CLARA: Clinical Longitudinal AI Research Assistant

**Daily multimodal capture — voice and video together — and longitudinal reasoning across time.**
**A simulation-powered framework that proves continuous AI monitoring saves lives, then deploys it as a real-world application.**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](#)
[![License](https://img.shields.io/badge/License-Apache_2.0-green.svg)](#license)
[![HAI-DEF](https://img.shields.io/badge/HAI--DEF-MedGemma-FF6F00?logo=google&logoColor=white)](https://developers.google.com/health-ai-developer-foundations)
[![Kaggle](https://img.shields.io/badge/MedGemma_Impact-Challenge_2026-20BEFF?logo=kaggle&logoColor=white)](https://www.kaggle.com/competitions/med-gemma-impact-challenge)

[Quick Start](#quick-start) · [HAI-DEF Models](#hai-def-models) · [Notebooks](#notebooks) · [Results](#results) · [Technical Details](#technical-details)

</div>

---

<p align="center">
  <img src="docs/assets/figure.png" width="100%" alt="CLARA System Overview — Data Collection Agent, Simulation Village, and Data Analysis Agent" />
</p>

---

## The Problem

In oncology trials, clinical monitoring relies heavily on clinic visits scheduled every **12–21 days** for patient safety. During this gap, nobody assures the safety of the patient on the trial.

- **400,000+** oncology trial participants are affected by these blind spots globally each year ([Izarn et al., *ESMO Open*, 2025](https://doi.org/10.1016/j.esmoop.2024.104086))
- Physician–patient AE grade agreement is only **~50%** ([Basch et al., *Lancet Oncol.*, 2006](https://doi.org/10.1016/S1470-2045(06)70910-X))
- Physicians routinely fail to capture **57% of AEs** ([Di Maio et al., *J Clin Oncol.*, 2015](https://doi.org/10.1200/JCO.2014.57.9334))
- Self-reporting rates for Grade 1–2 Adverse Events (AEs) are exceptionally low, hovering around **~2%**

Most patients tend to underreport their discomfort. Missing these implicit signals can lead to severe repercussions—including grade escalation, emergency hospitalization, and loss of life. The delayed attention will also affect the effectiveness of clinical trials if forced dose interruptions occur.

---

## Our Solution

**CLARA** simulates the complex dynamics of clinical trials, emulating hundreds of patients' daily health statuses and their psychological tendency to underreport symptoms, then quantifies the measurable impact of proactive daily monitoring on patient outcomes.

> **Drug name in → Simulated trial out → Evidence that daily monitoring works → Deploy as real-world app**

CLARA consists of two MedGemma-based agents. We transitioned them from simulation to a **production mobile application** powered by MedGemma and HAI-DEF models, enabling continuous clinical visibility between visits.

<table>
<tr>
<td width="100" align="center">
<img src="docs/assets/nurse.png" width="80" /><br/>
<sub><b>DCA</b></sub>
</td>
<td>

**Data Collection Agent (DCA)** — Runs on the patient's device. Conducts daily ~60-second CLARA Call check-ins via video + voice interaction. Extracts structured clinical signals through multimodal processing: voice → context representation (MedGemma), audio → cough classification (HeAR), video → on-device feature map → server-side classification (MedSigLIP). Operates strictly within nursing scope: detect, report, refer.

</td>
</tr>
<tr>
<td width="100" align="center">
<img src="docs/assets/gemma.png" width="60" /><br/>
<sub><b>DAA</b></sub>
</td>
<td>

**Data Analysis Agent (DAA)** — Runs on the platform. Transforms daily signals into continuous patient timelines with AE/SAE flagging, auto-generated briefings, and report generation (CDISC-compliant CRF records, SAE narratives, FDA MedWatch 3500A, E2B XML). Powered by MedGemma 4B fine-tuned with RLFR (Reinforcement Learning Feature Reward), reducing hallucination rate from 37.3% to 6.7%.

</td>
</tr>
</table>

---

## HAI-DEF Models

<p align="center">
  <img src="docs/assets/gemma.png" width="44" alt="Gemma" />
  &nbsp;&nbsp;&nbsp;
  <img src="docs/assets/gemini.png" width="44" alt="Gemini" />
</p>

Each HAI-DEF model serves a distinct clinical role within CLARA:

| Model | Role in CLARA | Details |
|-------|--------------|---------|
| <img src="docs/assets/gemma.png" width="16" /> [**MedGemma 1.5 4B**](https://huggingface.co/google/medgemma-1.5-4b-it) | DCA — powers the conversational AI medical staff during daily CLARA Call check-ins | Generates clinically appropriate questions and assessments |
| <img src="docs/assets/gemma.png" width="16" /> [**MedSigLIP**](https://huggingface.co/google/medsiglip-448) | DCA — processes video frames captured during CLARA Call to detect visible AE symptoms (e.g., rash, swelling) and classify their CTCAE grade | Trained on 210 Gemini-generated synthetic patient images (147/21/42 split); W-F1 = 90% |
| <img src="docs/assets/gemma.png" width="16" /> [**HeAR**](https://huggingface.co/google/hear-pytorch) | DCA — analyzes patient audio through a cough detection and classification pipeline, distinguishing dry from wet cough | Fine-tuned on 956 randomly sampled recordings (478 dry / 478 wet) from the [COUGHVID dataset](https://www.kaggle.com/datasets/nasrulhakim86/coughvid-wav), tested on Gemini-generated synthetic audio |
| <img src="docs/assets/gemini.png" width="16" /> [**Gemini 2.0 Flash**](https://ai.google.dev/) | Simulation orchestrator — rule discovery, patient generation, narration | API-based; no local GPU needed |
| <img src="docs/assets/gemma.png" width="16" /> **MedGemma 4B + RLFR** | DAA — whenever a SAE occurs, autonomously generates reports in FDA MedWatch and E2B XML format | Hallucination rate 37.3% → 6.7% |

---

## Notebooks

CLARA ships with **5 Jupyter notebooks** demonstrating each component:

| # | Notebook | What It Covers | GPU |
|---|----------|---------------|-----|
| 1 | `medgemma_anti-hallucination` | RLFR fine-tuning — hallucination probe training and RL optimization | ~10 GB |
| 2 | `medgemma+medsiglip+HeAR_SAE-detection` | Multimodal AE detection — vision (MedSigLIP) + audio (HeAR) pipelines | ~20 GB |
| 3 | `simulate-clinical-trial` | End-to-end trial simulation — rule discovery, patient generation, daily loop | None (API) |
| 4 | `application_voice_call` | CLARA Call voice call demo — MedASR + TTS + nurse conversation | ~10 GB |
| 5 | `application_SAE_report_generation` | FDA MedWatch 3500A + E2B XML report generation from CRF data | ~10 GB |

All models and data **download automatically** from HuggingFace on first run:
- Models: [AlphaRaven/medgemma-ae-detection](https://huggingface.co/AlphaRaven/medgemma-ae-detection), [AlphaRaven/medgemma-4b-antihallu](https://huggingface.co/AlphaRaven/medgemma-4b-antihallu)
- Data: [AlphaRaven/clinical-trial-engine-data](https://huggingface.co/datasets/AlphaRaven/clinical-trial-engine-data)

---

## Results

### A/B Comparison: Standard Visit vs. CLARA Call

CLARA runs A/B comparisons on identical cohorts (N=100, same seed): Group A (no agent, bi-weekly visits only) vs. Group B (same trial + CLARA Call daily monitoring).

```
Group A (Standard Visit-Based)             Group B (+ CLARA Call)
No daily monitoring                        Daily ~60s video + voice check-ins

Hospital ──── 13 days ──── Hospital        Hospital ── 3 days ── Early Visit ── Hospital
              AE hidden                                AE detected    dose adjusted
              AE worsens                               early referral  faster recovery
```

| Metric | Group A (Standard) | Group B (CLARA Call) | Improvement |
|--------|-------------------|---------------------|-------------|
| AE Detection Delay (mean) | 4.6 days | **1.2 days** | **↓74%** |
| Discontinued (out of 100) | 21 | **17** | **↓19%** |
| Deaths (out of 100) | 21 | **16** | **↓24%** |
| Grade 3+ AE Duration (mean) | 1.4 days | **1.3 days** | **↓7%** |

### Anti-Hallucination (RLFR Fine-Tuning)

| Metric | Base MedGemma 4B | + RLFR | Change |
|--------|-----------------|--------|--------|
| Hallucination Rate (MedHallu Hard) | 37.3% | **6.7%** | -82% |
| MMLU Medical Score | 60.7% | **64.3%** | +5.9% |

### Visual AE Classification (MedSigLIP)

| Metric | Score |
|--------|-------|
| Weighted F1 (skin AE grading) | **90%** |
| Training data | 210 Gemini-generated images (147/21/42 split) |

---

## How It Works

> **Why Simulation?** Real-world daily multimodal longitudinal datasets do not yet exist at scale. So we built one — a rule-based simulation engine grounded in real clinical datasets and published drug safety profiles, validated against 7 known drug profiles.

CLARA operates through **three mechanisms**:

**1. Data-Driven Ruleset Generation** — Synthesizes historical clinical trial data from **10+ biomedical databases** (DailyMed, ClinicalTrials.gov, Project Data Sphere, PubMed, DrugBank, OnSIDES, etc.) to automatically build simulation rulesets covering AE incidence distributions, demographics, efficacy endpoints, and dose modification protocols.

**2. Realistic Daily Simulation with Information Asymmetry** — The simulation maintains two strict data layers: a **Ground Truth (GT)** layer computing each patient's actual daily status, and a **Hospital Record (HR)** layer updated only at clinic visits (typically every 2 weeks). All treatment decisions are made from HR only — never from GT. This gap mirrors real-world blind spots.

**3. Data Collection Agent — CLARA Call** — Patients complete a short, ~60-second daily check-in. Voice becomes structured context, audio enables cough classification, and video is converted into feature maps for server-side classification. These signals are fused through MedGemma for longitudinal multimodal reasoning, producing structured clinical variables that medical teams can act on.

---

## Technical Details

### DCA: Data Collection Agent

The DCA conducts daily CLARA Call check-ins following a **4-turn protocol**:

```
Turn 1: Patient describes how they feel        (speech → MedASR transcription)
Turn 2: Nurse asks targeted follow-up questions (MedGemma conversation)
Turn 3: Patient shows affected area on camera   (image → MedSigLIP classification)
Turn 4: Nurse summarizes and refers if needed   (MedGemma → early visit recommendation)
```

**Multimodal Pipeline:**

```
Voice → Context Representation              (MedGemma conversation)
Audio → Cough Classification (Dry/Wet/None) (HeAR audio classification)
Video → On-device Feature Map               (MedSigLIP vision analysis)
      → Server-side Classification

Signal Fusion → MedGemma Reasoning → Structured Clinical Variables
```

Multimodal detection channels cover five categories:

```
Channel              Examples                    Detection Method
────────────────────────────────────────────────────────────────
lab                  neutropenia, anemia          Blood test (hospital only)
patient_reported     nausea, pain, fatigue        Patient self-report (mood-dependent)
video_detectable     rash, alopecia, edema        MedSigLIP vision analysis
audio_detectable     cough, dyspnea              HeAR audio classification
physical_exam        neuropathy, hepatomegaly     Doctor examination (hospital only)
```

### DAA: Data Analysis Agent

The DAA transforms daily signals into continuous patient timelines with AE/SAE flagging, auto-generated briefings, and report generation.

We discovered that MedGemma 1.5 4B is vulnerable to hallucinations due to its multi-modal attribution. CLARA uses **Reinforcement Learning Feature Reward (RLFR)**:

1. Train a binary hallucination detection probe on MedGemma's hidden states
2. Freeze the probe
3. Use probe confidence as the reward signal for RL fine-tuning

This reduced the hallucination rate from **37.3% to 6.7%** on MedHallu Hard while *improving* MMLU medical scores from 60.7% to 64.3%. In medical settings, grounded and stable outputs are not optional — they are essential.

### Simulation Pipeline

<details>
<summary><b>Hazard Function</b></summary>

<br/>

Instead of a pre-determined fate table, CLARA uses a mixture model to compute daily AE onset probability:

```
P(onset on day t | no onset before t) = I · f(t) / (1 − I · F(t−1))

Where:
  I    = patient-adjusted AE incidence rate
  F(t) = onset CDF (Normal, LogNormal, or Uniform)
  f(t) = F(t) − F(t−1)  (discrete probability mass)
```

AE grade transitions use daily Markov probabilities:
- Base worsen rate: 1.5%/day (increases with cumulative toxicity)
- Base improve rate: 0.5%/day
- CLARA Call intervention: worsen ×0.3, improve ×3.0

</details>

<details>
<summary><b>Observation Model (GT ↔ HR)</b></summary>

<br/>

The observation model implements a **whitelist-based filter** — HR only receives GT information through defined observation channels:

| Observation Point | What Gets Updated | Trigger |
|-------------------|------------------|---------|
| Scheduled visit | Full exam: labs, vitals, physical exam, AE assessment | Treatment cycle day |
| Scheduled scan | Tumor/RECIST data | Protocol-defined intervals |
| Self-report | Patient-reported AEs only | Mood-dependent probability |
| Video call (CLARA Call) | video_detectable + patient_reported AEs | Daily (CLARA Call mode) |
| ER visit | Full exam | Grade 4+ AE or dangerous vitals |

HR never falls back to GT. If a lab value wasn't measured, it stays stale.

</details>

<details>
<summary><b>7-Dimension Mood Model</b></summary>

<br/>

Each virtual patient has a persistent psychological profile that evolves over time and directly impacts clinical data collection:

| Dimension | Effect on Simulation |
|-----------|---------------------|
| **Anxiety** | High → over-reports symptoms, seeks ER visits early |
| **Depression** | High → reduced reporting motivation, missed calls |
| **Fatigue** | High → shorter video calls, less engagement |
| **Irritability** | High → terminates calls early, refuses visual inspection |
| **Hopefulness** | High → better treatment adherence, attends visits |
| **Defensiveness** | High → minimizes symptoms (reports Grade 2 as Grade 1) |
| **Trust** | High → accurate reporting, engages fully with CLARA Call |

Persona-specific baselines are set at patient generation. Events (new AE, good scan result, CLARA Call empathy) cause daily micro-adjustments.

</details>

---

## Quick Start

### Prerequisites

- Python 3.10+
- NVIDIA GPU with 10–20 GB VRAM (for notebooks; simulation CLI uses Gemini API only)
- [Google Gemini API key](https://ai.google.dev/)

### Installation

```bash
git clone https://github.com/newmes/CLARA.git
cd CLARA

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

### HuggingFace Login (for gated models)

Request access to [google/medgemma-4b-it](https://huggingface.co/google/medgemma-4b-it) and [google/medsiglip-448](https://huggingface.co/google/medsiglip-448), then:

```bash
python -c "from huggingface_hub import login; login()"
```

### Environment

```bash
cp .env.example .env
# Set GOOGLE_API_KEY (required for NB3, NB5 and CLI simulation)
```

### Run Notebooks

```bash
jupyter lab notebooks/
# Run sequentially: 1 → 5
```

### Run Simulation (CLI)

```bash
# Default: Padcev + Pembrolizumab, 1 patient, 21 days
python src/run_simulation_v2.py

# A/B comparison: 50 patients, 84 days (4 cycles)
python src/run_simulation_v2.py --patients 50 --days 84 --seed 42 --mode both

# Drug-agnostic: any drug works
python src/run_simulation_v2.py --drug "Ozempic" --indication "type 2 diabetes" --patients 5
```

---

## Team

**dmyoun** · **lisavictorialee** · **hyenawon** · **sabapivot** · **jjin6573**

## Citation

```bibtex
@software{clara2026,
  title   = {CLARA: Clinical Longitudinal AI Research Assistant},
  author  = {dmyoun and lisavictorialee and hyenawon and sabapivot and jjin6573},
  year    = {2026},
  url     = {https://github.com/newmes/CLARA}
}
```

## References

- Basch, E. et al. (2006). *Lancet Oncol.*, 7(11), 903–909. [doi:10.1016/S1470-2045(06)70910-X](https://doi.org/10.1016/S1470-2045(06)70910-X)
- Di Maio, M. et al. (2015). *J Clin Oncol.*, 33(8), 910–915. [doi:10.1200/JCO.2014.57.9334](https://doi.org/10.1200/JCO.2014.57.9334)
- Izarn, F. et al. (2025). *ESMO Open*, 10(1), 104086. [doi:10.1016/j.esmoop.2024.104086](https://doi.org/10.1016/j.esmoop.2024.104086)
- Orlandic, L. et al. (2021). *Sci Data*, 8, 156. [doi:10.1038/s41597-021-00937-4](https://doi.org/10.1038/s41597-021-00937-4)
- Atmaja, B. T. et al. (2023). *Int. J. Inf. Technol.* [doi:10.1007/s41870-023-01626-8](https://doi.org/10.1007/s41870-023-01626-8)

## Acknowledgments

<p>
  Built with
  <a href="https://ai.google.dev/"><img src="docs/assets/gemini.png" width="18" align="center" /> Google Gemini</a>
  and the
  <a href="https://developers.google.com/health-ai-developer-foundations"><img src="docs/assets/gemma.png" width="18" align="center" /> HAI-DEF</a>
  model collection.
  <br/>
  Submitted to the <a href="https://www.kaggle.com/competitions/med-gemma-impact-challenge">MedGemma Impact Challenge</a> on Kaggle.
</p>
