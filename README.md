# Clinical Trial Patient Simulation Engine v2

LLM-driven clinical trial patient simulation for AI nurse (Care AI) training and evaluation.

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

## Quick Start

```bash
# Not yet runnable — implementation in progress
# See TODO.md for current status and CLAUDE.md for full architecture
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
