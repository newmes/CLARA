#!/usr/bin/env python3
"""
new_drugs base.json의 누락/null 필드를 의학적 근거에 기반해 채워넣는 스크립트.

채우는 필드:
  1. ae_profile[].onset_day      — AE별 onset timing distribution
  2. ae_profile[].duration_days  — AE별 duration distribution
  3. ae_profile[].risk_modifiers — 빈 리스트 or 적절한 modifier
  4. ae_profile[].cumulative     — 축적 독성 여부
  5. ae_profile[].reversible     — 가역성 여부
  6. disease_baseline.tumor_sites / n_target_lesions / sum_of_diameters_mm
  7. comorbidities[].conditional_modifiers  — null → 적절한 값
"""

import json
import copy
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# 1. AE CATEGORY KNOWLEDGE BASE
#    각 AE 카테고리별 onset/duration/cumulative/reversible 기본값
#    onset/duration은 21-day cycle 기반 cytotoxic chemo 기준
# ═══════════════════════════════════════════════════════════════

AE_DEFAULTS = {
    # --- Myelosuppression ---
    "neutropenia": {
        "onset": {"distribution": "normal", "params": {"mean": 12, "std": 5, "min": 7, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 8, "std": 4, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "neutrophil_count_decreased": {  # alias
        "onset": {"distribution": "normal", "params": {"mean": 12, "std": 5, "min": 7, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 8, "std": 4, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "leukopenia": {
        "onset": {"distribution": "normal", "params": {"mean": 12, "std": 5, "min": 7, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 8, "std": 4, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "white_blood_cell_count_decreased": {
        "onset": {"distribution": "normal", "params": {"mean": 12, "std": 5, "min": 7, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 8, "std": 4, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "anaemia": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 90}},
        "duration": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
        "cumulative": True, "reversible": True,
    },
    "haemoglobin_decreased": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 90}},
        "duration": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
        "cumulative": True, "reversible": True,
    },
    "thrombocytopenia": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 7, "max": 35}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 28}},
        "cumulative": True, "reversible": True,
    },
    "platelet_count_decreased": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 7, "max": 35}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 28}},
        "cumulative": True, "reversible": True,
    },
    "lymphopenia": {
        "onset": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 5, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 10, "min": 7, "max": 60}},
        "cumulative": True, "reversible": True,
    },
    "haematologic_other": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 7, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 5, "max": 42}},
        "cumulative": False, "reversible": True,
    },

    # --- GI Toxicity ---
    "nausea": {
        "onset": {"distribution": "normal", "params": {"mean": 3, "std": 3, "min": 1, "max": 14}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 1, "max": 14}},
        "cumulative": False, "reversible": True,
    },
    "vomiting": {
        "onset": {"distribution": "normal", "params": {"mean": 2, "std": 2, "min": 1, "max": 10}},
        "duration": {"distribution": "normal", "params": {"mean": 4, "std": 3, "min": 1, "max": 10}},
        "cumulative": False, "reversible": True,
    },
    "diarrhoea": {
        "onset": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 2, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "constipation": {
        "onset": {"distribution": "normal", "params": {"mean": 5, "std": 4, "min": 1, "max": 21}},
        "duration": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 2, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "stomatitis": {
        "onset": {"distribution": "normal", "params": {"mean": 7, "std": 4, "min": 3, "max": 21}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 28}},
        "cumulative": False, "reversible": True,
    },
    "mucosal_inflammation": {
        "onset": {"distribution": "normal", "params": {"mean": 7, "std": 4, "min": 3, "max": 21}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 28}},
        "cumulative": False, "reversible": True,
    },
    "anorexia": {
        "onset": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 5, "max": 60}},
        "cumulative": True, "reversible": True,
    },
    "decreased_appetite": {
        "onset": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 5, "max": 60}},
        "cumulative": True, "reversible": True,
    },
    "gastrointestinal_other": {
        "onset": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 2, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 3, "max": 30}},
        "cumulative": False, "reversible": True,
    },
    "dyspepsia": {
        "onset": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 2, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "abdominal_pain": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 2, "max": 30}},
        "cumulative": False, "reversible": True,
    },
    "abdominal_pain_upper": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 2, "max": 30}},
        "cumulative": False, "reversible": True,
    },
    "oropharyngeal_pain": {
        "onset": {"distribution": "normal", "params": {"mean": 7, "std": 4, "min": 3, "max": 21}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },

    # --- Neurotoxicity (Paclitaxel/Cisplatin: cumulative, dose-dependent) ---
    "peripheral_sensory_neuropathy": {
        "onset": {"distribution": "lognormal", "params": {"mean": 60, "std": 30, "min": 21, "max": 180}},
        "duration": {"distribution": "normal", "params": {"mean": 90, "std": 45, "min": 21, "max": 365}},
        "cumulative": True, "reversible": True,  # partially reversible
    },
    "peripheral_motor_neuropathy": {
        "onset": {"distribution": "lognormal", "params": {"mean": 75, "std": 35, "min": 28, "max": 180}},
        "duration": {"distribution": "normal", "params": {"mean": 120, "std": 60, "min": 30, "max": 365}},
        "cumulative": True, "reversible": True,
    },
    "neuropathy_peripheral": {
        "onset": {"distribution": "lognormal", "params": {"mean": 60, "std": 30, "min": 21, "max": 180}},
        "duration": {"distribution": "normal", "params": {"mean": 90, "std": 45, "min": 21, "max": 365}},
        "cumulative": True, "reversible": True,
    },
    "neurologic_other": {
        "onset": {"distribution": "normal", "params": {"mean": 42, "std": 21, "min": 14, "max": 120}},
        "duration": {"distribution": "normal", "params": {"mean": 30, "std": 20, "min": 7, "max": 90}},
        "cumulative": False, "reversible": True,
    },
    "paraesthesia": {
        "onset": {"distribution": "lognormal", "params": {"mean": 50, "std": 25, "min": 14, "max": 150}},
        "duration": {"distribution": "normal", "params": {"mean": 60, "std": 30, "min": 14, "max": 180}},
        "cumulative": True, "reversible": True,
    },
    "dysgeusia": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },
    "confusional_state": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 2, "max": 21}},
        "cumulative": False, "reversible": True,
    },

    # --- Constitutional ---
    "fatigue": {
        "onset": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 30, "std": 21, "min": 7, "max": 90}},
        "cumulative": True, "reversible": True,
    },
    "asthenia": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
        "cumulative": True, "reversible": True,
    },
    "flu_like_symptoms": {
        "onset": {"distribution": "normal", "params": {"mean": 2, "std": 2, "min": 1, "max": 7}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 1, "max": 10}},
        "cumulative": False, "reversible": True,
    },
    "pyrexia": {
        "onset": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 3, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 1, "max": 14}},
        "cumulative": False, "reversible": True,
    },
    "weight_decreased": {
        "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
        "duration": {"distribution": "normal", "params": {"mean": 60, "std": 30, "min": 14, "max": 180}},
        "cumulative": True, "reversible": True,
    },
    "dehydration": {
        "onset": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 2, "max": 30}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 1, "max": 14}},
        "cumulative": False, "reversible": True,
    },
    "insomnia": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 30, "std": 21, "min": 7, "max": 90}},
        "cumulative": False, "reversible": True,
    },
    "anxiety": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 30, "std": 21, "min": 7, "max": 90}},
        "cumulative": False, "reversible": True,
    },
    "depression": {
        "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
        "duration": {"distribution": "normal", "params": {"mean": 60, "std": 30, "min": 14, "max": 180}},
        "cumulative": False, "reversible": True,
    },

    # --- Alopecia (Paclitaxel-containing: almost universal) ---
    "alopecia": {
        "onset": {"distribution": "normal", "params": {"mean": 18, "std": 7, "min": 10, "max": 35}},
        "duration": {"distribution": "normal", "params": {"mean": 120, "std": 60, "min": 60, "max": 365}},
        "cumulative": False, "reversible": True,
    },

    # --- Respiratory ---
    "dyspnoea": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": True, "reversible": True,
    },
    "cough": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },
    "pulmonary_other": {
        "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },
    "haemoptysis": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 1, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "pneumonia": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 5, "max": 30}},
        "cumulative": False, "reversible": True,
    },
    "upper_respiratory_tract_infection": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },

    # --- Renal/Electrolyte (Cisplatin: nephrotoxic, Mg/K wasting) ---
    "blood_creatinine_increased": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 5, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 5, "max": 60}},
        "cumulative": True, "reversible": True,
    },
    "renal_other": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 5, "max": 42}},
        "cumulative": True, "reversible": True,
    },
    "hypomagnesaemia": {  # cisplatin-induced renal Mg wasting
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 5, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 5, "max": 42}},
        "cumulative": True, "reversible": True,
    },
    "hypokalaemia": {
        "onset": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 3, "max": 28}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "hyperkalaemia": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 5, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 2, "max": 21}},
        "cumulative": False, "reversible": True,
    },
    "hyponatraemia": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 5, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 3, "max": 28}},
        "cumulative": False, "reversible": True,
    },

    # --- Hepatic ---
    "transaminases_increased": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },
    "alanine_aminotransferase_increased": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },
    "aspartate_aminotransferase_increased": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },
    "hepatic_other": {
        "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },
    "alkaline_phosphatase_increased": {
        "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
        "cumulative": True, "reversible": True,
    },
    "blood_bilirubin_increased": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 5, "max": 42}},
        "cumulative": False, "reversible": True,
    },
    "blood_alkaline_phosphatase_increased": {
        "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
        "cumulative": True, "reversible": True,
    },
    "blood_lactate_dehydrogenase_increased": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },

    # --- Cardiac ---
    "cardiac_arrhythmia": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 7, "min": 2, "max": 30}},
        "cumulative": False, "reversible": True,
    },
    "cardiac_ischemia": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "cumulative": False, "reversible": True,
    },
    "cardiac_other": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 5, "max": 42}},
        "cumulative": False, "reversible": True,
    },

    # --- Musculoskeletal (Paclitaxel: acute myalgia/arthralgia D2-5) ---
    "myalgia_arthralgia": {
        "onset": {"distribution": "normal", "params": {"mean": 3, "std": 2, "min": 1, "max": 7}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 2, "max": 14}},
        "cumulative": False, "reversible": True,
    },
    "arthralgia": {
        "onset": {"distribution": "normal", "params": {"mean": 3, "std": 2, "min": 1, "max": 7}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 2, "max": 14}},
        "cumulative": False, "reversible": True,
    },
    "myalgia": {
        "onset": {"distribution": "normal", "params": {"mean": 3, "std": 2, "min": 1, "max": 7}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 2, "max": 14}},
        "cumulative": False, "reversible": True,
    },
    "back_pain": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "cumulative": False, "reversible": True,
    },
    "bone_pain": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "cumulative": False, "reversible": True,
    },
    "pain": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "cumulative": False, "reversible": True,
    },
    "pain_in_extremity": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "cumulative": False, "reversible": True,
    },
    "musculoskeletal_pain": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "cumulative": False, "reversible": True,
    },
    "musculoskeletal_chest_pain": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "cumulative": False, "reversible": True,
    },

    # --- Dermatologic ---
    "rash": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 5, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },
    "pruritus": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 5, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 5, "max": 42}},
        "cumulative": False, "reversible": True,
    },

    # --- Hypersensitivity (during/right after infusion) ---
    "hypersensitivity": {
        "onset": {"distribution": "normal", "params": {"mean": 1, "std": 1, "min": 1, "max": 3}},
        "duration": {"distribution": "normal", "params": {"mean": 2, "std": 1, "min": 1, "max": 5}},
        "cumulative": False, "reversible": True,
    },
    "allergic_reaction": {
        "onset": {"distribution": "normal", "params": {"mean": 1, "std": 1, "min": 1, "max": 3}},
        "duration": {"distribution": "normal", "params": {"mean": 2, "std": 1, "min": 1, "max": 5}},
        "cumulative": False, "reversible": True,
    },

    # --- Ototoxicity (Cisplatin-specific, cumulative, often irreversible) ---
    "hearing_impaired": {
        "onset": {"distribution": "lognormal", "params": {"mean": 60, "std": 30, "min": 21, "max": 180}},
        "duration": {"distribution": "normal", "params": {"mean": 365, "std": 90, "min": 90, "max": 730}},
        "cumulative": True, "reversible": False,
    },

    # --- Bevacizumab-specific ---
    "hypertension": {
        "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
        "duration": {"distribution": "normal", "params": {"mean": 60, "std": 30, "min": 14, "max": 180}},
        "cumulative": False, "reversible": True,
    },
    "proteinuria": {
        "onset": {"distribution": "normal", "params": {"mean": 42, "std": 21, "min": 14, "max": 120}},
        "duration": {"distribution": "normal", "params": {"mean": 60, "std": 30, "min": 14, "max": 180}},
        "cumulative": True, "reversible": True,
    },
    "epistaxis": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 1, "max": 21}},
        "cumulative": False, "reversible": True,
    },

    # --- Metabolic ---
    "hyperglycaemia": {
        "onset": {"distribution": "normal", "params": {"mean": 14, "std": 10, "min": 3, "max": 42}},
        "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "cumulative": False, "reversible": True,
    },

    # --- Other/Edema ---
    "oedema_peripheral": {
        "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 30, "std": 21, "min": 7, "max": 90}},
        "cumulative": True, "reversible": True,
    },
    "headache": {
        "onset": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 1, "max": 21}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 1, "max": 14}},
        "cumulative": False, "reversible": True,
    },
    "dizziness": {
        "onset": {"distribution": "normal", "params": {"mean": 7, "std": 5, "min": 1, "max": 21}},
        "duration": {"distribution": "normal", "params": {"mean": 5, "std": 3, "min": 1, "max": 14}},
        "cumulative": False, "reversible": True,
    },
    "hypotension": {
        "onset": {"distribution": "normal", "params": {"mean": 3, "std": 3, "min": 1, "max": 14}},
        "duration": {"distribution": "normal", "params": {"mean": 3, "std": 2, "min": 1, "max": 10}},
        "cumulative": False, "reversible": True,
    },
    "urinary_tract_infection": {
        "onset": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
        "duration": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3, "max": 21}},
        "cumulative": False, "reversible": True,
    },
}

