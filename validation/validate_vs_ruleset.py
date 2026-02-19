"""
Validation v3: Simulation vs rule_set.json

Statistical methods:
  - Binomial / chi-square / KS tests for basic comparison
  - TOST equivalence testing (FDA-style)
  - Benjamini-Hochberg FDR correction
  - Post-hoc power analysis
  - Anderson-Darling goodness-of-fit

Usage:
    python validation/validate_vs_ruleset.py <run_dir> [--mode natural]
"""
from __future__ import annotations
import json, math, sys, os
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np
from scipy import stats as sp_stats

SEV_TO_GRADE = {"MILD": 1, "MODERATE": 2, "SEVERE": 3, "LIFE-THREATENING": 4, "FATAL": 5}

CATEGORY_WEIGHTS = {
    "Demographics": 1.0,
    "Comorbidities": 0.8,
    "Comorbidity Modifiers": 0.5,
    "Disease Baseline": 0.9,
    "AE Incidence": 1.0,
    "AE Grade Distribution": 0.9,
    "AE Onset Timing": 0.7,
    "Efficacy": 1.0,
    "Dose Modification": 0.7,
    "Supportive Care": 0.6,
    "Survival": 0.5,
    "ECOG": 0.7,
    "AE Cascade": 0.4,
}

EQUIV_MARGIN_PROPORTION = {
    "Demographics": 0.10,
    "Comorbidities": 0.12,
    "Disease Baseline": 0.10,
    "AE Incidence": 0.10,
    "AE Grade Distribution": 0.15,
    "Efficacy": 0.10,
    "Dose Modification": 0.15,
    "Supportive Care": 0.15,
    "Survival": 0.15,
    "ECOG": 0.15,
    "AE Cascade": 0.20,
    "Comorbidity Modifiers": 0.15,
    "AE Onset Timing": 0.15,
}

EQUIV_MARGIN_CONTINUOUS_SIGMA = 0.5


# ── statistical primitives ─────────────────────────────────
def _z_score_proportion(obs, n, expected):
    if n == 0 or expected >= 1.0:
        return 0.0
    se = math.sqrt(expected * (1 - expected) / n) if expected > 0 else 1e-9
    return (obs - expected) / max(se, 1e-9)

def _binomial_test(k, n, p):
    if n == 0:
        return {"p": None, "test": "N/A"}
    if p >= 1.0:
        return {"p": 0.0 if k < n else 1.0, "test": f"binomial({k}/{n}, p=1.0)"}
    if p <= 0.0:
        return {"p": 0.0 if k > 0 else 1.0, "test": f"binomial({k}/{n}, p=0.0)"}
    res = sp_stats.binomtest(k, n, p, alternative='two-sided')
    return {"p": float(res.pvalue), "test": f"binomial({k}/{n}, p={p:.3f})"}

def _wilson_ci(k, n, alpha=0.05):
    if n == 0:
        return (0, 0)
    z = sp_stats.norm.ppf(1 - alpha / 2)
    p_hat = k / n
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n)) / n) / denom
    return (max(0, center - margin), min(1, center + margin))

def _ks_test(values, dist_name, params):
    if len(values) < 5:
        return {"stat": None, "p": None}
    mean, std = params.get("mean", 0), params.get("std", 1)
    if dist_name == "lognormal":
        sigma2 = math.log(1 + (std / max(mean, 1e-9))**2)
        mu_ln = math.log(max(mean, 1e-9)) - sigma2 / 2
        sigma_ln = math.sqrt(sigma2)
        stat, p = sp_stats.kstest(values, 'lognorm', args=(sigma_ln, 0, math.exp(mu_ln)))
    elif dist_name == "exponential":
        stat, p = sp_stats.kstest(values, 'expon', args=(0, max(mean, 1e-9)))
    else:
        stat, p = sp_stats.kstest(values, 'norm', args=(mean, max(std, 1e-9)))
    return {"stat": float(stat), "p": float(p)}

def _ad_test(values):
    if len(values) < 8:
        return {"stat": None, "cv_5pct": None, "verdict": "insufficient_data"}
    res = sp_stats.anderson(np.array(values, dtype=float), dist='norm')
    cv5 = float(res.critical_values[2])
    return {
        "stat": float(res.statistic),
        "cv_5pct": cv5,
        "verdict": "normal" if res.statistic < cv5 else "non-normal",
    }

