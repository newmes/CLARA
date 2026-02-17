"""hazard.py 단위 테스트 — LLM 호출 없음, 순수 수학 검증

검증 항목:
  1. daily_onset_hazard: 합리적 확률 범위, 분포 형태
  2. Monte Carlo: 1000회 시뮬레이션 시 실제 발생률 ≈ incidence
  3. daily_resolution_hazard: 해소 확률 동작
  4. grade_transition_probs: 확률 합 = 1.0
  5. tumor_change_pct: RECIST 기준 부합
  6. adjust_incidence_by_risk_modifiers: 코드 기반 보정
"""

import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.hazard import (
    daily_onset_hazard,
    daily_resolution_hazard,
    grade_transition_probs,
    tumor_change_pct,
    adjust_incidence_by_risk_modifiers,
)
from src.engine.sampler import Sampler

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} — {detail}")


# ══════════════════════════════════════════════════════
# 1. daily_onset_hazard 기본 검증
# ══════════════════════════════════════════════════════

print("=" * 60)
print("1. daily_onset_hazard — 기본 검증")
print("=" * 60)

# Padcev의 peripheral_neuropathy: incidence 56%, onset Normal(mean=63, std=21, min=7, max=180)
onset_spec = {
    "distribution": "normal",
    "params": {"mean": 63, "std": 21, "min": 7, "max": 180},
}
incidence = 0.56

# (a) 모든 일별 확률이 [0, 1] 범위
hazards = [daily_onset_hazard(d, incidence, onset_spec) for d in range(1, 181)]
check("모든 hazard >= 0", all(h >= 0 for h in hazards))
check("모든 hazard <= 1", all(h <= 1 for h in hazards))

# (b) Day 1 (min 이전)에는 확률이 매우 낮아야 함
h_day1 = daily_onset_hazard(1, incidence, onset_spec)
check(f"Day 1 hazard 매우 낮음 ({h_day1:.6f})", h_day1 < 0.01, f"got {h_day1}")

# (c) mean 근처(Day 63)에서 확률이 가장 높아야 함
h_day63 = daily_onset_hazard(63, incidence, onset_spec)
h_day30 = daily_onset_hazard(30, incidence, onset_spec)
h_day120 = daily_onset_hazard(120, incidence, onset_spec)
check(f"Day 63 hazard > Day 30 ({h_day63:.4f} > {h_day30:.4f})", h_day63 > h_day30)

# (d) incidence 0이면 항상 0
h_zero = daily_onset_hazard(63, 0.0, onset_spec)
check("incidence=0 → hazard=0", h_zero == 0.0)

# (e) 확률 곡선 출력
print(f"\n  Hazard curve (peripheral_neuropathy, I=0.56):")
for d in [1, 7, 14, 21, 30, 42, 56, 63, 70, 84, 100, 120, 150, 180]:
    h = daily_onset_hazard(d, incidence, onset_spec)
    bar = "█" * int(h * 500)
    print(f"    Day {d:3d}: {h:.5f} {bar}")


# ══════════════════════════════════════════════════════
# 2. Monte Carlo — 발생률이 incidence와 일치하는지
# ══════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print("2. Monte Carlo — 발생률 검증 (N=5000, 180일)")
print("=" * 60)

# 여러 AE에 대해 테스트
test_cases = [
    ("peripheral_neuropathy", 0.56, {"distribution": "normal", "params": {"mean": 63, "std": 21, "min": 7, "max": 180}}),
    ("rash", 0.35, {"distribution": "normal", "params": {"mean": 21, "std": 10, "min": 3, "max": 120}}),
    ("hepatitis", 0.10, {"distribution": "normal", "params": {"mean": 42, "std": 14, "min": 14, "max": 150}}),
    ("rare_event", 0.03, {"distribution": "normal", "params": {"mean": 30, "std": 15, "min": 7, "max": 180}}),
]

N_PATIENTS = 5000
DAYS = 180

for ae_name, target_inc, spec in test_cases:
    sampler = Sampler(seed=42)
    occurred = 0

    for _ in range(N_PATIENTS):
        for day in range(1, DAYS + 1):
            h = daily_onset_hazard(day, target_inc, spec)
            if sampler.boolean(h):
                occurred += 1
                break  # 이 환자는 AE 발생함

    actual_rate = occurred / N_PATIENTS
    diff = abs(actual_rate - target_inc)
    tolerance = 0.05  # ±5% 허용

    check(
        f"{ae_name}: target={target_inc:.2f}, actual={actual_rate:.3f}, diff={diff:.3f}",
        diff < tolerance,
        f"차이 {diff:.3f} > 허용 {tolerance}",
    )


# ══════════════════════════════════════════════════════
# 3. daily_resolution_hazard 검증
# ══════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print("3. daily_resolution_hazard — 해소 확률 검증")
print("=" * 60)

duration_spec = {
    "distribution": "normal",
    "params": {"mean": 30, "std": 10, "min": 7},
}

# (a) 기본 범위 확인
res_hazards = [daily_resolution_hazard(d, duration_spec) for d in range(1, 91)]
check("모든 resolution hazard >= 0", all(h >= 0 for h in res_hazards))
check("모든 resolution hazard <= 1", all(h <= 1 for h in res_hazards))

# (b) 경과일 1에서는 낮고, mean 근처에서는 높아야 함
h_res_1 = daily_resolution_hazard(1, duration_spec)
h_res_30 = daily_resolution_hazard(30, duration_spec)
check(f"Day 1 해소 확률 < Day 30 ({h_res_1:.4f} < {h_res_30:.4f})", h_res_1 < h_res_30)