# Fallback for any AE not in the database
GENERIC_DEFAULTS = {
    "onset": {"distribution": "normal", "params": {"mean": 28, "std": 14, "min": 7, "max": 90}},
    "duration": {"distribution": "normal", "params": {"mean": 21, "std": 14, "min": 7, "max": 60}},
    "cumulative": False, "reversible": True,
}


# ═══════════════════════════════════════════════════════════════
# 2. DISEASE BASELINE BY INDICATION
# ═══════════════════════════════════════════════════════════════

DISEASE_BASELINES = {
    "ES-SCLC": {
        "tumor_sites": {
            "lung": 0.90,
            "liver": 0.55,
            "lymph_node": 0.65,
            "brain": 0.30,
            "bone": 0.25,
            "adrenal": 0.20,
        },
        "n_target_lesions": {
            "type": "numeric",
            "distribution": "normal",
            "params": {"mean": 4.0, "std": 1.5, "min": 2, "max": 6},
        },
        "sum_of_diameters_mm": {
            "type": "numeric",
            "distribution": "lognormal",
            "params": {"mean": 100, "std": 60, "min": 30, "max": 400},
        },
    },
    "NSCLC": {
        "tumor_sites": {
            "lung": 0.95,
            "lymph_node": 0.55,
            "bone": 0.30,
            "brain": 0.20,
            "liver": 0.20,
            "adrenal": 0.15,
            "pleura": 0.10,
        },
        "n_target_lesions": {
            "type": "numeric",
            "distribution": "normal",
            "params": {"mean": 3.5, "std": 1.5, "min": 1, "max": 5},
        },
        "sum_of_diameters_mm": {
            "type": "numeric",
            "distribution": "lognormal",
            "params": {"mean": 80, "std": 50, "min": 20, "max": 300},
        },
    },
}