def _tost_proportion(obs_p, n, expected_p, margin):
    if n == 0 or expected_p is None:
        return {"p": 1.0, "equivalent": False, "margin": margin}
    se = math.sqrt(max(obs_p * (1 - obs_p), 1e-12) / max(n, 1))
    t1 = (obs_p - (expected_p - margin)) / max(se, 1e-9)
    t2 = ((expected_p + margin) - obs_p) / max(se, 1e-9)
    p1 = float(1 - sp_stats.norm.cdf(t1))
    p2 = float(1 - sp_stats.norm.cdf(t2))
    tost_p = max(p1, p2)
    return {"p": round(tost_p, 6), "equivalent": bool(tost_p < 0.05), "margin": margin}

def _tost_mean(values, expected_mean, margin_sigma=EQUIV_MARGIN_CONTINUOUS_SIGMA):
    if len(values) < 5:
        return {"p": 1.0, "equivalent": False}
    obs_mean = float(np.mean(values))
    obs_std = float(np.std(values, ddof=1))
    margin = margin_sigma * max(obs_std, 1e-9)
    se = obs_std / math.sqrt(len(values))
    t1 = (obs_mean - (expected_mean - margin)) / max(se, 1e-9)
    t2 = ((expected_mean + margin) - obs_mean) / max(se, 1e-9)
    df = len(values) - 1
    p1 = float(1 - sp_stats.t.cdf(t1, df))
    p2 = float(1 - sp_stats.t.cdf(t2, df))
    tost_p = max(p1, p2)
    return {"p": round(tost_p, 6), "equivalent": bool(tost_p < 0.05), "margin": round(margin, 3)}

def _power_proportion(n, expected_p, margin=0.10, alpha=0.05):
    if n == 0 or expected_p is None or expected_p <= 0 or expected_p >= 1:
        return None
    se = math.sqrt(expected_p * (1 - expected_p) / n)
    z_a = sp_stats.norm.ppf(1 - alpha / 2)
    ncp = margin / max(se, 1e-9)
    power = 1 - sp_stats.norm.cdf(z_a - ncp) + sp_stats.norm.cdf(-z_a - ncp)
    return round(float(power), 3)

def _power_mean(n, expected_std, margin_sigma=EQUIV_MARGIN_CONTINUOUS_SIGMA, alpha=0.05):
    if n < 5 or expected_std <= 0:
        return None
    margin = margin_sigma * expected_std
    se = expected_std / math.sqrt(n)
    z_a = sp_stats.norm.ppf(1 - alpha / 2)
    ncp = margin / max(se, 1e-9)
    power = 1 - sp_stats.norm.cdf(z_a - ncp) + sp_stats.norm.cdf(-z_a - ncp)
    return round(float(power), 3)


# ── scoring ─────────────────────────────────────────────
def score_proportion(label, obs_k, n, expected_p, section):
    obs_p = obs_k / n if n > 0 else 0
    z = _z_score_proportion(obs_p, n, expected_p)
    bt = _binomial_test(obs_k, n, expected_p)
    ci = _wilson_ci(obs_k, n)
    margin = EQUIV_MARGIN_PROPORTION.get(section, 0.10)
    tost = _tost_proportion(obs_p, n, expected_p, margin)
    power = _power_proportion(n, expected_p, margin)

    if tost.get("equivalent"):
        grade = "A"
    elif abs(z) < 1.0:
        grade = "A"
    elif abs(z) < 2.0:
        grade = "B"
    elif abs(z) < 3.0:
        grade = "C"
    else:
        grade = "D"

    test_str = f"z={z:+.2f} binom_p={bt['p']:.4g}" if bt['p'] is not None else f"z={z:+.2f}"
    if tost.get("equivalent"):
        test_str += " TOST=equiv"
    return {
        "label": label, "section": section,
        "observed": round(obs_p, 4), "expected": round(expected_p, 4),
        "n": n, "z": round(z, 2), "ci_95": [round(ci[0], 4), round(ci[1], 4)],
        "binomial_p": bt["p"], "test": test_str,
        "tost_p": tost["p"], "tost_equiv": tost["equivalent"], "tost_margin": margin,
        "power": power, "grade": grade,
    }

