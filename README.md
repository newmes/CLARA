# Clinical Trial Patient Simulation Engine v2

LLM-driven clinical trial patient simulation for AI nurse (Care AI) training and evaluation.

## Deployment

### Prerequisites

- Docker & Docker Compose v2
- NVIDIA driver (535+) and NVIDIA Container Toolkit (for GPU services)
- User must be in the `docker` group (`sudo usermod -aG docker $USER && newgrp docker`)

### Step 1: Create the Docker network

The services communicate over an external Docker network called `vital-net`:

```bash
docker network create vital-net
# Safe to ignore "already exists" errors
```

### Step 2: Start the Django dashboard (no GPU required)

```bash
cd /path/to/CLARA
docker compose up -d --build django
```

This builds the `clara-django` image (Python 3.10-slim) and starts the `clara_dev` container.

- **Host port**: `19001` → **Container port**: `9001`
- Dashboard URL: `http://<host-ip>:19001/`

### Step 3: Start the MedGemma GPU services

The dashboard depends on three vLLM model servers for anti-hallucination and CTCAE grading. Each requires a GPU and locally available model weights.

| Service | Profile | Default GPU | Model path | Purpose |
|---------|---------|-------------|------------|---------|
| `medgemma4b-ctcae-ft` | `medgemma4b-ft` | GPU 3 | `./models/medgemma-4b-ft-ctcae` | Fine-tuned CTCAE grading |
| `medgemma4b-base` | `medgemma4b-base` | GPU 2 | `/data2/huggingface/hub/models--google--medgemma-1.5-4b-it` | Base MedGemma 1.5 4B |
| `medgemma4b-antihallu-ft` | `medgemma4b-antihallu` | GPU 2 | `./models/medgemma-4b-ft-antihallu` | Anti-hallucination detector |

**Prepare model weights** before starting GPU services:

```bash
# Create model directories
mkdir -p models/medgemma-4b-ft-ctcae
mkdir -p models/medgemma-4b-ft-antihallu
# Place the fine-tuned model weights in the directories above.
# The base model should be at /data2/huggingface/hub/models--google--medgemma-1.5-4b-it
```

**Start all GPU services:**

```bash
docker compose --profile medgemma4b-ft \
               --profile medgemma4b-base \
               --profile medgemma4b-antihallu \
               up -d
```

Or start individually:

```bash
# Anti-hallucination model only (used by Django for CTE verification)
docker compose --profile medgemma4b-antihallu up -d medgemma4b-antihallu-ft
```

**Override GPU assignment** via environment variables:

```bash
GPU_MEDGEMMA4B_FT=1 GPU_MEDGEMMA4B=0 docker compose --profile medgemma4b-ft --profile medgemma4b-antihallu up -d
```

### Step 4: Verify

```bash
# Check containers are running
docker ps --filter "name=clara"

# Test Django dashboard
curl -s -o /dev/null -w "%{http_code}" http://localhost:19001/
# Expected: 200

# Test vLLM model servers (if GPU services are up)
curl -s http://localhost:38001/v1/models  # CTCAE fine-tuned
curl -s http://localhost:38002/v1/models  # Anti-hallucination
curl -s http://localhost:38004/v1/models  # Base MedGemma
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITAL_CONTAINER` | `clara_dev` | Django container name |
| `VITAL_PORT` | `19001` | Host port for the dashboard |
| `GPU_MEDGEMMA4B_FT` | `3` | GPU device ID for CTCAE model |
| `GPU_MEDGEMMA4B` | `2` | GPU device ID for anti-hallucination model |

### Service architecture

```
Browser ──→ Django (:19001)
               ├──→ medgemma4b-antihallu-ft (:38002) — CTE anti-hallucination
               ├──→ medgemma4b-ctcae-ft     (:38001) — CTCAE grading
               └──→ medgemma4b-base         (:38004) — Base MedGemma
            (all connected via vital-net Docker network)
```

---

## Problem

Clinical trials monitor patients through scheduled hospital visits (every 3 weeks). Between visits, adverse events (AEs) can silently escalate — a Grade 2 rash becomes SJS/TEN, hyperglycemia becomes DKA. An AI nurse conducting video calls between visits could detect these earlier, but we need realistic patient data to train and evaluate it.

## Solution

3 LLM agents collaborate to generate day-by-day patient data:

1. **God Agent** — Creates realistic patient profiles (demographics, medical history, persona)
2. **Fate Agent** — Determines what AEs will occur without intervention (the "baseline destiny")
3. **Progression Agent** — Generates each day's labs, vitals, AE status, visual appearance, and patient's subjective experience

A **Care Agent** (Patient LLM + Nurse LLM) optionally conducts video call simulations. Its decisions are recorded as `care_record` entries in patient data, which the Progression Agent naturally incorporates into subsequent days.

**Key insight**: Same patient + same fate table + different care_record = different outcomes. This quantifies the value of Care AI intervention.

## Target Scenario

**Padcev (enfortumab vedotin) + Pembrolizumab** for metastatic urothelial carcinoma (bladder cancer), based on EV-302 trial design.

Key demo scenarios:
- **Triple Rash Differential**: Padcev skin toxicity vs Pembro immune-related AE vs SJS/TEN (BLACK BOX warning) — distinguished by visual features on video call
- **Urinary Frequency Paradox**: Bladder cancer symptom vs DKA prodrome vs age-related — detected by cross-referencing glucose labs with medical history

## Architecture

```
drug_name → God Agent → Patient Profile
                           ↓
            Fate Agent → Fate Table (natural course)
                           ↓
         ┌─ Day Loop ──────────────────────────────┐
         │  Progression Agent                       │
         │    (fate + history + care_record)        │
         │    → today's objective + subjective state│
         │                                          │
         │  [Optional] Care Agent                   │
         │    → care_record appended to patient data│
         └──────────────────────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | **Read this first.** Complete architecture, schemas, conventions. |
| `TODO.md` | Prioritized task list with 13-day roadmap |
| `docs/architecture_v2.md` | Detailed design with full JSON schema examples |
| `docs/drug_profile.md` | Padcev+Pembro AE profile (LLM output validation reference) |
| `src/soa_pipeline.py` | Legacy v1 pipeline (reference for SoA calendar logic) |

## Design Principles

1. **Zero hardcoding** — All medical knowledge inferred by LLM from drug name
2. **Internal consistency** — AE ↔ Lab ↔ Vitals always medically coherent
3. **Day-by-day simulation** — Cannot batch-generate; Care AI intervention changes trajectory
4. **care_record as branching mechanism** — Empty = natural course, populated = intervention reflected

## MedGemma Impact Challenge

- Deadline: February 24, 2026
- Prize: $100K
- Focus: Demonstrating MedGemma's value in multimodal AE detection (visual + lab + patient report)