# ═══════════════════════════════════════════════════════════════
# 3. CONDITIONAL MODIFIERS BY COMORBIDITY
# ═══════════════════════════════════════════════════════════════

CONDITIONAL_MODIFIERS = {
    "hypertension": [
        {"if_condition": "age > 70", "multiplier": 1.3},
    ],
    "diabetes": [
        {"if_condition": "bmi > 30", "multiplier": 1.5},
    ],
    "cardiac_disease": [
        {"if_condition": "age > 65", "multiplier": 1.4},
    ],
    "copd": [
        {"if_condition": "smoking_history", "multiplier": 1.5},
    ],
    "copd_chronic_respiratory": [
        {"if_condition": "smoking_history", "multiplier": 1.5},
    ],
    "smoking_history": [],
    "hypothyroidism": [],
    "hyperlipidemia": [
        {"if_condition": "diabetes", "multiplier": 1.3},
    ],
    "gastrointestinal_disease": [],
    "hepatobiliary_disease": [],
    "anaemia_preexisting": [
        {"if_condition": "age > 70", "multiplier": 1.2},
    ],
    "drug_hypersensitivity": [],
}


# ═══════════════════════════════════════════════════════════════
# 4. FILE PROCESSING
# ═══════════════════════════════════════════════════════════════

FILES = {
    "3": {
        "path": "data/rule_sets/3_CALGB9732_Paclitaxel_Cisplatin_Etoposide.json",
        "indication_key": "ES-SCLC",
    },
    "4": {
        "path": "data/rule_sets/4_Carboplatin_Etoposide.json",
        "indication_key": "ES-SCLC",
    },
    "6": {
        "path": "data/rule_sets/6_Paclitaxel_Carboplatin_Bevacizumab.json",
        "indication_key": "NSCLC",
    },
    "7": {
        "path": "data/rule_sets/7_Paclitaxel_Carboplatin.json",
        "indication_key": "NSCLC",
    },
    "8": {
        "path": "data/rule_sets/8_Gemcitabine_Cisplatin.json",
        "indication_key": "NSCLC",
    },
}