def score_continuous(label, values, expected_mean, expected_std, dist_name, section):
    if len(values) < 3:
        return {"label": label, "section": section, "grade": "?", "n": len(values), "note": "insufficient data"}
    obs_mean = float(np.mean(values))
    obs_std = float(np.std(values, ddof=1))
    ks = _ks_test(values, dist_name, {"mean": expected_mean, "std": expected_std})
    ad = _ad_test(values)
    tost = _tost_mean(values, expected_mean)
    power = _power_mean(len(values), max(expected_std, 1e-9))
    z = (obs_mean - expected_mean) / max(expected_std / math.sqrt(len(values)), 1e-9)

    if tost.get("equivalent"):
        grade = "A"
    elif abs(z) < 1.0:
        grade = "A"
    elif abs(z) < 2.0:
        grade = "B"
    elif abs(z) < 3.0:
        grade = "C"
    else:
        grade = "D"

    test_str = f"z={z:+.2f}"
    if ks["p"] is not None:
        test_str += f" KS_p={ks['p']:.4g}"
    if tost.get("equivalent"):
        test_str += " TOST=equiv"
    return {
        "label": label, "section": section,
        "observed_mean": round(obs_mean, 2), "observed_std": round(obs_std, 2),
        "expected_mean": round(expected_mean, 2), "expected_std": round(expected_std, 2),
        "n": len(values), "z": round(z, 2),
        "ks_p": ks["p"], "ks_stat": ks.get("stat"),
        "ad_stat": ad.get("stat"), "ad_cv_5pct": ad.get("cv_5pct"), "ad_verdict": ad.get("verdict"),
        "tost_p": tost["p"], "tost_equiv": tost["equivalent"],
        "power": power, "test": test_str, "grade": grade,
    }


# ── data loading ────────────────────────────────────────
def load_run(run_dir: Path, mode: str = "natural"):
    patients = {}
    for f in sorted((run_dir / "patients").glob("*.json")):
        p = json.loads(f.read_text())
        patients[p["patient_id"]] = p
    simulations = {}
    for f in sorted((run_dir / "simulations").glob(f"*_{mode}.jsonl")):
        pid = f.stem.replace(f"_{mode}", "")
        days = []
        for line in f.read_text().splitlines():
            if line.strip():
                days.append(json.loads(line))
        simulations[pid] = days
    # Try rule_set from run dir first, then from data/
    rs_path = run_dir / "rule_set.json"
    if not rs_path.exists():
        rs_path = run_dir.parent.parent / "rule_set.json"
    if not rs_path.exists():
        # Go up until we find data/rule_set.json
        candidate = run_dir
        for _ in range(5):
            candidate = candidate.parent
            p = candidate / "data" / "rule_set.json"
            if p.exists():
                rs_path = p
                break
    rule_set = json.loads(rs_path.read_text())
    return patients, simulations, rule_set


# ── comparison functions ────────────────────────────────
def compare_demographics(patients, rule_set):
    rows = []
    N = len(patients)
    demos = rule_set.get("demographics", {})

    for field in ("sex", "race", "smoking", "ecog_ps"):
        spec = demos.get(field, {})
        if spec.get("type") != "categorical":
            continue
        options = spec.get("options", {})
        counts = Counter()
        for p in patients.values():
            val = str(p["emr"]["demographics"].get(field, ""))
            counts[val] += 1
        for cat, expected_p in options.items():
            k = counts.get(str(cat), 0)
            rows.append(score_proportion(f"{field}={cat}", k, N, expected_p, "Demographics"))

    for field in ("age", "bmi"):
        spec = demos.get(field, {})
        if spec.get("type") != "numeric":
            continue
        params = spec.get("params", {})
        values = [p["emr"]["demographics"].get(field) for p in patients.values()
                  if p["emr"]["demographics"].get(field) is not None]
        values = [float(v) for v in values]
        if values:
            rows.append(score_continuous(
                field, values, params["mean"], params["std"],
                spec.get("distribution", "normal"), "Demographics"))
    return rows


def compare_comorbidities(patients, rule_set):
    rows = []
    N = len(patients)
    for comorb in rule_set.get("comorbidities", []):
        cond = comorb["condition"]
        expected_p = comorb["base_probability"]
        k = sum(1 for p in patients.values()
                if any(c.get("condition", "").lower() == cond.lower()
                       for c in p["emr"].get("medical_history", [])))
        rows.append(score_proportion(f"comorbidity: {cond}", k, N, expected_p, "Comorbidities"))
    return rows


