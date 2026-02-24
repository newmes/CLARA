<div align="center">

<img src="docs/assets/logo.svg" width="100" alt="CLARA Logo" />

# CLARA: Clinical Longitudinal AI Research Assistant

**A simulation-powered framework that proves AI nursing care saves lives &mdash;**
**then deploys it as a real-world multimodal application.**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](#)
[![License](https://img.shields.io/badge/License-Apache_2.0-green.svg)](#license)
[![HAI-DEF](https://img.shields.io/badge/HAI--DEF-MedGemma-FF6F00?logo=google&logoColor=white)](https://developers.google.com/health-ai-developer-foundations)
[![Kaggle](https://img.shields.io/badge/MedGemma_Impact-Challenge_2026-20BEFF?logo=kaggle&logoColor=white)](https://www.kaggle.com/competitions/med-gemma-impact-challenge)

[Quick Start](#quick-start) &middot; [Notebooks](#notebooks) &middot; [Architecture](#architecture) &middot; [Results](#results) &middot; [Deployment](#docker-deployment)

</div>

---

<p align="center">
  <img src="docs/assets/figure.png" width="100%" alt="CLARA System Overview — Data Collection Agent, Simulation Village, and Data Analysis Agent" />
</p>

---

## The Problem

In oncology clinical trials, patient safety monitoring relies on clinic visits scheduled every **12&ndash;21 days**. Between visits, no one is watching.

- **400,000+** oncology trial participants are affected globally each year ([Izarn et al., *ESMO Open*, 2025](https://doi.org/10.1016/j.esmoop.2024.104086))
- Physician&ndash;patient AE grade agreement is only **~50%** ([Basch et al., *Lancet Oncol.*, 2006](https://doi.org/10.1016/S1470-2045(06)70910-X))
- Physicians routinely fail to capture **57% of AEs** ([Di Maio et al., *J Clin Oncol.*, 2015](https://doi.org/10.1200/JCO.2014.57.9334))
- Patient self-reporting rates for Grade 1&ndash;2 AEs are as low as **~2%**

Most patients underreport discomfort. These missed signals lead to grade escalation, emergency hospitalization, forced dose interruptions, and loss of life.

## Our Solution

**CLARA** simulates complete clinical trials with and without an AI nurse agent, then quantifies the measurable impact of proactive daily monitoring on patient outcomes.

> **Drug name in &rarr; Simulated trial out &rarr; Evidence that AI nursing care works &rarr; Deploy as real-world app**

We then transitioned the AI nurse from simulation to a **production mobile application** powered by MedGemma and HAI-DEF models.

<table>
<tr>
<td width="100" align="center">
<img src="docs/assets/nurse.png" width="80" /><br/>
<sub><b>AI Nurse</b></sub>
</td>
<td>

**Data Collection Agent** &mdash; Conducts daily structured video calls with patients. Detects visual AEs (rash, swelling) via MedSigLIP, audio AEs (cough) via HeAR, and assesses symptoms through empathetic MedGemma-powered conversation. Operates strictly within nursing scope: detect, report, refer.

</td>
</tr>
<tr>
<td width="100" align="center">
<img src="docs/assets/gemma.png" width="60" /><br/>
<sub><b>RLFR Model</b></sub>
</td>
<td>

**Data Analysis Agent** &mdash; Generates CDISC-compliant CRF records, SAE narratives, and FDA MedWatch 3500A reports. Powered by MedGemma 4B fine-tuned with RLFR (Reinforcement Learning Feature Reward), reducing hallucination rate from 37.3% to 6.7%.

</td>
</tr>
</table>

<details>
<summary><b>Table of Contents</b></summary>

- [The Problem](#the-problem)
- [Our Solution](#our-solution)
- [Key Results](#results)
- [How It Works](#how-it-works)
- [Architecture](#architecture)
- [HAI-DEF Models](#hai-def-models)
- [Notebooks](#notebooks)
- [Quick Start](#quick-start)
- [Docker Deployment](#docker-deployment)
- [Project Structure](#project-structure)
- [Citation](#citation)
- [License](#license)

</details>

---

## Results

### A/B Comparison: Natural vs. Care AI

The same virtual patient cohort is simulated under two conditions &mdash; identical seed, identical biology, diverging only in whether a daily AI nurse is present.

```
Run A (Natural)                        Run B (Care AI)
No AI nurse                            Daily 4-turn video calls

Hospital ──── 13 days ──── Hospital    Hospital ── 3 days ── Early Visit ── Hospital
              AE hidden                            AE detected    dose adjusted
              AE worsens                           early referral  faster recovery
```

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

CLARA operates through **three mechanisms**:

### 1. Data-Driven Ruleset Generation

CLARA synthesizes clinical trial data from **10+ biomedical databases** &mdash; DailyMed, ClinicalTrials.gov, Project Data Sphere, PubMed, DrugBank, OnSIDES, and more &mdash; to automatically build simulation rulesets covering AE incidence distributions, demographics, efficacy endpoints, and dose modification protocols.

### 2. Realistic Daily Simulation with Information Asymmetry

The simulation maintains two strict data layers:

| Layer | What It Knows | Updated When |
|-------|--------------|--------------|
| **Ground Truth (GT)** | Patient's actual daily state &mdash; every AE, every lab value | Every day (hazard function) |
| **Hospital Record (HR)** | Only what the hospital has observed | Clinic visits only (every 12&ndash;21 days) |

All treatment decisions (dose hold, reduce, withdraw) are made from HR only &mdash; never from GT. This gap mirrors the real-world blind spot.

### 3. Data Collection Agent (AI Nurse)

Daily structured video calls following a **4-turn protocol**:

```
Turn 1: Patient describes how they feel        (speech → MedASR transcription)
Turn 2: Nurse asks targeted follow-up questions (MedGemma conversation)
Turn 3: Patient shows affected area on camera   (image → MedSigLIP classification)
Turn 4: Nurse summarizes and refers if needed   (MedGemma → early visit recommendation)
```

A **7-dimension mood model** (anxiety, depression, fatigue, irritability, hopefulness, defensiveness, trust) governs each patient's reporting behavior. The agent adapts its strategy accordingly.

### 10-Step Daily Pipeline

Each patient goes through this pipeline every simulated day:

| Step | Name | Description |
|------|------|-------------|
| 1 | Stochastic AE Onset | Hazard function determines new adverse events |
| 2 | AE Grade Transition | Active AEs worsen or improve based on cumulative toxicity |
| 3 | Tumor & RECIST Evaluation | Tumor response on scheduled scan days |
| 4 | Dose Modification | Hold/reduce/withdraw &mdash; hospital visit days only, HR-based |
| 5 | Lab Data Simulation | CBC, metabolic panel, liver/kidney function |
| 6 | Vitals Simulation | BP, heart rate, temperature, SpO2 |
| 7 | CDASH/CRF Mapping | Daily records mapped to CDISC clinical trial format |
| 8 | AE Cascade Update | Secondary AE chains triggered by primary events |
| 9 | Dynamic ECOG PS | Performance status adjusted by AE burden |
| 10 | Mortality Assessment | Life-threatening event evaluation |

---

## Architecture

```
Drug Name + Indication
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 0: Rule Discovery                                            │
│  10+ biomedical DBs → Gemini 2.0 Flash → rule_set.json             │
│  (AE incidence, onset distributions, demographics, dose rules)      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1: Virtual Cohort Generation                                 │
│  Step 1: Demographic Profiling    Step 3: Labs Construction         │
│  Step 2: Comorbidity Modeling     Step 4: Persona Assignment        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 2: Daily Simulation (10-step loop × N patients × D days)     │
│                                                                     │
│  ┌──────────┐   ┌──────────────┐   ┌───────────────────┐           │
│  │ Hazard   │──▶│ Ground Truth │──▶│ Observation Model │           │
│  │ Function │   │ (actual AEs) │   │ (GT → HR filter)  │           │
│  └──────────┘   └──────────────┘   └─────────┬─────────┘           │
│                                               │                     │
│                        ┌──────────────────────┼──────────────┐      │
│                        ▼                      ▼              │      │
│              ┌──────────────────┐   ┌──────────────────┐     │      │
│              │ Hospital Record  │   │  Care AI Nurse   │     │      │
│              │ (clinic visits)  │   │  (daily calls)   │     │      │
│              │ → dose decisions │   │  → early referral│     │      │
│              └──────────────────┘   └──────────────────┘     │      │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Data Analysis Agent (RLFR-powered MedGemma)                        │
│  CRF records → SAE narratives → MedWatch 3500A → E2B XML           │
│  Anti-hallucination: frozen probe as reward signal                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

| Principle | Why |
|-----------|-----|
| **LLM sets probabilities, code rolls dice** | Prevents mode collapse; guarantees statistical distributions |
| **Ground Truth vs. Hospital Record** | Dose decisions use observed data only &mdash; never omniscient GT |
| **Drug-agnostic** | Change the drug name &rarr; system auto-discovers rules from 10+ databases |
| **No fate table** | Daily hazard functions replace pre-determined event timelines |
| **Care AI detects, doctors decide** | AI nurse flags and refers; only physicians modify treatment |
| **Seed-reproducible** | Same seed &rarr; identical simulation for scientific rigor |

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
| <img src="docs/assets/gemma.png" width="16" /> [**MedGemma 1.5 4B**](https://huggingface.co/google/medgemma-1.5-4b-it) | Data Collection Agent &mdash; conversational AI nurse for daily video calls | Clinically appropriate questions and assessments |
| <img src="docs/assets/gemma.png" width="16" /> [**MedSigLIP**](https://huggingface.co/google/medsiglip-448) | Visual AE detection &mdash; classifies skin AEs from video call images | Fine-tuned on 210 Gemini-generated images; W-F1 = 90% |
| <img src="docs/assets/gemma.png" width="16" /> [**HeAR**](https://huggingface.co/google/hear-pytorch) | Audio AE detection &mdash; cough detection and dry/wet classification | Fine-tuned on 956 COUGHVID samples |
| <img src="docs/assets/gemini.png" width="16" /> [**Gemini 2.0 Flash**](https://ai.google.dev/) | Simulation orchestrator &mdash; rule discovery, patient generation, narration | API-based; no local GPU needed |
| <img src="docs/assets/gemma.png" width="16" /> **MedGemma 4B + RLFR** | Data Analysis Agent &mdash; CRF records, SAE narratives, MedWatch reports | Hallucination rate 37.3% &rarr; 6.7% |

### Multimodal Detection Channels

```
Channel              Examples                    Detection Method
────────────────────────────────────────────────────────────────
lab                  neutropenia, anemia          Blood test (hospital only)
patient_reported     nausea, pain, fatigue        Patient self-report (mood-dependent)
video_detectable     rash, alopecia, edema        MedSigLIP vision analysis
audio_detectable     cough, dyspnea              HeAR audio classification
physical_exam        neuropathy, hepatomegaly     Doctor examination (hospital only)
```

---

## Notebooks

CLARA ships with **5 Jupyter notebooks** demonstrating each component:

| # | Notebook | What It Covers | GPU |
|---|----------|---------------|-----|
| 1 | `medgemma_anti-hallucination` | RLFR fine-tuning &mdash; hallucination probe training and RL optimization | ~10 GB |
| 2 | `medgemma+medsiglip+HeAR_SAE-detection` | Multimodal AE detection &mdash; vision (MedSigLIP) + audio (HeAR) pipelines | ~20 GB |
| 3 | `simulate-clinical-trial` | End-to-end trial simulation &mdash; rule discovery, patient generation, daily loop | None (API) |
| 4 | `application_voice_call` | Care AI voice call demo &mdash; MedASR + TTS + nurse conversation | ~10 GB |
| 5 | `application_SAE_report_generation` | FDA MedWatch 3500A + E2B XML report generation from CRF data | ~10 GB |

All models and data **download automatically** from HuggingFace on first run:
- Models: [AlphaRaven/medgemma-ae-detection](https://huggingface.co/AlphaRaven/medgemma-ae-detection), [AlphaRaven/medgemma-4b-antihallu](https://huggingface.co/AlphaRaven/medgemma-4b-antihallu)
- Data: [AlphaRaven/clinical-trial-engine-data](https://huggingface.co/datasets/AlphaRaven/clinical-trial-engine-data)

---

## Quick Start

### Prerequisites

- Python 3.10+
- NVIDIA GPU with 10&ndash;20 GB VRAM (for notebooks; simulation CLI uses Gemini API only)
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

## Docker Deployment

CLARA deploys as a multi-service Docker stack with GPU acceleration:

```bash
docker compose up -d
```

| Service | Port | GPU | Purpose |
|---------|------|-----|---------|
| `django` | 19001 | &mdash; | Web interface (trial viewer, CRF tables, SAE reports) |
| `medgemma4b-base` | 38004 | GPU 2 | Care AI nurse backend (vLLM) |
| `medgemma4b-antihallu-ft` | 38002 | GPU 3 | Doc Agent + anti-hallucination (vLLM + LoRA) |
| `antihallu-server` | 38003 | GPU 2 | Hallucination fact-checking API |
| `data-collection-agent` | 38005 | GPU 3 | Multimodal detection (SigLIP + HeAR + MedASR + TTS) |

---

## Project Structure

```
CLARA/
├── notebooks/                     5 demo notebooks (anti-hallu → SAE reports)
│
├── src/
│   ├── engine/                    Probability engine (LLM-independent)
│   │   ├── hazard.py              Daily AE onset/grade hazard functions
│   │   ├── observation.py         Ground Truth ↔ Hospital Record model
│   │   ├── sampler.py             Seed-reproducible random sampling
│   │   ├── mood.py                7-dimension patient psychology
│   │   └── prob_engine.py         LLM → rand → LLM orchestration
│   │
│   ├── agents/                    LLM agents (Gemini-powered)
│   │   ├── rule_agent.py          Phase 0: drug rule discovery
│   │   ├── patient_agent.py       Phase 1: virtual cohort generation
│   │   ├── daily_agent.py         Phase 2: daily simulation
│   │   └── care_agent.py          Care AI: 4-turn video calls
│   │
│   ├── multimodal_v2/             Multimodal AE detection pipeline
│   ├── cough_detection/           HeAR-based cough classification
│   ├── orchestrator_v2.py         3-Phase simulation orchestrator
│   └── run_simulation_v2.py       CLI entry point
│
├── dca_server/                       Care AI Nurse API (FastAPI)
│   ├── server.py                  Endpoints: classify, cough, transcribe, nurse
│   ├── nurse_engine.py            Medical conversation engine
│   ├── siglip_classifier.py       SigLIP vision classifier head
│   └── cough_classifier.py        Cough audio classifier
│
├── frontend/                      Django web interface
│   ├── viewer/                    Trial viewer, patient state, CRF tables
│   └── templates/                 Interactive simulation visualization
│
├── models/                        Fine-tuned checkpoints
│   ├── medgemma-4b-ft-antihallu/  RLFR anti-hallucination (hallu 37.3% → 6.7%)
│   └── medgemma-4b-ft-ctcae/      Skin AE classifier (W-F1 = 90%)
│
├── data/                          Simulation outputs
│   ├── rule_set.json              Default Padcev+Pembro rule set
│   └── runs/                      Per-experiment results (JSONL)
│
└── docker-compose.yaml            Multi-GPU service orchestration
```

---

## Technical Details

<details>
<summary><b>Hazard Function Mathematics</b></summary>

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
- Care AI intervention: worsen &times;0.3, improve &times;3.0

</details>

<details>
<summary><b>Observation Model (GT ↔ HR)</b></summary>

<br/>

The observation model implements a **whitelist-based filter** &mdash; HR only receives GT information through defined observation channels:

| Observation Point | What Gets Updated | Trigger |
|-------------------|------------------|---------|
| Scheduled visit | Full exam: labs, vitals, physical exam, AE assessment | Treatment cycle day |
| Scheduled scan | Tumor/RECIST data | Protocol-defined intervals |
| Self-report | Patient-reported AEs only | Mood-dependent probability |
| Video call (Care AI) | video_detectable + patient_reported AEs | Daily (Care AI mode) |
| ER visit | Full exam | Grade 4+ AE or dangerous vitals |

HR never falls back to GT. If a lab value wasn't measured, it stays stale.

</details>

<details>
<summary><b>7-Dimension Mood Model</b></summary>

<br/>

Each virtual patient has a persistent psychological profile that evolves over time and directly impacts clinical data collection:

| Dimension | Effect on Simulation |
|-----------|---------------------|
| **Anxiety** | High &rarr; over-reports symptoms, seeks ER visits early |
| **Depression** | High &rarr; reduced reporting motivation, missed calls |
| **Fatigue** | High &rarr; shorter video calls, less engagement |
| **Irritability** | High &rarr; terminates calls early, refuses visual inspection |
| **Hopefulness** | High &rarr; better treatment adherence, attends visits |
| **Defensiveness** | High &rarr; minimizes symptoms (reports Grade 2 as Grade 1) |
| **Trust** | High &rarr; accurate reporting, engages fully with AI nurse |

Persona-specific baselines are set at patient generation. Events (new AE, good scan result, Care AI empathy) cause daily micro-adjustments.

</details>

<details>
<summary><b>RLFR Anti-Hallucination Training</b></summary>

<br/>

Standard MedGemma 4B is vulnerable to hallucinations due to multi-modal attribution. CLARA uses **Reinforcement Learning Feature Reward (RLFR)**:

1. Train a binary hallucination detection probe on MedGemma's hidden states
2. Freeze the probe
3. Use probe confidence as the reward signal for RL fine-tuning

This reduced the hallucination rate from **37.3% to 6.7%** on MedHallu Hard while *improving* MMLU medical scores from 60.7% to 64.3%.

</details>

---

## Team

**dmyoun** &middot; **lisavictorialee** &middot; **hyenawon** &middot; **sabapivot** &middot; **jjin6573**

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

- Basch, E. et al. (2006). *Lancet Oncol.*, 7(11), 903&ndash;909. [doi:10.1016/S1470-2045(06)70910-X](https://doi.org/10.1016/S1470-2045(06)70910-X)
- Di Maio, M. et al. (2015). *J Clin Oncol.*, 33(8), 910&ndash;915. [doi:10.1200/JCO.2014.57.9334](https://doi.org/10.1200/JCO.2014.57.9334)
- Izarn, F. et al. (2025). *ESMO Open*, 10(1), 104086. [doi:10.1016/j.esmoop.2024.104086](https://doi.org/10.1016/j.esmoop.2024.104086)

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

## License

[Apache 2.0](LICENSE)