def fill_ae_fields(ae_entry):
    """AE 엔트리의 누락 필드를 의학적 기본값으로 채운다."""
    term = ae_entry["ae_term"]
    defaults = AE_DEFAULTS.get(term, GENERIC_DEFAULTS)

    if ae_entry.get("onset_day") is None:
        ae_entry["onset_day"] = copy.deepcopy(defaults["onset"])

    if ae_entry.get("duration_days") is None:
        ae_entry["duration_days"] = copy.deepcopy(defaults["duration"])

    if ae_entry.get("risk_modifiers") is None:
        ae_entry["risk_modifiers"] = []

    if "cumulative" not in ae_entry or ae_entry.get("cumulative") is None:
        ae_entry["cumulative"] = defaults.get("cumulative", GENERIC_DEFAULTS["cumulative"])

    if "reversible" not in ae_entry or ae_entry.get("reversible") is None:
        ae_entry["reversible"] = defaults.get("reversible", GENERIC_DEFAULTS["reversible"])

    return ae_entry


def fill_disease_baseline(db, indication_key):
    """disease_baseline의 null 필드를 indication에 맞는 값으로 채운다."""
    template = DISEASE_BASELINES.get(indication_key, DISEASE_BASELINES["NSCLC"])

    if db.get("tumor_sites") is None:
        db["tumor_sites"] = copy.deepcopy(template["tumor_sites"])

    if db.get("n_target_lesions") is None:
        db["n_target_lesions"] = copy.deepcopy(template["n_target_lesions"])

    if db.get("sum_of_diameters_mm") is None:
        db["sum_of_diameters_mm"] = copy.deepcopy(template["sum_of_diameters_mm"])

    return db