def compare_disease_baseline(patients, rule_set):
    rows = []
    N = len(patients)
    db = rule_set.get("disease_baseline", {})

    tumor_sites = db.get("tumor_sites", {})
    for site, expected_p in tumor_sites.items():
        k = 0
        for p in patients.values():
            diag = p["emr"].get("diagnosis", {})
            bt = p["emr"].get("baseline_tumor", diag)
            lesions = bt.get("target_lesions", diag.get("target_lesions", []))
            for les in (lesions or []):
                ts = les.get("tumor_site", les.get("site", les.get("location", "")))
                if ts and ts.lower() == site.lower():
                    k += 1
                    break
        rows.append(score_proportion(f"tumor_site: {site}", k, N, expected_p, "Disease Baseline"))

    sod_spec = db.get("sum_of_diameters_mm", {})
    if sod_spec.get("type") == "numeric":
        params = sod_spec.get("params", {})
        values = []
        for p in patients.values():
            diag = p["emr"].get("diagnosis", {})
            sod = diag.get("sum_of_diameters_mm")
            if sod is None:
                lesions = diag.get("target_lesions", [])
                if lesions:
                    sod = sum(float(l.get("diameter_mm", 0)) for l in lesions if isinstance(l, dict))
            if sod is not None and sod > 0:
                values.append(float(sod))
        if values:
            rows.append(score_continuous(
                "sum_of_diameters_mm", values,
                params.get("mean", 50), params.get("std", 20),
                sod_spec.get("distribution", "lognormal"), "Disease Baseline"))

    ntl_spec = db.get("n_target_lesions", {})
    if ntl_spec.get("type") == "categorical":
        options = ntl_spec.get("options", {})
        counts = Counter()
        for p in patients.values():
            diag = p["emr"].get("diagnosis", {})
            lesions = diag.get("target_lesions", [])
            n = len(lesions) if lesions else 0
            counts[str(n)] += 1
        for cat, expected_p in options.items():
            k = counts.get(cat, 0)
            rows.append(score_proportion(f"n_target_lesions={cat}", k, N, expected_p, "Disease Baseline"))

    return rows


def _extract_patient_aes(sim_days):
    ae_info = {}
    for day_rec in sim_days:
        day = day_rec.get("day", 0)
        for ae in day_rec.get("AE", []):
            term = ae.get("AETERM", "")
            if not term:
                continue
            g = ae.get("_grade") or SEV_TO_GRADE.get(ae.get("AESEV", ""), 0)
            if term not in ae_info:
                ae_info[term] = {"onset_day": day, "max_grade": g, "grades": [g]}
            else:
                ae_info[term]["max_grade"] = max(ae_info[term]["max_grade"], g)
                ae_info[term]["grades"].append(g)
    return ae_info


def compare_ae_profile(simulations, rule_set):
    rows = []
    N = len(simulations)
    ae_profile = rule_set.get("ae_profile", [])

    all_patient_aes = {}
    for pid, days in simulations.items():
        all_patient_aes[pid] = _extract_patient_aes(days)

    for ae_spec in ae_profile:
        ae_term = ae_spec["ae_term"]
        expected_incidence = ae_spec["incidence_all_grade"]
        grade_dist = ae_spec.get("grade_distribution", {})

        affected = [pid for pid, aes in all_patient_aes.items() if ae_term in aes]
        k = len(affected)
        rows.append(score_proportion(f"AE incidence: {ae_term}", k, N, expected_incidence, "AE Incidence"))

        if affected and grade_dist:
            grade_counts = Counter()
            for pid in affected:
                mg = all_patient_aes[pid][ae_term]["max_grade"]
                grade_counts[str(mg)] += 1
            n_aff = len(affected)
            for g_str, g_prob in grade_dist.items():
                if g_prob > 0:
                    gc = grade_counts.get(g_str, 0)
                    rows.append(score_proportion(
                        f"AE grade {ae_term} G{g_str}", gc, n_aff, g_prob,
                        "AE Grade Distribution"))

        onset_spec = ae_spec.get("onset_day", {})
        if onset_spec and affected:
            onset_values = [all_patient_aes[pid][ae_term]["onset_day"] for pid in affected]
            params = onset_spec.get("params", {})
            if params.get("mean"):
                rows.append(score_continuous(
                    f"AE onset: {ae_term}", onset_values,
                    params["mean"], params.get("std", params["mean"] * 0.5),
                    onset_spec.get("distribution", "normal"), "AE Onset Timing"))

    return rows, all_patient_aes


