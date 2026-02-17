"""CRF 필드 파생 로직 단위 테스트

DailySimulator의 CRF 파생 메서드를 LLM 호출 없이 검증한다.
- AE CDISC 필드 (AESEV, AESER, AEREL, AEACN, AEOUT)
- EC 레코드 (투약 기록)
- CM 레코드 (병용약)
- Vitals mean-reversion
- Subjective variation
- Dose modification logic
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.sampler import Sampler

# ══════════════════════════════════════════════════════
# 테스트용 mock 데이터
# ══════════════════════════════════════════════════════

MOCK_RULE_SET = {
    "drug_name": "TestDrug + CompanionDrug",
    "indication": "test indication",
    "trial_design": {
        "cycle_length_days": 21,
        "planned_duration_days": 126,
        "administration_route": "IV",
    },
    "administration_schedule": [
        {
            "drug_name": "TestDrug",
            "dose_value": 1.25,
            "dose_unit": "mg/kg",
            "route": "INTRAVENOUS",
            "cycle_days": [1, 8],
        },
        {
            "drug_name": "CompanionDrug",
            "dose_value": 200,
            "dose_unit": "mg",
            "route": "INTRAVENOUS",
            "cycle_days": [1],
        },
    ],
    "dose_modification_rules": [
        {
            "ae_term": "default",
            "grade_actions": {
                "1": "DOSE NOT CHANGED",
                "2": "DOSE NOT CHANGED",
                "3": "DRUG INTERRUPTED",
                "4": "DRUG WITHDRAWN",
            },
            "dose_reduction_levels": [1.0, 0.75, 0.5],
            "rechallenge_criteria": "resolves to Grade ≤ 1",
        },
        {
            "ae_term": "skin_rash",
            "grade_actions": {
                "1": "DOSE NOT CHANGED",
                "2": "DOSE REDUCED",
                "3": "DRUG INTERRUPTED",
                "4": "DRUG WITHDRAWN",
            },
            "dose_reduction_levels": [1.0, 0.75, 0.5],
            "rechallenge_criteria": "resolves to Grade ≤ 1",
        },
    ],
    "supportive_care_rules": [
        {
            "ae_term": "nausea",
            "treatments": [
                {"drug": "ondansetron", "dose": "8 mg", "unit": "mg",
                 "route": "ORAL", "frequency": "BID", "probability": 0.8},
                {"drug": "metoclopramide", "dose": "10 mg", "unit": "mg",
                 "route": "ORAL", "frequency": "TID", "probability": 0.2},
            ],
        },
        {
            "ae_term": "neutropenia",
            "treatments": [
                {"drug": "filgrastim", "dose": "5 mcg/kg", "unit": "mcg/kg",
                 "route": "SUBCUTANEOUS", "frequency": "QD", "probability": 1.0},
            ],
        },
    ],
    "ae_profile": [
        {
            "ae_term": "nausea",
            "incidence_all_grade": 0.5,
            "incidence_grade3plus": 0.05,
            "grade_distribution": {"1": 0.4, "2": 0.4, "3": 0.15, "4": 0.05, "5": 0.0},
            "onset_day": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 1, "max": 60}},
            "duration_days": {"distribution": "normal", "params": {"mean": 14, "std": 7, "min": 3}},
            "risk_modifiers": [],
            "detection_modality": "patient_report",
            "mechanism": "direct",
            "cumulative": False,
            "reversible": True,
        },
        {
            "ae_term": "skin_rash",
            "incidence_all_grade": 0.4,
            "incidence_grade3plus": 0.1,
            "grade_distribution": {"1": 0.3, "2": 0.4, "3": 0.2, "4": 0.1, "5": 0.0},
            "onset_day": {"distribution": "normal", "params": {"mean": 30, "std": 14, "min": 7, "max": 90}},
            "duration_days": {"distribution": "normal", "params": {"mean": 21, "std": 10, "min": 7}},
            "risk_modifiers": [],
            "detection_modality": "visual",
            "mechanism": "immune-mediated",
            "cumulative": False,
            "reversible": True,
        },
        {
            "ae_term": "neutropenia",
            "incidence_all_grade": 0.3,
            "incidence_grade3plus": 0.15,
            "grade_distribution": {"1": 0.2, "2": 0.3, "3": 0.3, "4": 0.15, "5": 0.05},
            "onset_day": {"distribution": "normal", "params": {"mean": 21, "std": 10, "min": 7, "max": 60}},
            "duration_days": {"distribution": "normal", "params": {"mean": 10, "std": 5, "min": 3}},
            "risk_modifiers": [],
            "detection_modality": "lab",
            "mechanism": "myelosuppression",
            "cumulative": True,
            "reversible": True,
        },
    ],
    "disease_baseline": {
        "tumor_response_distribution": {"CR": 0.1, "PR": 0.4, "SD": 0.3, "PD": 0.2},
    },
    "efficacy": {},
    "lab_reference_ranges": {},
    # ── 복합 위험 모델 (필수) ──
    "mortality_model": {
        "disease_progression": {
            "base_daily_hazard": 0.0005,
            "pd_multiplier": 3.0,
            "response_benefit": 0.5,
            "response_lag_days": 28,
        },
        "treatment_toxicity": {
            "base_daily_hazard": 0.0001,
            "grade4_multiplier": 5.0,
            "grade5_is_fatal": True,
        },
    },
    "ecog_model": {
        "ae_burden_weight": 0.15,
        "disease_weight": 0.3,
        "response_lag_days": 21,
        "response_benefit": -0.3,
        "comorbidity_penalty": 0.1,
    },
    "ae_cascade_rules": [],
    "disposition_model": {
        "consent_withdrawal": {
            "base_daily_rate": 0.001,
            "risk_factors": {
                "severe_ae_active": 2.0,
                "ecog_worsened": 2.0,
                "treatment_weeks_gt_12": 1.3,
                "no_response": 1.5,
            },
        },
        "physician_decision": {
            "base_daily_rate": 0.0005,
            "risk_factors": {
                "ecog_ge_3": 3.0,
                "dose_reductions_ge_2": 2.0,
                "pd_confirmed": 5.0,
                "grade4_ae": 3.0,
            },
        },
        "background_daily_rate": 0.0002,
    },
}

MOCK_PATIENT = {
    "patient_id": "PT-TEST",
    "emr": {
        "demographics": {"age": 65, "sex": "M", "race": "White", "smoking": "former",
                         "ecog_ps": "1", "bmi": 27.0},
        "diagnosis": {"primary": "test indication", "stage": "IV"},
        "medical_history": [
            {"condition": "Hypertension", "ongoing": True, "medication": "ACE inhibitors"},
        ],
        "baseline_ecog": 1,
        "baseline_labs": {},
        "baseline_vitals": {"SBP": 140, "DBP": 85, "HR": 72, "BT": 36.8,
                           "RR": 16, "SpO2": 97, "weight_kg": 80.0},
        "baseline_tumor": {"target_lesions": [{"site": "lung", "size_mm": 30}],
                          "sum_of_diameters_mm": 30},
    },
    "persona": {"type": "stoic", "description": "Test patient."},
    "initial_state": {"location": "HOME", "treatment_status": "screening"},
}


# ══════════════════════════════════════════════════════
# 테스트 유틸리티 — LLM 호출 우회
# ══════════════════════════════════════════════════════

def create_test_simulator():
    """LLM 호출 없이 DailySimulator를 생성한다."""
    # DailySimulator.__init__에서 LLM을 호출하므로 우회
    from src.agents.daily_agent import DailySimulator
    from unittest.mock import patch

    # estimate_probabilities를 mock — 원래 incidence 그대로 반환
    def mock_estimate(context, question, schema, model):
        ae_profile = MOCK_RULE_SET.get("ae_profile", [])
        return {
            "adjusted_ae_risks": [
                {"ae_term": ae["ae_term"],
                 "adjusted_incidence": ae["incidence_all_grade"],
                 "reasoning": "test"}
                for ae in ae_profile
            ]
        }

    with patch("src.agents.daily_agent.estimate_probabilities", side_effect=mock_estimate):
        sim = DailySimulator(
            rule_set=MOCK_RULE_SET,
            patient=MOCK_PATIENT,
            sampler=Sampler(seed=42),
        )
    return sim


# ══════════════════════════════════════════════════════
# 1. AE CRF 필드 파생 테스트
# ══════════════════════════════════════════════════════

def test_ae_crf_fields():
    print("=" * 60)
    print("1. AE CRF 필드 파생 테스트")
    print("=" * 60)
    passed = 0

    sim = create_test_simulator()

    # Grade 1 nausea — known AE
    fields = sim._derive_ae_crf_fields("nausea", 1, 14, "active_stable", 20)
    assert fields["AESEV"] == "MILD", f"Grade 1 → MILD, got {fields['AESEV']}"
    assert fields["AESER"] is False, "Grade 1 should not be serious"
    assert fields["AEREL"] is True, "nausea is in ae_profile → related"
    assert fields["AEACN"] == "DOSE NOT CHANGED", f"Grade 1 default → no change, got {fields['AEACN']}"
    assert fields["AEOUT"] == "NOT RECOVERED/NOT RESOLVED"
    print(f"  ✓ Grade 1 known AE: MILD, not serious, related, no dose change")
    passed += 1

    # Grade 2 skin_rash — has special rule (DOSE REDUCED)
    fields = sim._derive_ae_crf_fields("skin_rash", 2, 30, "active_worsening", 35)
    assert fields["AESEV"] == "MODERATE"
    assert fields["AESER"] is False
    assert fields["AEREL"] is True
    assert fields["AEACN"] == "DOSE REDUCED", f"skin_rash G2 → DOSE REDUCED, got {fields['AEACN']}"
    print(f"  ✓ Grade 2 skin_rash: MODERATE, DOSE REDUCED (specific rule)")
    passed += 1

    # Grade 3 neutropenia — SAE
    fields = sim._derive_ae_crf_fields("neutropenia", 3, 21, "active_worsening", 25)
    assert fields["AESEV"] == "SEVERE"
    assert fields["AESER"] is True, "Grade 3 should be serious"
    assert fields["AESHOSP"] is True
    assert fields["AEACN"] == "DRUG INTERRUPTED"
    print(f"  ✓ Grade 3 neutropenia: SEVERE, SAE, hospitalized, drug interrupted")
    passed += 1

    # Grade 4 — life-threatening
    fields = sim._derive_ae_crf_fields("neutropenia", 4, 21, "active_worsening", 25)
    assert fields["AESLIFE"] is True
    assert fields["AESER"] is True
    assert fields["AEACN"] == "DRUG WITHDRAWN"
    print(f"  ✓ Grade 4: life-threatening, drug withdrawn")
    passed += 1

    # Grade 5 — fatal
    fields = sim._derive_ae_crf_fields("neutropenia", 5, 21, "active_worsening", 25)
    assert fields["AESDTH"] is True
    assert fields["AEOUT"] == "FATAL"
    print(f"  ✓ Grade 5: fatal, AEOUT=FATAL")
    passed += 1

    # Resolved AE
    fields = sim._derive_ae_crf_fields("nausea", 2, 14, "resolved", 28)
    assert fields["AEOUT"] == "RECOVERED/RESOLVED"
    print(f"  ✓ Resolved AE: AEOUT=RECOVERED/RESOLVED")
    passed += 1

    # Improving AE
    fields = sim._derive_ae_crf_fields("nausea", 2, 14, "active_improving", 22)
    assert fields["AEOUT"] == "RECOVERING/RESOLVING"
    print(f"  ✓ Improving AE: AEOUT=RECOVERING/RESOLVING")
    passed += 1

    # Unknown AE (not in ae_profile) — not related
    fields = sim._derive_ae_crf_fields("unknown_ae", 1, 14, "active_stable", 20)
    assert fields["AEREL"] is False, "Unknown AE should be not related"
    print(f"  ✓ Unknown AE: AEREL=False (not in drug profile)")
    passed += 1

    print(f"\n  {passed} passed\n")
    return passed


# ══════════════════════════════════════════════════════
# 2. EC (Exposure) 레코드 테스트
# ══════════════════════════════════════════════════════

def test_exposure_records():
    print("=" * 60)
    print("2. EC (Exposure) 레코드 테스트")
    print("=" * 60)
    passed = 0

    sim = create_test_simulator()

    # Day 1 (cycle day 1) — 두 약 모두 투여
    records = sim._derive_exposure_records(day=1, cycle_day=1, weight_kg=80.0)
    assert len(records) == 2, f"Day 1 should have 2 drugs, got {len(records)}"
    print(f"  ✓ Day 1 (C1D1): {len(records)} drugs administered")

    # TestDrug: 1.25 mg/kg * 80 kg = 100 mg
    td_rec = next(r for r in records if r["drug_name"] == "TestDrug")
    assert td_rec["dose_mg"] == 100.0, f"TestDrug dose: 1.25*80=100, got {td_rec['dose_mg']}"
    assert td_rec["ECROUTE"] == "INTRAVENOUS"
    assert td_rec["ECDOSADJ"] is False
    assert td_rec["cumulative_dose_mg"] == 100.0
    print(f"  ✓ TestDrug: {td_rec['dose_mg']} mg (1.25 mg/kg × 80 kg), cumulative={td_rec['cumulative_dose_mg']}")
    passed += 1

    # CompanionDrug: 200 mg flat dose
    cd_rec = next(r for r in records if r["drug_name"] == "CompanionDrug")
    assert cd_rec["dose_mg"] == 200.0, f"CompanionDrug dose: 200mg, got {cd_rec['dose_mg']}"
    print(f"  ✓ CompanionDrug: {cd_rec['dose_mg']} mg (flat dose), cumulative={cd_rec['cumulative_dose_mg']}")
    passed += 1

    # Day 8 (cycle day 8) — TestDrug만
    records = sim._derive_exposure_records(day=8, cycle_day=8, weight_kg=80.0)
    assert len(records) == 1, f"Day 8 should have 1 drug, got {len(records)}"
    assert records[0]["drug_name"] == "TestDrug"
    assert records[0]["cumulative_dose_mg"] == 200.0  # 100 + 100
    print(f"  ✓ Day 8 (C1D8): TestDrug only, cumulative={records[0]['cumulative_dose_mg']} mg")
    passed += 1

    # Day 15 (cycle day 15) — 투약 없음
    records = sim._derive_exposure_records(day=15, cycle_day=15, weight_kg=80.0)
    assert len(records) == 0, f"Day 15 should have no drugs, got {len(records)}"
    print(f"  ✓ Day 15: No administration")
    passed += 1

    # Dose reduction 테스트
    sim.dose_levels["TestDrug"] = 0.75
    records = sim._derive_exposure_records(day=22, cycle_day=1, weight_kg=80.0)
    td_rec = next(r for r in records if r["drug_name"] == "TestDrug")
    assert td_rec["dose_mg"] == 75.0, f"Reduced dose: 1.25*80*0.75=75, got {td_rec['dose_mg']}"
    assert td_rec["ECDOSADJ"] is True
    print(f"  ✓ Dose reduction: {td_rec['dose_mg']} mg (75% of full), ECDOSADJ=True")
    passed += 1

    # Treatment held 테스트
    sim.treatment_held = True
    sim.hold_reason = "neutropenia Grade 3"
    records = sim._derive_exposure_records(day=29, cycle_day=8, weight_kg=80.0)
    assert len(records) == 1  # 기록은 남되
    assert records[0]["ECTRTCMP"] is False  # 투여 미완료
    assert records[0]["dose_mg"] == 0
    print(f"  ✓ Treatment held: dose=0, ECTRTCMP=False, reason='{records[0]['hold_reason']}'")
    passed += 1

    # Treatment discontinued 테스트
    sim.treatment_discontinued = True
    records = sim._derive_exposure_records(day=36, cycle_day=15, weight_kg=80.0)
    assert len(records) == 0
    print(f"  ✓ Treatment discontinued: no records")
    passed += 1

    print(f"\n  {passed} passed\n")
    return passed


# ══════════════════════════════════════════════════════
# 3. CM (Concomitant Meds) 테스트
# ══════════════════════════════════════════════════════

def test_concomitant_meds():
    print("=" * 60)
    print("3. CM (병용약) 레코드 테스트")
    print("=" * 60)
    passed = 0

    sim = create_test_simulator()

    # nausea onset → CM 생성
    events = [{"type": "ae_onset", "ae": "nausea", "grade": 2}]
    cm = sim._derive_concomitant_meds(events, day=14)
    assert len(cm) == 1, f"Should create 1 CM, got {len(cm)}"
    assert cm[0]["CMTRT"] in ("ondansetron", "metoclopramide")
    assert cm[0]["CMINDC"] == "nausea"
    assert cm[0]["CMONGO"] is True
    print(f"  ✓ Nausea onset → CM: {cm[0]['CMTRT']} ({cm[0]['CMDSTXT']}, {cm[0]['CMROUTE']})")
    passed += 1

    # 동일 AE에 중복 CM 방지
    cm2 = sim._derive_concomitant_meds(events, day=15)
    assert len(cm2) == 0, f"Should not duplicate CM, got {len(cm2)}"
    print(f"  ✓ Duplicate prevention: no new CM for ongoing nausea")
    passed += 1

    # neutropenia onset → filgrastim
    events2 = [{"type": "ae_onset", "ae": "neutropenia", "grade": 3}]
    cm3 = sim._derive_concomitant_meds(events2, day=21)
    assert len(cm3) == 1
    assert cm3[0]["CMTRT"] == "filgrastim"
    assert cm3[0]["CMROUTE"] == "SUBCUTANEOUS"
    print(f"  ✓ Neutropenia → filgrastim (SC, QD)")
    passed += 1

    # AE resolved → CM 종료
    resolve_events = [{"type": "ae_resolved", "ae": "nausea", "days_active": 10}]
    sim._derive_concomitant_meds(resolve_events, day=24)
    nausea_cm = [cm for cm in sim.active_cm if cm["CMINDC"] == "nausea"]
    assert nausea_cm[0]["CMONGO"] is False
    assert nausea_cm[0]["CMENDAT"] == 24
    print(f"  ✓ AE resolved → CM ended (CMONGO=False, CMENDAT=24)")
    passed += 1

    # Unknown AE (no supportive care rule) → no CM
    events3 = [{"type": "ae_onset", "ae": "unknown_rare_ae", "grade": 1}]
    cm4 = sim._derive_concomitant_meds(events3, day=30)
    assert len(cm4) == 0
    print(f"  ✓ Unknown AE → no CM (no supportive care rule)")
    passed += 1

    print(f"\n  {passed} passed\n")
    return passed


# ══════════════════════════════════════════════════════
# 4. Vitals Mean-Reversion 테스트
# ══════════════════════════════════════════════════════

def test_vitals_mean_reversion():
    print("=" * 60)
    print("4. Vitals Mean-Reversion 테스트")
    print("=" * 60)
    passed = 0

    sim = create_test_simulator()

    # 시뮬레이션: baseline SBP=140, 초기값을 160으로 올린 후 10일 반복
    prev_vitals = {"SBP": 160.0, "DBP": 85.0, "HR": 72.0, "BT": 36.8,
                   "RR": 16.0, "SpO2": 97.0, "weight_kg": 80.0}

    sbp_values = [160.0]
    for _ in range(30):
        new_v = sim._perturb_vitals(prev_vitals)
        sbp_values.append(new_v["SBP"])
        prev_vitals = new_v

    # Mean-reversion: 30일 후 baseline(140)에 가까워져야 함
    final_sbp = sbp_values[-1]
    drift_from_start = abs(160.0 - final_sbp)
    drift_from_baseline = abs(140.0 - final_sbp)

    # 30일 후 baseline(140)에 더 가까워야 함
    assert drift_from_baseline < drift_from_start or drift_from_baseline < 15, \
        f"Mean-reversion failed: started at 160, ended at {final_sbp:.1f}, baseline=140"
    print(f"  ✓ SBP mean-reversion: 160 → {final_sbp:.1f} (baseline=140, θ=0.15)")
    passed += 1

    # 이전의 random walk에서는 이런 현상이 없었는지 확인
    # 기준: SBP가 170 이상으로 치솟지 않아야 함
    max_sbp = max(sbp_values)
    assert max_sbp < 175, f"SBP should not drift too high: max={max_sbp:.1f}"
    print(f"  ✓ No excessive drift: max SBP = {max_sbp:.1f} (< 175)")
    passed += 1

    # SpO2는 baseline=97 근처에 유지
    prev_vitals = {"SBP": 140.0, "DBP": 85.0, "HR": 72.0, "BT": 36.8,
                   "RR": 16.0, "SpO2": 93.0, "weight_kg": 80.0}
    for _ in range(20):
        prev_vitals = sim._perturb_vitals(prev_vitals)

    spo2_final = prev_vitals["SpO2"]
    assert spo2_final > 94, f"SpO2 should revert toward 97, got {spo2_final:.1f}"
    print(f"  ✓ SpO2 mean-reversion: 93.0 → {spo2_final:.1f} (baseline=97)")
    passed += 1

    print(f"\n  {passed} passed\n")
    return passed


# ══════════════════════════════════════════════════════
# 5. Dose Modification Logic 테스트
# ══════════════════════════════════════════════════════

def test_dose_modification():
    print("=" * 60)
    print("5. Dose Modification Logic 테스트")
    print("=" * 60)
    passed = 0

    sim = create_test_simulator()

    # Grade 1 → no change
    events = [{"type": "ae_onset", "ae": "nausea", "grade": 1}]
    sim._apply_dose_modifications(events, day=14)
    assert sim.treatment_held is False
    assert sim.treatment_discontinued is False
    print(f"  ✓ Grade 1 AE → no dose modification")
    passed += 1

    # skin_rash Grade 2 → DOSE REDUCED (specific rule)
    events = [{"type": "ae_onset", "ae": "skin_rash", "grade": 2}]
    sim._apply_dose_modifications(events, day=30)
    assert sim.dose_levels["TestDrug"] == 0.75, f"Should reduce to 0.75, got {sim.dose_levels['TestDrug']}"
    print(f"  ✓ skin_rash G2 → dose reduced to 0.75")
    passed += 1

    # Grade 3 → DRUG INTERRUPTED
    events = [{"type": "ae_grade_change", "ae": "neutropenia", "new_grade": 3}]
    sim._apply_dose_modifications(events, day=35)
    assert sim.treatment_held is True
    assert "neutropenia" in sim.hold_reason
    print(f"  ✓ G3 neutropenia → DRUG INTERRUPTED (hold_reason={sim.hold_reason})")
    passed += 1

    # AE resolved → treatment resumed
    events = [{"type": "ae_resolved", "ae": "neutropenia", "days_active": 10}]
    sim._apply_dose_modifications(events, day=45)
    assert sim.treatment_held is False
    print(f"  ✓ AE resolved → treatment resumed")
    passed += 1

    # Grade 4 → DRUG WITHDRAWN
    sim2 = create_test_simulator()
    events = [{"type": "ae_onset", "ae": "neutropenia", "grade": 4}]
    sim2._apply_dose_modifications(events, day=21)
    assert sim2.treatment_discontinued is True
    print(f"  ✓ G4 → DRUG WITHDRAWN (treatment_discontinued=True)")
    passed += 1

    print(f"\n  {passed} passed\n")
    return passed


# ══════════════════════════════════════════════════════
# 6. Subjective Variation 테스트
# ══════════════════════════════════════════════════════

def test_subjective_variation():
    print("=" * 60)
    print("6. Subjective Variation 테스트")
    print("=" * 60)
    passed = 0

    sim = create_test_simulator()

    prev_sub = {
        "overall_awareness": "NOTICED",
        "symptoms_patient_perceives": [
            {
                "symptom": "nausea",
                "awareness": "yes",
                "would_report": "no",
                "verbal_expression": "Patient feels queasy. Not too worried about it.",
            }
        ],
    }
    active_aes = [{"ae": "nausea", "grade": 2, "onset_day": 14, "status": "active_stable"}]

    # 여러 날의 subjective 확인 — 모두 같으면 안 됨
    expressions = set()
    for day in range(15, 25):
        new_sub = sim._vary_subjective(prev_sub, day, active_aes)
        symptoms = new_sub.get("symptoms_patient_perceives", [])
        assert len(symptoms) == 1, f"Should have 1 symptom, got {len(symptoms)}"
        expr = symptoms[0]["verbal_expression"]
        expressions.add(expr)
        assert "days_active" in symptoms[0], "Should have days_active field"

    assert len(expressions) > 1, f"Expressions should vary across days, got {len(expressions)} unique"
    print(f"  ✓ {len(expressions)} unique expressions over 10 days (not identical)")
    passed += 1

    # days_active 계산 확인
    new_sub = sim._vary_subjective(prev_sub, day=20, active_aes=active_aes)
    assert new_sub["symptoms_patient_perceives"][0]["days_active"] == 6  # 20 - 14
    print(f"  ✓ days_active = 6 (day 20 - onset 14)")
    passed += 1

    print(f"\n  {passed} passed\n")
    return passed


# ══════════════════════════════════════════════════════
# 7. Administration Day 판정 테스트
# ══════════════════════════════════════════════════════

def test_administration_day():
    print("=" * 60)
    print("7. Administration Day 판정 테스트")
    print("=" * 60)
    passed = 0

    sim = create_test_simulator()

    assert sim.is_administration_day(1) is True, "Cycle day 1 should be admin day"
    assert sim.is_administration_day(8) is True, "Cycle day 8 should be admin day"
    assert sim.is_administration_day(2) is False, "Cycle day 2 should NOT be admin day"
    assert sim.is_administration_day(15) is False, "Cycle day 15 should NOT be admin day"
    assert sim.is_administration_day(21) is False, "Cycle day 21 should NOT be admin day"
    print(f"  ✓ Admin days: [1, 8] correctly identified")
    passed += 1

    print(f"\n  {passed} passed\n")
    return passed


# ══════════════════════════════════════════════════════
# 8. Drug Tracking (cumulative dose) 테스트
# ══════════════════════════════════════════════════════

def test_drug_tracking():
    print("=" * 60)
    print("8. Drug Tracking (cumulative dose) 테스트")
    print("=" * 60)
    passed = 0

    sim = create_test_simulator()
    obj = {"vitals": {"weight_kg": 80.0}}

    # Cycle 1: Day 1 → 2 drugs, Day 8 → 1 drug
    sim._derive_exposure_records(day=1, cycle_day=1, weight_kg=80.0)
    sim._derive_exposure_records(day=8, cycle_day=8, weight_kg=80.0)

    assert sim.cumulative_doses["TestDrug"] == 200.0  # 100 + 100
    assert sim.cumulative_doses["CompanionDrug"] == 200.0  # 200
    print(f"  ✓ After C1D1+D8: TestDrug={sim.cumulative_doses['TestDrug']}mg, CompanionDrug={sim.cumulative_doses['CompanionDrug']}mg")
    passed += 1

    # Cycle 2: Day 22 → 2 drugs
    sim._derive_exposure_records(day=22, cycle_day=1, weight_kg=80.0)
    assert sim.cumulative_doses["TestDrug"] == 300.0  # 200 + 100
    assert sim.cumulative_doses["CompanionDrug"] == 400.0  # 200 + 200
    print(f"  ✓ After C2D1: TestDrug={sim.cumulative_doses['TestDrug']}mg, CompanionDrug={sim.cumulative_doses['CompanionDrug']}mg")
    passed += 1

    # _update_drug_tracking 테스트 — 실제 약물명을 키로 사용
    sim._update_drug_tracking(obj, day=22, cycle_day=1, ec_records=[])
    assert "TestDrug" in obj, f"Expected 'TestDrug' key, got: {list(obj.keys())}"
    assert obj["TestDrug"]["cumulative_dose_mg"] == 300.0
    assert obj["TestDrug"]["last_administered_day"] == 22
    assert "CompanionDrug" in obj
    assert obj["CompanionDrug"]["cumulative_dose_mg"] == 400.0
    # 레거시 drug_a/drug_b 키가 없어야 함
    assert "drug_a" not in obj, "Legacy drug_a key should be removed"
    assert "drug_b" not in obj, "Legacy drug_b key should be removed"
    print(f"  ✓ Drug tracking by name: correct cumulative doses and last_admin_day")
    passed += 1

    print(f"\n  {passed} passed\n")
    return passed


# ══════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    total = 0
    total += test_ae_crf_fields()
    total += test_exposure_records()
    total += test_concomitant_meds()
    total += test_vitals_mean_reversion()
    total += test_dose_modification()
    total += test_subjective_variation()
    total += test_administration_day()
    total += test_drug_tracking()

    print("=" * 60)
    print(f"결과: {total} passed")
    print("=" * 60)
    if total >= 25:
        print("✓ 모든 테스트 통과!")
    else:
        print("✗ 일부 테스트 실패")