def fill_conditional_modifiers(comorbidities):
    """comorbidities의 null conditional_modifiers를 채운다."""
    for c in comorbidities:
        if c.get("conditional_modifiers") is None:
            cond = c.get("condition", "")
            c["conditional_modifiers"] = copy.deepcopy(
                CONDITIONAL_MODIFIERS.get(cond, [])
            )
    return comorbidities


def process_file(key, info):
    path = Path(info["path"])
    data = json.loads(path.read_text())
    drug = data["drug_name"]

    print(f"\n{'='*60}")
    print(f"  [{key}] {drug}")
    print(f"{'='*60}")

    changes = []

    # Fill AE fields
    ae_list = data.get("ae_profile", [])
    for ae in ae_list:
        was_null_onset = ae.get("onset_day") is None
        was_null_dur = ae.get("duration_days") is None
        had_no_cumul = "cumulative" not in ae
        fill_ae_fields(ae)
        if was_null_onset:
            changes.append(f"  AE '{ae['ae_term']}': filled onset_day")
        if was_null_dur:
            changes.append(f"  AE '{ae['ae_term']}': filled duration_days")
        if had_no_cumul:
            changes.append(f"  AE '{ae['ae_term']}': added cumulative={ae['cumulative']}, reversible={ae['reversible']}")

    # Fill disease_baseline
    db = data.get("disease_baseline", {})
    before_ts = db.get("tumor_sites") is None
    fill_disease_baseline(db, info["indication_key"])
    if before_ts:
        changes.append("  disease_baseline: filled tumor_sites, n_target_lesions, sum_of_diameters_mm")

    # Fill conditional_modifiers
    combs = data.get("comorbidities", [])
    null_cm_before = [c["condition"] for c in combs if c.get("conditional_modifiers") is None]
    fill_conditional_modifiers(combs)
    if null_cm_before:
        changes.append(f"  comorbidities: filled conditional_modifiers for {null_cm_before}")

    # Write back
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    for c in changes:
        print(c)
    print(f"  → Saved ({len(changes)} change groups)")

    return len(changes)


if __name__ == "__main__":
    total = 0
    for key, info in FILES.items():
        total += process_file(key, info)
    print(f"\n{'='*60}")
    print(f"  DONE: {total} total change groups across {len(FILES)} files")
    print(f"{'='*60}")