def compare_efficacy(patients, simulations, rule_set):
    rows = []
    N = len(simulations)
    eff = rule_set.get("efficacy", {})
    trd = rule_set.get("disease_baseline", {}).get("tumor_response_distribution", {})

    best_responses = Counter()
    for pid, days in simulations.items():
        nadir = 0
        for d in days:
            tumor = d.get("objective", {}).get("tumor", {})
            change = tumor.get("estimated_change_pct", 0)
            if change < nadir:
                nadir = change

        if nadir <= -90:
            best = "CR"
        elif nadir <= -30:
            best = "PR"
        elif nadir < 20:
            best = "SD"
        else:
            best = "PD"
        best_responses[best] += 1

    orr_k = best_responses.get("CR", 0) + best_responses.get("PR", 0)
    orr_expected = eff.get("overall_response_rate", 0)
    rows.append(score_proportion("ORR (CR+PR)", orr_k, N, orr_expected, "Efficacy"))

    cr_expected = eff.get("complete_response_rate", trd.get("CR", 0))
    rows.append(score_proportion("CR rate", best_responses.get("CR", 0), N, cr_expected, "Efficacy"))

    for cat, expected_p in trd.items():
        k = best_responses.get(cat, 0)
        rows.append(score_proportion(f"Response: {cat}", k, N, expected_p, "Efficacy"))

    return rows, best_responses


def compare_dose_modification(simulations, rule_set):
    rows = []
    N = len(simulations)

    holds = 0
    reductions = 0
    discontinuations = 0
    for pid, days in simulations.items():
        patient_held = False
        patient_reduced = False
        patient_disc = False
        for d in days:
            obj = d.get("objective", {})
            for drug_key in list(obj.keys()):
                drug_data = obj.get(drug_key, {})
                if not isinstance(drug_data, dict):
                    continue
                if drug_data.get("treatment_held"):
                    patient_held = True
                dl = drug_data.get("dose_level", 1.0)
                if isinstance(dl, (int, float)) and dl < 1.0:
                    patient_reduced = True
                if drug_data.get("treatment_discontinued"):
                    patient_disc = True
        if patient_held:
            holds += 1
        if patient_reduced:
            reductions += 1
        if patient_disc:
            discontinuations += 1

    rows.append(score_proportion("Dose hold (any)", holds, N, 0.35, "Dose Modification"))
    rows.append(score_proportion("Dose reduction (any)", reductions, N, 0.25, "Dose Modification"))
    rows.append(score_proportion("Treatment discontinuation", discontinuations, N, 0.15, "Dose Modification"))
    return rows


def compare_supportive_care(simulations, rule_set, all_patient_aes):
    rows = []
    sc_rules = rule_set.get("supportive_care_rules", [])

    for sc in sc_rules:
        ae_term = sc["ae_term"]
        treatments = sc.get("treatments", [])
        if not treatments:
            continue

        affected_pids = [pid for pid, aes in all_patient_aes.items() if ae_term in aes]
        if not affected_pids:
            continue
        n_affected = len(affected_pids)

        prescribed = 0
        for pid in affected_pids:
            days = simulations.get(pid, [])
            found = False
            for d in days:
                for cm in d.get("CM", []):
                    if cm.get("_baseline"):
                        continue
                    indc = (cm.get("CMINDC") or "").lower()
                    if ae_term.lower() in indc or indc in ae_term.lower():
                        prescribed += 1
                        found = True
                        break
                if found:
                    break

        expected_p = max(t.get("probability", 0.5) for t in treatments)
        if expected_p >= 1.0:
            expected_p = 0.95
        rows.append(score_proportion(
            f"SC: {ae_term} ({treatments[0]['drug']})", prescribed, n_affected,
            expected_p, "Supportive Care"))
    return rows


def compare_mortality(simulations, rule_set):
    rows = []
    N = len(simulations)
    mort = rule_set.get("mortality_model", {})
    annual_rate = mort.get("baseline_annual_mortality", 0.25)

    sim_days_max = max(len(days) for days in simulations.values()) if simulations else 126
    expected_mort = 1 - (1 - annual_rate) ** (sim_days_max / 365)

    deaths = 0
    for pid, days in simulations.items():
        last = days[-1] if days else {}
        loc = last.get("objective", {}).get("location", "")
        ds = last.get("DS", {})
        if loc == "DECEASED" or (isinstance(ds, dict) and ds.get("DSDECOD") == "DEATH"):
            deaths += 1

    rows.append(score_proportion("Mortality", deaths, N, expected_mort, "Survival"))
    return rows


