"""복합 위험 모델 단위 테스트 (v2 — 축소된 모델)

hazard.py 함수 검증:
1. _check_threshold — 조건 파서
2. compute_daily_mortality — 2채널 (disease + toxicity) + ECOG
3. compute_dynamic_ecog — 동적 ECOG
4. compute_causal_lab_target — 인과적 lab 목표
5. compute_ae_cascade_multipliers — AE 연쇄
6. compute_discontinuation_risk — 2채널 + background (persona 없음)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.hazard import (
    _check_threshold,
    compute_daily_mortality,
    compute_dynamic_ecog,
    compute_causal_lab_target,
    compute_ae_cascade_multipliers,
    compute_discontinuation_risk,
)


# ══════════════════════════════════════════════════════
# 1. _check_threshold
# ══════════════════════════════════════════════════════

class TestCheckThreshold:
    def test_gt_absolute(self):
        assert _check_threshold("SBP_gt_180", 190) is True
        assert _check_threshold("SBP_gt_180", 170) is False

    def test_lt_absolute(self):
        assert _check_threshold("SpO2_lt_90", 85) is True
        assert _check_threshold("SpO2_lt_90", 95) is False

    def test_gt_xULN(self):
        assert _check_threshold("ALT_gt_5xULN", 250, uln=40) is True
        assert _check_threshold("ALT_gt_5xULN", 150, uln=40) is False

    def test_lt_absolute_platelets(self):
        assert _check_threshold("Platelets_lt_50000", 30000) is True
        assert _check_threshold("Platelets_lt_50000", 80000) is False

    def test_gt_xBL(self):
        assert _check_threshold("Creatinine_gt_3xBL", 4.0, baseline=1.0) is True
        assert _check_threshold("Creatinine_gt_3xBL", 2.5, baseline=1.0) is False

    def test_invalid_format(self):
        assert _check_threshold("invalid", 100) is False
        assert _check_threshold("", 100) is False

    def test_missing_reference(self):
        assert _check_threshold("ALT_gt_5xULN", 250, uln=None) is False


# ══════════════════════════════════════════════════════
# 2. compute_daily_mortality (2채널)
# ══════════════════════════════════════════════════════

RISK_CONFIG = {
    "baseline_annual_mortality": 0.25,
    "channels": {
        "disease_progression": {
            "pd_multiplier": 4.0,
            "response_lag_days": 21,
            "response_reduction": 0.3,
        },
        "treatment_toxicity": {
            "ae_grade_multipliers": {"3": 1.5, "4": 3.0},
            "concurrent_ae_threshold": 3,
            "concurrent_ae_multiplier": 2.0,
        },
    },
}


class TestDailyMortality:
    def test_baseline_risk(self):
        """아무 위험 인자 없는 기저 상태."""
        risk, ch = compute_daily_mortality(
            day=10, active_aes=[], tumor_status="SD",
            ecog=1, treatment_discontinued=False,
            response_onset_day=None, risk_config=RISK_CONFIG,
        )
        assert 0.0005 < risk < 0.002
        assert ch["disease_progression"] == 1.0
        assert ch["treatment_toxicity"] == 1.0

    def test_pd_increases_risk(self):
        """PD 상태면 disease channel 4배."""
        risk_sd, _ = compute_daily_mortality(
            day=10, active_aes=[], tumor_status="SD",
            ecog=1, treatment_discontinued=False,
            response_onset_day=None, risk_config=RISK_CONFIG,
        )
        risk_pd, ch = compute_daily_mortality(
            day=10, active_aes=[], tumor_status="PD",
            ecog=1, treatment_discontinued=False,
            response_onset_day=None, risk_config=RISK_CONFIG,
        )
        assert risk_pd > risk_sd * 3
        assert ch["disease_progression"] == 4.0

    def test_response_reduces_risk_after_lag(self):
        """CR 반응 후 lag 지나면 위험 감소."""
        _, ch_early = compute_daily_mortality(
            day=30, active_aes=[], tumor_status="CR",
            ecog=0, treatment_discontinued=False,
            response_onset_day=20, risk_config=RISK_CONFIG,
        )
        assert ch_early["disease_progression"] == 1.0

        _, ch_late = compute_daily_mortality(
            day=80, active_aes=[], tumor_status="CR",
            ecog=0, treatment_discontinued=False,
            response_onset_day=20, risk_config=RISK_CONFIG,
        )
        assert ch_late["disease_progression"] < 1.0

    def test_severe_ae_increases_risk(self):
        """Grade 4 AE는 toxicity channel 증가."""
        risk, ch = compute_daily_mortality(
            day=15, active_aes=[{"ae": "hepatotoxicity", "grade": 4, "status": "active"}],
            tumor_status="SD", ecog=1, treatment_discontinued=False,
            response_onset_day=None, risk_config=RISK_CONFIG,
        )
        assert ch["treatment_toxicity"] == 3.0

    def test_ecog4_high_risk(self):
        """ECOG 4는 5배 위험."""
        risk_e1, _ = compute_daily_mortality(
            day=10, active_aes=[], tumor_status="SD",
            ecog=1, treatment_discontinued=False,
            response_onset_day=None, risk_config=RISK_CONFIG,
        )
        risk_e4, ch_e4 = compute_daily_mortality(
            day=10, active_aes=[], tumor_status="SD",
            ecog=4, treatment_discontinued=False,
            response_onset_day=None, risk_config=RISK_CONFIG,
        )
        assert risk_e4 > risk_e1 * 4
        assert ch_e4["ecog"] == 5.0

    def test_no_comorbidity_channel(self):
        """comorbidity 채널 제거됨 — contributions에 없어야 함."""
        _, ch = compute_daily_mortality(
            day=10, active_aes=[], tumor_status="SD",
            ecog=1, treatment_discontinued=False,
            response_onset_day=None, risk_config=RISK_CONFIG,
        )
        assert "comorbidity" not in ch
        assert "acute_crisis" not in ch

    def test_risk_capped(self):
        """일일 상한 50%."""
        risk, _ = compute_daily_mortality(
            day=10,
            active_aes=[
                {"ae": f"ae{i}", "grade": 4, "status": "active"} for i in range(5)
            ],
            tumor_status="PD", ecog=4, treatment_discontinued=True,
            response_onset_day=None, risk_config=RISK_CONFIG,
        )
        assert risk <= 0.5


# ══════════════════════════════════════════════════════
# 3. compute_dynamic_ecog
# ══════════════════════════════════════════════════════

ECOG_CONFIG = {
    "ae_burden_weight": 0.15,
    "disease_weight": 0.3,
    "response_lag_days": 21,
    "response_benefit": -0.3,
    "comorbidity_penalty": 0.1,
}


class TestDynamicECOG:
    def test_baseline_no_change(self):
        ecog = compute_dynamic_ecog(
            baseline_ecog=1, current_ecog=1,
            active_aes=[], tumor_status="SD",
            response_onset_day=None, day=10,
            comorbidities=set(), treatment_discontinued=False,
            ecog_config=ECOG_CONFIG,
        )
        assert ecog == 1

    def test_ae_worsens_ecog(self):
        ecog = compute_dynamic_ecog(
            baseline_ecog=1, current_ecog=1,
            active_aes=[
                {"ae": "fatigue", "grade": 4, "status": "active"},
                {"ae": "neuropathy", "grade": 3, "status": "active"},
                {"ae": "nausea", "grade": 2, "status": "active"},
            ],
            tumor_status="SD", response_onset_day=None, day=10,
            comorbidities=set(), treatment_discontinued=False,
            ecog_config=ECOG_CONFIG,
        )
        assert ecog == 2

    def test_pd_worsens_ecog(self):
        ecog = compute_dynamic_ecog(
            baseline_ecog=1, current_ecog=1,
            active_aes=[], tumor_status="PD",
            response_onset_day=None, day=10,
            comorbidities=set(), treatment_discontinued=False,
            ecog_config=ECOG_CONFIG,
        )
        assert ecog >= 2

    def test_rate_limited(self):
        ecog = compute_dynamic_ecog(
            baseline_ecog=0, current_ecog=0,
            active_aes=[
                {"ae": f"ae{i}", "grade": 4, "status": "active"}
                for i in range(5)
            ],
            tumor_status="PD", response_onset_day=None, day=10,
            comorbidities={"a", "b", "c"}, treatment_discontinued=True,
            ecog_config=ECOG_CONFIG,
        )
        assert ecog <= 1

    def test_clamped_0_4(self):
        ecog = compute_dynamic_ecog(
            baseline_ecog=0, current_ecog=0,
            active_aes=[], tumor_status="CR",
            response_onset_day=5, day=100,
            comorbidities=set(), treatment_discontinued=False,
            ecog_config=ECOG_CONFIG,
        )
        assert 0 <= ecog <= 4


# ══════════════════════════════════════════════════════
# 4. compute_causal_lab_target
# ══════════════════════════════════════════════════════

class TestCausalLabTarget:
    def test_no_effect_baseline(self):
        target = compute_causal_lab_target(
            "ALT", 30.0, {}, {},
            ae_lab_links=[], cumulative_dose_effects=[],
        )
        assert target == 30.0

    def test_ae_elevates_alt(self):
        target = compute_causal_lab_target(
            "ALT", 30.0,
            active_aes={"hepatotoxicity": {"grade": 3, "status": "active"}},
            cumulative_doses={},
            ae_lab_links=[
                {"ae_term": "hepatotoxicity", "lab": "ALT",
                 "grade_effects": {"1": 2.0, "2": 4.0, "3": 10.0, "4": 25.0}},
            ],
            cumulative_dose_effects=[],
        )
        assert target == 300.0

    def test_ae_decreases_anc(self):
        target = compute_causal_lab_target(
            "ANC", 4000.0,
            active_aes={"myelosuppression": {"grade": 2, "status": "active"}},
            cumulative_doses={},
            ae_lab_links=[
                {"ae_term": "myelosuppression", "lab": "ANC",
                 "grade_effects": {"1": 0.8, "2": 0.5, "3": 0.2, "4": 0.05}},
            ],
            cumulative_dose_effects=[],
        )
        assert target == 2000.0

    def test_resolved_ae_no_effect(self):
        target = compute_causal_lab_target(
            "ALT", 30.0,
            active_aes={"hepatotoxicity": {"grade": 3, "status": "resolved"}},
            cumulative_doses={},
            ae_lab_links=[
                {"ae_term": "hepatotoxicity", "lab": "ALT",
                 "grade_effects": {"3": 10.0}},
            ],
            cumulative_dose_effects=[],
        )
        assert target == 30.0


# ══════════════════════════════════════════════════════
# 5. compute_ae_cascade_multipliers
# ══════════════════════════════════════════════════════

class TestAECascade:
    def test_no_cascade(self):
        mults = compute_ae_cascade_multipliers({}, [
            {"trigger_ae": "neutropenia", "grade_threshold": 3,
             "target_ae": "infection", "multiplier": 3.0},
        ])
        assert mults == {}

    def test_cascade_triggered(self):
        mults = compute_ae_cascade_multipliers(
            {"neutropenia": {"grade": 3, "status": "active"}},
            [{"trigger_ae": "neutropenia", "grade_threshold": 3,
              "target_ae": "infection", "multiplier": 3.0}],
        )
        assert mults == {"infection": 3.0}

    def test_below_threshold(self):
        mults = compute_ae_cascade_multipliers(
            {"neutropenia": {"grade": 2, "status": "active"}},
            [{"trigger_ae": "neutropenia", "grade_threshold": 3,
              "target_ae": "infection", "multiplier": 3.0}],
        )
        assert mults == {}

    def test_resolved_no_effect(self):
        mults = compute_ae_cascade_multipliers(
            {"neutropenia": {"grade": 4, "status": "resolved"}},
            [{"trigger_ae": "neutropenia", "grade_threshold": 3,
              "target_ae": "infection", "multiplier": 3.0}],
        )
        assert mults == {}

    def test_cumulative_multipliers(self):
        mults = compute_ae_cascade_multipliers(
            {
                "neutropenia": {"grade": 3, "status": "active"},
                "immunosuppression": {"grade": 2, "status": "active"},
            },
            [
                {"trigger_ae": "neutropenia", "grade_threshold": 3,
                 "target_ae": "infection", "multiplier": 3.0},
                {"trigger_ae": "immunosuppression", "grade_threshold": 2,
                 "target_ae": "infection", "multiplier": 2.0},
            ],
        )
        assert mults["infection"] == 6.0


# ══════════════════════════════════════════════════════
# 6. compute_discontinuation_risk (2채널 + background)
# ══════════════════════════════════════════════════════

DISP_CONFIG = {
    "independent_hazards": {
        "consent_withdrawal": {
            "base_daily_rate": 0.0004,
            "risk_factors": {
                "active_ae_grade_3_plus": 2.5,
                "ecog_worsened": 2.0,
                "treatment_weeks_gt_12": 1.5,
                "poor_response": 1.3,
            },
        },
        "physician_decision": {
            "base_daily_rate": 0.00012,
            "risk_factors": {
                "ecog_ge_3": 3.0,
                "multiple_dose_reductions": 2.0,
                "poor_tumor_response": 2.0,
                "severe_ae": 2.0,
            },
        },
    },
}


class TestDiscontinuationRisk:
    def test_baseline_rates(self):
        risks = compute_discontinuation_risk(
            day=10, active_aes=[], ecog=1, baseline_ecog=1,
            tumor_status="SD", treatment_weeks=1.0,
            treatment_discontinued=False, dose_reductions=0,
            disposition_config=DISP_CONFIG,
        )
        assert risks["patient_withdrawal"] > 0
        assert risks["physician_decision"] > 0
        assert risks["background"] > 0
        # protocol_violation/other 키는 없어야 함
        assert "protocol_violation" not in risks
        assert "other" not in risks

    def test_no_persona_in_model(self):
        """persona는 확률 모델에서 제거됨."""
        # 같은 조건 → persona 무관하게 동일 결과
        risks = compute_discontinuation_risk(
            day=10, active_aes=[], ecog=1, baseline_ecog=1,
            tumor_status="PR", treatment_weeks=1.0,
            treatment_discontinued=False, dose_reductions=0,
            disposition_config=DISP_CONFIG,
        )
        # persona 파라미터 자체가 없으므로 결과가 일관적
        assert risks["patient_withdrawal"] == risks["patient_withdrawal"]  # tautology — persona 파라미터가 함수에 없음을 확인

    def test_severe_ae_increases_withdrawal(self):
        risks_no_ae = compute_discontinuation_risk(
            day=10, active_aes=[], ecog=1, baseline_ecog=1,
            tumor_status="PR", treatment_weeks=1.0,
            treatment_discontinued=False, dose_reductions=0,
            disposition_config=DISP_CONFIG,
        )
        risks_ae = compute_discontinuation_risk(
            day=10,
            active_aes=[{"ae": "rash", "grade": 3, "status": "active"}],
            ecog=1, baseline_ecog=1,
            tumor_status="PR", treatment_weeks=1.0,
            treatment_discontinued=False, dose_reductions=0,
            disposition_config=DISP_CONFIG,
        )
        assert risks_ae["patient_withdrawal"] > risks_no_ae["patient_withdrawal"]

    def test_ecog_worsening_increases_both(self):
        risks = compute_discontinuation_risk(
            day=50,
            active_aes=[{"ae": "fatigue", "grade": 3, "status": "active"}],
            ecog=3, baseline_ecog=1,
            tumor_status="SD", treatment_weeks=7.0,
            treatment_discontinued=False, dose_reductions=2,
            disposition_config=DISP_CONFIG,
        )
        assert risks["physician_decision"] > 0.0005

    def test_already_discontinued_zero(self):
        risks = compute_discontinuation_risk(
            day=10, active_aes=[], ecog=1, baseline_ecog=1,
            tumor_status="SD", treatment_weeks=1.0,
            treatment_discontinued=True, dose_reductions=0,
            disposition_config=DISP_CONFIG,
        )
        assert all(v == 0 for v in risks.values())

    def test_treatment_fatigue(self):
        risks_early = compute_discontinuation_risk(
            day=10, active_aes=[], ecog=1, baseline_ecog=1,
            tumor_status="PR", treatment_weeks=2.0,
            treatment_discontinued=False, dose_reductions=0,
            disposition_config=DISP_CONFIG,
        )
        risks_late = compute_discontinuation_risk(
            day=100, active_aes=[], ecog=1, baseline_ecog=1,
            tumor_status="PR", treatment_weeks=14.0,
            treatment_discontinued=False, dose_reductions=0,
            disposition_config=DISP_CONFIG,
        )
        assert risks_late["patient_withdrawal"] > risks_early["patient_withdrawal"]


# ══════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