# (c) None이면 0 (비가역적)
h_irreversible = daily_resolution_hazard(30, None)
check("비가역적 AE → 해소 확률 0", h_irreversible == 0.0)

# (d) 해소 곡선 출력
print(f"\n  Resolution hazard curve (duration Normal(30, 10)):")
for d in [1, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 90]:
    h = daily_resolution_hazard(d, duration_spec)
    bar = "█" * int(h * 100)
    print(f"    Day {d:3d} active: {h:.4f} {bar}")


# ══════════════════════════════════════════════════════
# 4. grade_transition_probs 검증
# ══════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print("4. grade_transition_probs — 확률 합 검증")
print("=" * 60)

# 모든 조합에서 확률 합 = 1.0
for grade in [1, 2, 3, 4, 5]:
    for days in [1, 7, 14, 30, 60]:
        for cumulative in [False, True]:
            probs = grade_transition_probs(grade, days, cumulative)
            total = sum(probs.values())
            check(
                f"G{grade} d{days:2d} cum={cumulative} → sum={total:.4f}",
                abs(total - 1.0) < 0.001,
                f"합이 {total:.6f}",
            )

# 누적 독성일 때 악화 확률이 더 높은지
probs_non_cum = grade_transition_probs(2, 30, False)
probs_cum = grade_transition_probs(2, 30, True)
check(
    f"누적독성 시 worsen↑ ({probs_non_cum['worsen']:.4f} → {probs_cum['worsen']:.4f})",
    probs_cum["worsen"] > probs_non_cum["worsen"],
)

# Grade 5는 변화 불가
probs_g5 = grade_transition_probs(5, 14, False)
check("Grade 5 → stable 100%", probs_g5["stable"] == 1.0)


# ══════════════════════════════════════════════════════
# 5. tumor_change_pct — RECIST 기준 검증
# ══════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print("5. tumor_change_pct — RECIST 기준 검증")
print("=" * 60)

response_onset = 63  # ~9주

# CR: 결국 -100%에 도달해야 함
cr_180 = tumor_change_pct(180, "CR", response_onset)
check(f"CR Day 180: {cr_180:.1f}% (should be close to -100%)", cr_180 <= -90)

# PR: 30% 이상 감소해야 함
pr_120 = tumor_change_pct(120, "PR", response_onset)
check(f"PR Day 120: {pr_120:.1f}% (should be ≤ -30%)", pr_120 <= -30)

# SD: ±29% 이내
sd_120 = tumor_change_pct(120, "SD", response_onset)
check(f"SD Day 120: {sd_120:.1f}% (should be -29% ~ +19%)", -29 <= sd_120 <= 19)

# PD: 20% 이상 증가
pd_120 = tumor_change_pct(120, "PD", response_onset)
check(f"PD Day 120: {pd_120:.1f}% (should be ≥ +20%)", pd_120 >= 20)

# 종양 궤적 출력
print(f"\n  Tumor trajectory (response onset Day {response_onset}):")
for response in ["CR", "PR", "SD", "PD"]:
    print(f"    {response}: ", end="")
    for d in [1, 21, 42, 63, 84, 105, 126, 147, 168]:
        pct = tumor_change_pct(d, response, response_onset)
        if pct is not None:
            print(f"D{d}={pct:+.0f}%  ", end="")
    print()


# ══════════════════════════════════════════════════════
# 6. adjust_incidence_by_risk_modifiers 검증
# ══════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print("6. adjust_incidence_by_risk_modifiers — 보정 검증")
print("=" * 60)

# (a) 수정자 없으면 원래 값 유지
adj = adjust_incidence_by_risk_modifiers(0.30, [], set(), 65)
check(f"수정자 없음: {adj:.2f} == 0.30", abs(adj - 0.30) < 0.001)

# (b) 동반질환 매칭
modifiers = [
    {"condition": "baseline diabetes", "incidence_multiplier": 1.5},
    {"condition": "CKD", "incidence_multiplier": 1.8},
]
adj_dm = adjust_incidence_by_risk_modifiers(0.30, modifiers, {"diabetes"}, 65)
check(f"당뇨 환자: {adj_dm:.2f} > 0.30", adj_dm > 0.30)

adj_both = adjust_incidence_by_risk_modifiers(0.30, modifiers, {"diabetes", "ckd"}, 65)
check(f"당뇨+CKD: {adj_both:.2f} > 당뇨만 {adj_dm:.2f}", adj_both > adj_dm)

# (c) 나이 조건
age_modifier = [{"condition": "age > 70", "incidence_multiplier": 1.3}]
adj_young = adjust_incidence_by_risk_modifiers(0.30, age_modifier, set(), 55)
adj_old = adjust_incidence_by_risk_modifiers(0.30, age_modifier, set(), 75)
check(f"55세: {adj_young:.2f} < 75세: {adj_old:.2f}", adj_young < adj_old)

# (d) 0.99 상한 클램프
adj_max = adjust_incidence_by_risk_modifiers(0.80, modifiers, {"diabetes", "ckd"}, 80)
check(f"상한 클램프: {adj_max:.2f} ≤ 0.99", adj_max <= 0.99)


# ══════════════════════════════════════════════════════
# 결과 요약
# ══════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"결과: {PASS} passed, {FAIL} failed (총 {PASS + FAIL})")
print(f"{'=' * 60}")

if FAIL > 0:
    sys.exit(1)
else:
    print("✓ 모든 테스트 통과!")