def compare_ecog(patients, simulations, rule_set):
    rows = []
    N = len(patients)
    demos = rule_set.get("demographics", {})
    ecog_spec = demos.get("ecog_ps", {})
    if ecog_spec.get("type") != "categorical":
        return rows

    options = ecog_spec.get("options", {})
    ecog_counts = Counter()
    for p in patients.values():
        e = str(p["emr"]["demographics"].get("ecog_ps", p["emr"].get("baseline_ecog", "")))
        ecog_counts[e] += 1
    for cat, expected_p in options.items():
        k = ecog_counts.get(cat, 0)
        rows.append(score_proportion(f"Baseline ECOG={cat}", k, N, expected_p, "ECOG"))

    worsened = 0
    for pid, days in simulations.items():
        if not days:
            continue
        first_ecog = days[0].get("objective", {}).get("ecog")
        last_ecog = days[-1].get("objective", {}).get("ecog")
        if first_ecog is not None and last_ecog is not None and last_ecog > first_ecog:
            worsened += 1
    rows.append(score_proportion("ECOG worsened", worsened, N, 0.35, "ECOG"))
    return rows


def compare_ae_cascade(simulations, rule_set, all_patient_aes):
    rows = []
    cascade_rules = rule_set.get("ae_cascade_rules", [])
    for rule in cascade_rules:
        trigger = rule["trigger_ae"]
        threshold = rule["grade_threshold"]
        target = rule["target_ae"]
        multiplier = rule["multiplier"]

        triggered = []
        non_triggered = []
        for pid, aes in all_patient_aes.items():
            if trigger in aes and aes[trigger]["max_grade"] >= threshold:
                triggered.append(pid)
            else:
                non_triggered.append(pid)

        if len(triggered) < 2:
            rows.append({
                "label": f"Cascade: {trigger}>=G{threshold}->{target} (x{multiplier})",
                "section": "AE Cascade", "grade": "?",
                "note": f"Only {len(triggered)} patients with trigger", "n": len(triggered),
            })
            continue

        target_in_triggered = sum(1 for pid in triggered if target in all_patient_aes[pid])
        target_in_non = sum(1 for pid in non_triggered if target in all_patient_aes[pid])
        p_trig = target_in_triggered / len(triggered) if triggered else 0
        p_non = target_in_non / len(non_triggered) if non_triggered else 0

        if p_non > 0:
            obs_ratio = p_trig / p_non
        else:
            obs_ratio = float('inf') if p_trig > 0 else 1.0

        rows.append({
            "label": f"Cascade: {trigger}>=G{threshold}->{target} (x{multiplier})",
            "section": "AE Cascade",
            "expected_multiplier": multiplier,
            "observed_ratio": round(obs_ratio, 2),
            "n_triggered": len(triggered),
            "n_non_triggered": len(non_triggered),
            "target_rate_triggered": round(p_trig, 3),
            "target_rate_non_triggered": round(p_non, 3),
            "grade": "A" if 0.8 * multiplier <= obs_ratio <= 1.5 * multiplier else
                     "B" if obs_ratio > 1.0 else "C",
        })
    return rows


# ── FDR correction ──────────────────────────────────────
def apply_fdr_correction(all_rows, alpha=0.05):
    idx_pvals = []
    for i, d in enumerate(all_rows):
        raw_p = d.get("binomial_p")
        if raw_p is None:
            raw_p = d.get("ks_p")
        if raw_p is not None:
            idx_pvals.append((i, float(raw_p)))

    if not idx_pvals:
        return {"n_tested": 0, "n_significant_raw": 0, "n_significant_fdr": 0}

    idx_pvals.sort(key=lambda x: x[1])
    m = len(idx_pvals)
    pvals = np.array([p for _, p in idx_pvals])
    adjusted = np.zeros(m)
    adjusted[m - 1] = pvals[m - 1]
    for k in range(m - 2, -1, -1):
        adjusted[k] = min(adjusted[k + 1], pvals[k] * m / (k + 1))
    adjusted = np.clip(adjusted, 0, 1)

    for k, (i, _) in enumerate(idx_pvals):
        all_rows[i]["fdr_p"] = round(float(adjusted[k]), 6)
        all_rows[i]["fdr_significant"] = bool(float(adjusted[k]) < alpha)

    for row in all_rows:
        if row.get("grade") == "D" and row.get("fdr_significant") is False:
            row["grade"] = "C"
            row["note"] = row.get("note", "") + " [D->C: FDR non-significant]"

    n_sig_raw = int(np.sum(pvals < alpha).item())
    n_sig_fdr = sum(1 for r in all_rows if r.get("fdr_significant"))
    return {"n_tested": m, "n_significant_raw": n_sig_raw, "n_significant_fdr": n_sig_fdr}


