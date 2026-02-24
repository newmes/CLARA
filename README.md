# Clinical Trial Simulation Engine

**MedGemma-powered Care AI**: A multimodal nurse agent that detects adverse events early through video calls, embedded in an end-to-end clinical trial simulation pipeline.

Built for the **MedGemma Impact Challenge** (Feb 2026, $100K).

---

## What This Does

A drug name goes in → a complete clinical trial simulation comes out, with daily patient records, AI nurse interventions, and pharmacovigilance documents.

**Core value proposition**: Care AI conducts daily video calls between hospital visits, detecting visual/audio AEs days earlier than scheduled visits alone. Same patients, same biology — statistically significant difference in outcomes.

## Pipeline (7 Notebooks)

```
┌─ Multimodal AE Detection ─────────────────────────────────────────┐
│  NB1  Visual AE Detection     MedGemma 1.5 + MedSigLIP (image→AE)│
│  NB2  Cough Detection         HeAR + 2-stage classifier (audio→dx)│
│  NB3  Care AI Conversation    MedGemma 4B virtual nurse (dialogue)│
├─ Clinical Trial Simulation ───────────────────────────────────────┤
│  NB4  Rule Set Generation     10 biomedical DBs → LLM → parameters│
│  NB5  Simulation Pipeline     Hazard functions → synthetic trial   │
├─ Pharmacovigilance ───────────────────────────────────────────────┤
│  NB6  Anti-Hallucination      RLFR fine-tuning for medical text    │
│  NB7  Doc Agent               CRF → MedWatch 3500A reports         │
└───────────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/AlphaRaven/ClinicalTrialEngine.git
cd ClinicalTrialEngine

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -e .

# NB2 only (cough detection — requires TensorFlow):
# pip install -r requirements-tf.txt
```

### 2. HuggingFace Login (required for gated Google models)

Request access to these models first:
- [google/medgemma-1.5-4b-it](https://huggingface.co/google/medgemma-1.5-4b-it)
- [google/medgemma-4b-it](https://huggingface.co/google/medgemma-4b-it)
- [google/medsiglip-448](https://huggingface.co/google/medsiglip-448)

Then login:

```bash
python -c "from huggingface_hub import login; login()"
```

### 3. Environment Variables

```bash
cp .env.example .env
# Edit .env — set GOOGLE_API_KEY (required for NB4, NB5)
```

### 4. Run Notebooks

Open `notebooks/` in Jupyter and run sequentially (1→7).

All models and data download automatically from HuggingFace on first run:
- **Models**: [AlphaRaven/medgemma-ae-detection](https://huggingface.co/AlphaRaven/medgemma-ae-detection), [AlphaRaven/medgemma-4b-antihallu](https://huggingface.co/AlphaRaven/medgemma-4b-antihallu)
- **Data**: [AlphaRaven/clinical-trial-engine-data](https://huggingface.co/datasets/AlphaRaven/clinical-trial-engine-data)

## GPU Requirements

| Notebook | GPU VRAM | Notes |
|----------|----------|-------|
| NB1 (Visual AE) | ~20 GB | Loads 2 MedGemma + 1 SigLIP simultaneously |
| NB2 (Cough) | ~2 GB | TensorFlow HeAR model |
| NB3 (Care AI) | ~10 GB | Single MedGemma 4B |
| NB4–5 (Simulation) | None | LLM API calls only (Gemini) |
| NB6 (Anti-Hallu) | ~10 GB | Single MedGemma 4B |
| NB7 (Doc Agent) | ~10 GB | Single MedGemma 4B |

Default GPU index is `0`. Override with `GPU_ID` environment variable.

## Environment Variables

| Variable | Required | Used By | Description |
|----------|----------|---------|-------------|
| `GOOGLE_API_KEY` | NB4, NB5 | Gemini API for rule/simulation generation |
| `GPU_ID` | Optional | All GPU notebooks | GPU index (default: 0) |
| `HF_DATA_REPO` | Optional | NB1–3 | Override dataset repo |
| `GEMMA_FT_MODEL_ID` | Optional | NB1 | Override AE detection model |
| `ANTIHALLU_MODEL_ID` | Optional | NB6 | Override anti-hallucination model |
| `DOC_AGENT_MODEL_ID` | Optional | NB7 | Override doc agent model |

## Architecture (v2.3)

```
LLM defines probability rules → Code rolls the dice → LLM fills in details
```

- **Phase 0**: Rule Agent discovers AE incidence/onset distributions from drug name
- **Phase 1**: Patient generation (demographics, comorbidities, persona)
- **Phase 2**: Daily simulation with hazard functions, observation model, and Care AI

Key design: **Ground Truth ↔ Hospital Record separation**. The hospital only knows what it can observe. Care AI bridges the gap by detecting AEs between visits.

See `CLAUDE.md` for full technical documentation.

## Key Files

| Path | Description |
|------|-------------|
| `CLAUDE.md` | Complete technical architecture (read this for deep dive) |
| `notebooks/` | 7 sequential demo notebooks |
| `src/engine/` | Probability engine (hazard, sampler, observation model) |
| `src/agents/` | LLM agents (rule, patient, daily, care) |
| `src/multimodal_v2/` | Multimodal AE detection config |
| `src/cough_detection/` | Cough audio analysis |
| `scripts/upload_to_hf.py` | Upload models/data to HuggingFace |

## License

Apache 2.0