# ── overall score ───────────────────────────────────────
GRADE_SCORES = {"A": 100, "B": 75, "C": 50, "D": 25, "?": None}

def compute_overall_score(all_rows):
    section_scores = defaultdict(list)
    for r in all_rows:
        sec = r.get("section", "")
        g = r.get("grade", "?")
        s = GRADE_SCORES.get(g)
        if s is not None:
            section_scores[sec].append(s)

    weighted_sum = 0
    weight_sum = 0
    section_summary = {}
    for sec, scores in section_scores.items():
        avg = sum(scores) / len(scores)
        w = CATEGORY_WEIGHTS.get(sec, 0.5)
        weighted_sum += avg * w
        weight_sum += w
        section_summary[sec] = {
            "avg_score": round(avg, 1),
            "n_items": len(scores),
            "weight": w,
            "grade_dist": dict(Counter(
                r.get("grade", "?") for r in all_rows if r.get("section") == sec and r.get("grade") != "?"
            )),
        }

    tost_equiv = sum(1 for r in all_rows if r.get("tost_equiv"))
    tost_total = sum(1 for r in all_rows if "tost_equiv" in r)

    overall = round(weighted_sum / max(weight_sum, 1e-9), 1)
    return {
        "overall_score": overall,
        "overall_grade": "A" if overall >= 85 else "B" if overall >= 70 else "C" if overall >= 50 else "D",
        "sections": section_summary,
        "tost_equivalent": tost_equiv,
        "tost_total": tost_total,
    }


# ── report generation ───────────────────────────────────
def generate_report(all_rows, summary, fdr_stats, run_dir, mode):
    lines = []
    lines.append(f"# Validation Report v3: Simulation vs rule_set.json")
    lines.append(f"Run: `{run_dir.name}` | Mode: `{mode}` | Generated: {datetime.now().isoformat()[:19]}")
    lines.append("")
    lines.append(f"## Overall: **{summary['overall_grade']}** ({summary['overall_score']}/100)")
    lines.append("")

    lines.append("### Grade Meaning")
    lines.append("| Grade | Meaning |")
    lines.append("|-------|---------|")
    lines.append("| A | Equivalent (TOST) or within 1 sigma |")
    lines.append("| B | Within 2 sigma — acceptable variation |")
    lines.append("| C | Within 3 sigma or FDR non-significant — investigate |")
    lines.append("| D | >3 sigma AND FDR-significant — engine issue |")
    lines.append("")

    lines.append("### TOST Equivalence")
    lines.append(f"Equivalent: **{summary['tost_equivalent']}/{summary['tost_total']}** comparisons")
    lines.append("")

    lines.append("### Multiple Comparison Correction (BH-FDR)")
    lines.append(f"- Tested: {fdr_stats['n_tested']} p-values")
    lines.append(f"- Raw significant (p<0.05): {fdr_stats['n_significant_raw']}")
    lines.append(f"- FDR significant (q<0.05): {fdr_stats['n_significant_fdr']}")
    lines.append("")

    lines.append("### Section Scores")
    lines.append("| Section | Score | Grade | Items | Weight |")
    lines.append("|---------|-------|-------|-------|--------|")
    for sec, info in sorted(summary["sections"].items(), key=lambda x: -x[1]["avg_score"]):
        g = "A" if info["avg_score"] >= 85 else "B" if info["avg_score"] >= 70 else "C" if info["avg_score"] >= 50 else "D"
        lines.append(f"| {sec} | {info['avg_score']} | {g} | {info['n_items']} | {info['weight']} |")
    lines.append("")

    for section in sorted(set(r.get("section", "") for r in all_rows)):
        sec_rows = [r for r in all_rows if r.get("section") == section]
        if not sec_rows:
            continue
        lines.append(f"## {section}")
        lines.append("")
        lines.append("| Label | Obs | Exp | Grade | Test | FDR q |")
        lines.append("|-------|-----|-----|-------|------|-------|")
        for r in sorted(sec_rows, key=lambda x: x.get("grade", "?")):
            obs = r.get("observed", r.get("observed_mean", r.get("observed_ratio", "?")))
            exp = r.get("expected", r.get("expected_mean", r.get("expected_multiplier", "?")))
            if isinstance(obs, float):
                obs = f"{obs:.3f}"
            if isinstance(exp, float):
                exp = f"{exp:.3f}"
            fdr_q = r.get("fdr_p")
            fdr_str = f"{fdr_q:.4g}" if fdr_q is not None else "-"
            test = r.get("test", r.get("note", "-"))
            lines.append(f"| {r.get('label','')} | {obs} | {exp} | {r.get('grade','?')} | {test} | {fdr_str} |")
        lines.append("")

    confirmed = [r for r in all_rows if r.get("grade") == "D" and r.get("fdr_significant")]
    if confirmed:
        lines.append("## Confirmed Engine Issues (FDR-significant D grades)")
        lines.append("")
        for r in confirmed:
            lines.append(f"- **{r.get('label')}** ({r.get('section')}): obs={r.get('observed', r.get('observed_mean', '?'))}, "
                        f"exp={r.get('expected', r.get('expected_mean', '?'))}, q={r.get('fdr_p', '?')}")
        lines.append("")

    low_power = [r for r in all_rows if r.get("power") is not None and r["power"] < 0.5]
    if low_power:
        lines.append("## Low Statistical Power Warnings (<50%)")
        lines.append("")
        for r in low_power:
            lines.append(f"- {r.get('label')} ({r.get('section')}): power={r['power']}")
        lines.append("")

    return "\n".join(lines)


# ── main ────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_vs_ruleset.py <run_dir> [--mode natural]")
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    mode = "natural"
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        mode = sys.argv[idx + 1]

    print(f"Loading run: {run_dir.name}, mode: {mode}")
    patients, simulations, rule_set = load_run(run_dir, mode)
    N = len(patients)
    print(f"Patients: {N}, Simulations: {len(simulations)}")

    all_rows = []

    print("Comparing demographics...")
    all_rows.extend(compare_demographics(patients, rule_set))

    print("Comparing comorbidities...")
    all_rows.extend(compare_comorbidities(patients, rule_set))

    print("Comparing disease baseline...")
    all_rows.extend(compare_disease_baseline(patients, rule_set))

    print("Comparing AE profile...")
    ae_rows, all_patient_aes = compare_ae_profile(simulations, rule_set)
    all_rows.extend(ae_rows)

    print("Comparing efficacy...")
    eff_rows, _ = compare_efficacy(patients, simulations, rule_set)
    all_rows.extend(eff_rows)

    print("Comparing dose modification...")
    all_rows.extend(compare_dose_modification(simulations, rule_set))

    print("Comparing supportive care...")
    all_rows.extend(compare_supportive_care(simulations, rule_set, all_patient_aes))

    print("Comparing mortality...")
    all_rows.extend(compare_mortality(simulations, rule_set))

    print("Comparing ECOG...")
    all_rows.extend(compare_ecog(patients, simulations, rule_set))

    print("Comparing AE cascades...")
    all_rows.extend(compare_ae_cascade(simulations, rule_set, all_patient_aes))

    print("Applying FDR correction...")
    fdr_stats = apply_fdr_correction(all_rows)

    print("Computing overall score...")
    summary = compute_overall_score(all_rows)

    out_dir = run_dir / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"ruleset_validation_{mode}_v3.json"
    with open(json_path, "w") as f:
        json.dump({"summary": summary, "fdr": fdr_stats, "details": all_rows},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"JSON saved: {json_path}")

    report = generate_report(all_rows, summary, fdr_stats, run_dir, mode)
    md_path = out_dir / f"ruleset_validation_{mode}_v3.md"
    with open(md_path, "w") as f:
        f.write(report)
    print(f"Report saved: {md_path}")

    print(f"\n{'='*60}")
    print(f"Overall: {summary['overall_grade']} ({summary['overall_score']}/100)")
    print(f"TOST equivalent: {summary['tost_equivalent']}/{summary['tost_total']}")
    print(f"FDR significant: {fdr_stats['n_significant_fdr']}/{fdr_stats['n_tested']}")
    for sec, info in sorted(summary["sections"].items(), key=lambda x: -x[1]["avg_score"]):
        print(f"  {sec:25s}: {info['avg_score']:5.1f} ({info['n_items']} items)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
