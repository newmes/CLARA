#!/usr/bin/env python3
"""E2E test + quality evaluation for the Doc Agent workflow.

Usage:
    cd /data2/workspace/AlphaRaven/dev
    python scripts/test_doc_agent.py [--runs N] [--no-ai]

Options:
    --runs N    Number of repetitions per case (default: 3)
    --no-ai     Skip AI steps (test deterministic pipeline only)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

# Add project root to path for src.doc_agent imports
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from lxml import etree

from src.doc_agent.e2b_converter import convert_to_e2b_xml, validate_e2b_xml
from src.doc_agent.meddra_coder import code_meddra
from src.doc_agent.medwatch_mapper import classify_cm_records, map_crf_to_medwatch
from src.doc_agent.medwatch_pdf import generate_medwatch_pdf
from src.doc_agent.config import Settings
from src.doc_agent.schemas.agent_output import MedDRACode, SentinelOutput
from src.doc_agent.schemas.crf import CRFData
from src.doc_agent.schemas.medwatch import MedWatch3500A

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "patients"
OUTPUT_DIR = PROJECT_ROOT / "output"


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_patient(patient_id: str) -> tuple[CRFData, Optional[SentinelOutput]]:
    """Load CRF data and optional sentinel output from patient JSON."""
    path = DATA_DIR / f"{patient_id}.json"
    raw = json.loads(path.read_text())

    sentinel_output = None
    if raw.get("sentinel_output"):
        sentinel_output = SentinelOutput.model_validate(raw["sentinel_output"])

    # Remove sentinel_output from CRF data before validation
    crf_raw = {k: v for k, v in raw.items() if k != "sentinel_output"}
    crf = CRFData.model_validate(crf_raw)

    return crf, sentinel_output


# ---------------------------------------------------------------------------
# Deterministic Pipeline Test (no AI)
# ---------------------------------------------------------------------------

def test_deterministic_pipeline(
    crf: CRFData,
    sentinel_output: Optional[SentinelOutput],
    settings: Settings,
) -> dict[str, Any]:
    """Test the non-AI components: CRF mapping, MedDRA coding, E2B conversion."""
    results: dict[str, Any] = {"errors": []}

    # 1. CRF → MedWatch mapping
    try:
        medwatch = map_crf_to_medwatch(crf, settings, sentinel_output)
        results["medwatch"] = medwatch
        results["mapping_ok"] = True
    except Exception as e:
        results["errors"].append(f"CRF mapping failed: {e}")
        results["mapping_ok"] = False
        return results

    # 1b. CM separation check
    cm_sep = evaluate_cm_separation(medwatch, crf)
    results["cm_separation"] = cm_sep
    if not cm_sep["c9_clean"]:
        results["errors"].append(f"AE treatment meds leaked to C9: {cm_sep['leaked_ae_meds']}")

    # 1c. Non-serious gate check
    ns_gate = evaluate_non_serious_gate(medwatch, crf)
    results["non_serious_gate"] = ns_gate
    if not ns_gate["correct"]:
        results["errors"].append(
            f"Non-serious flag mismatch: expected={ns_gate['expected_non_serious']}, "
            f"actual={ns_gate['actual_non_serious']}"
        )

    # 1d. Investigator mapping check
    inv_map = evaluate_investigator_mapping(medwatch, crf)
    results["investigator_mapping"] = inv_map
    if not inv_map["correct"]:
        results["errors"].append("Investigator mapping mismatch")

    # 1e. Hospitalization dates check
    hosp_dates = evaluate_hospitalization_dates(medwatch, crf)
    results["hospitalization_dates"] = hosp_dates
    if not hosp_dates["correct"]:
        results["errors"].append("Hospitalization dates mismatch")

    # 1f. CTCAE Grade mapping check
    ctcae_grade = evaluate_ctcae_grade_mapping(crf)
    results["ctcae_grade"] = ctcae_grade

    # 1g. Imaging / PFT domain check
    img_pft = evaluate_imaging_pft_domains(crf)
    results["imaging_pft"] = img_pft

    # 1h. Microbiology / Consultation domain check
    mb_consult = evaluate_microbiology_consultation_domains(crf)
    results["microbiology_consultation"] = mb_consult

    # 2. MedDRA coding
    try:
        meddra_code = code_meddra(crf.ae.AETERM, use_medgemma=False)
        results["meddra_code"] = meddra_code
        results["meddra_ok"] = meddra_code.pt_code is not None
    except Exception as e:
        results["errors"].append(f"MedDRA coding failed: {e}")
        results["meddra_ok"] = False
        meddra_code = MedDRACode(pt=crf.ae.AETERM)
        results["meddra_code"] = meddra_code

    # 3. Fill narrative placeholder for E2B test
    medwatch.section_b.narrative = "[Narrative placeholder for testing]"
    medwatch.section_c.dechallenge = "Yes — drug was withdrawn"
    medwatch.section_c.rechallenge = "Does not apply"

    # 4. E2B XML generation
    try:
        e2b_xml = convert_to_e2b_xml(
            medwatch=medwatch,
            crf=crf,
            meddra_code=meddra_code,
            settings=settings,
        )
        results["e2b_xml"] = e2b_xml
        results["e2b_ok"] = True
    except Exception as e:
        results["errors"].append(f"E2B conversion failed: {e}")
        results["e2b_ok"] = False
        return results

    # 5. Validate XML against XSD schema
    is_valid, schema_errors = validate_e2b_xml(e2b_xml)
    results["xml_valid"] = is_valid
    if not is_valid:
        for err in schema_errors:
            results["errors"].append(f"E2B schema validation: {err}")

    return results


# ---------------------------------------------------------------------------
# MedWatch 3500A Evaluation
# ---------------------------------------------------------------------------

def evaluate_medwatch_completeness(medwatch: MedWatch3500A) -> dict[str, Any]:
    """Check how many of the 33 MedWatch fields are filled."""
    filled = 0
    total = 0
    empty_fields = []

    sections = {
        "A": medwatch.section_a,
        "B": medwatch.section_b,
        "C": medwatch.section_c,
        "E": medwatch.section_e,
        "G": medwatch.section_g,
    }

    for section_name, section in sections.items():
        for field_name, value in section.model_dump().items():
            total += 1
            if value is not None and value != "" and value is not False:
                filled += 1
            else:
                empty_fields.append(f"{section_name}.{field_name}")

    return {
        "filled": filled,
        "total": total,
        "completeness": round(filled / total * 100, 1) if total else 0,
        "empty_fields": empty_fields,
    }


# ---------------------------------------------------------------------------
# B5 Narrative Evaluation
# ---------------------------------------------------------------------------

def evaluate_narrative(
    narrative: str,
    crf: CRFData,
    sentinel_output: Optional[SentinelOutput],
) -> dict[str, Any]:
    """Evaluate B5 narrative quality."""
    results: dict[str, Any] = {}

    # Word count
    words = narrative.split()
    results["word_count"] = len(words)
    results["word_count_ok"] = 250 <= len(words) <= 500

    # Date format check (DD-MMM-YYYY)
    date_pattern = r"\d{2}-[A-Z]{3}-\d{4}"
    dates_found = re.findall(date_pattern, narrative)
    results["dates_found"] = len(dates_found)
    results["date_format_ok"] = len(dates_found) > 0

    # Check for lab values
    narrative_lower = narrative.lower()
    lab_mentions = 0
    for rec in crf.lb.records:
        if rec.LBTESTCD and rec.LBTESTCD.lower() in narrative_lower:
            lab_mentions += 1
        elif rec.LBTEST and rec.LBTEST.lower() in narrative_lower:
            lab_mentions += 1
    results["lab_mentions"] = lab_mentions

    # Hallucination check: verify key facts exist in input
    hallucination_flags = []
    # Check that age mentioned matches input
    age_matches = re.findall(r"(\d+)[- ]year", narrative)
    for age_str in age_matches:
        if int(age_str) != crf.dm.AGE:
            hallucination_flags.append(f"Age mismatch: narrative says {age_str}, input is {crf.dm.AGE}")
    results["hallucination_flags"] = hallucination_flags
    results["hallucination_count"] = len(hallucination_flags)

    # Check chronological order (if dates found)
    results["chronological_ok"] = True  # Would need more sophisticated analysis

    # ILD mention if applicable
    if sentinel_output and sentinel_output.ild_detected:
        ild_mentioned = any(
            term in narrative.lower()
            for term in ["ild", "interstitial", "ground-glass", "ggo", "pneumonitis"]
        )
        results["ild_mentioned"] = ild_mentioned

    return results


# ---------------------------------------------------------------------------
# C7/C8 Evaluation
# ---------------------------------------------------------------------------

def evaluate_c7(
    dechallenge_result: dict[str, Any],
    crf: CRFData,
) -> dict[str, Any]:
    """Evaluate C7 Dechallenge correctness."""
    results: dict[str, Any] = {}

    c7_answer = dechallenge_result.get("c7_answer", "")
    results["answer"] = c7_answer
    results["has_rationale"] = bool(dechallenge_result.get("c7_rationale", ""))

    # Determine expected answer
    aeacn = crf.ae.AEACN
    aeout = crf.ae.AEOUT

    if aeacn in ("DOSE NOT CHANGED", "NOT APPLICABLE"):
        expected = "Does not apply"
    elif aeacn in ("DRUG WITHDRAWN", "DRUG INTERRUPTED", "DOSE REDUCED"):
        if aeout in ("RECOVERED/RESOLVED", "RECOVERING/RESOLVING", "RECOVERED/RESOLVED WITH SEQUELAE"):
            expected = "Yes"
        elif aeout == "NOT RECOVERED/NOT RESOLVED":
            expected = "No"
        elif aeout == "FATAL":
            expected = "No"
        else:
            expected = "Unknown"
    elif aeacn == "UNKNOWN":
        expected = "Unknown"
    else:
        expected = "Unknown"

    results["expected"] = expected
    results["correct"] = c7_answer == expected

    return results


def evaluate_c8(
    rechallenge_result: dict[str, Any],
    crf: CRFData,
) -> dict[str, Any]:
    """Evaluate C8 Rechallenge correctness."""
    results: dict[str, Any] = {}

    c8_answer = rechallenge_result.get("c8_answer", "")
    results["answer"] = c8_answer
    results["has_rationale"] = bool(rechallenge_result.get("c8_rationale", ""))
    results["e2b_code"] = rechallenge_result.get("e2b_code")

    # If only 1 exposure period → Does not apply
    expected = "Does not apply" if len(crf.ec) <= 1 else None
    results["expected"] = expected
    results["correct"] = (c8_answer == expected) if expected else None

    return results


# ---------------------------------------------------------------------------
# E2B XML Evaluation
# ---------------------------------------------------------------------------

def evaluate_e2b_xml(xml_str: str) -> dict[str, Any]:
    """Validate E2B XML structure and schema."""
    results: dict[str, Any] = {}

    try:
        root = etree.fromstring(xml_str.encode("utf-8"))
        results["valid_xml"] = True
    except etree.XMLSyntaxError:
        results["valid_xml"] = False
        return results

    # XSD schema validation
    is_valid, schema_errors = validate_e2b_xml(xml_str)
    results["schema_valid"] = is_valid
    results["schema_errors"] = schema_errors

    # Check required elements
    required_elements = [
        "ichicsrmessageheader",
        "safetyreport",
    ]
    for elem_name in required_elements:
        found = root.find(f".//{elem_name}")
        results[f"has_{elem_name}"] = found is not None

    # Check patient section
    patient = root.find(".//patient")
    results["has_patient"] = patient is not None

    if patient is not None:
        results["has_reaction"] = patient.find("reaction") is not None
        results["has_drug"] = patient.find("drug") is not None
        results["has_summary"] = patient.find("summary") is not None

        # Check MedDRA coding
        reaction = patient.find("reaction")
        if reaction is not None:
            results["has_meddra_pt"] = reaction.find("reactionmeddrapt") is not None

        # Count lab tests
        tests = patient.findall("test")
        results["lab_test_count"] = len(tests)

        # Count drugs
        drugs = patient.findall("drug")
        results["drug_count"] = len(drugs)

    return results


# ---------------------------------------------------------------------------
# CM Separation Evaluation
# ---------------------------------------------------------------------------

def evaluate_cm_separation(
    medwatch: MedWatch3500A,
    crf: CRFData,
) -> dict[str, Any]:
    """Check that AE treatment medications are NOT in C9 concomitant meds.

    C9 should only contain baseline medications. AE treatment meds
    should appear in the B5 narrative but not in C9.
    """
    results: dict[str, Any] = {}

    baseline_meds, ae_treatment_meds = classify_cm_records(crf)
    c9_text = (medwatch.section_c.concomitant_meds or "").lower()

    results["baseline_count"] = len(baseline_meds)
    results["ae_treatment_count"] = len(ae_treatment_meds)

    # Check that no AE treatment drug name appears in C9
    leaked = []
    for rec in ae_treatment_meds:
        if rec.CMTRT.lower() in c9_text:
            leaked.append(rec.CMTRT)

    results["leaked_ae_meds"] = leaked
    results["c9_clean"] = len(leaked) == 0

    # Check that baseline meds ARE in C9
    missing_baseline = []
    for rec in baseline_meds:
        if rec.CMTRT.lower() not in c9_text:
            missing_baseline.append(rec.CMTRT)

    results["missing_baseline_meds"] = missing_baseline
    results["baseline_complete"] = len(missing_baseline) == 0

    return results


# ---------------------------------------------------------------------------
# Non-Serious Gate Evaluation
# ---------------------------------------------------------------------------

def evaluate_non_serious_gate(
    medwatch: MedWatch3500A,
    crf: CRFData,
) -> dict[str, Any]:
    """Verify non_serious_flag matches AESER field."""
    results: dict[str, Any] = {}

    expected_non_serious = (crf.ae.AESER == "N")
    results["aeser"] = crf.ae.AESER
    results["expected_non_serious"] = expected_non_serious
    results["actual_non_serious"] = medwatch.non_serious_flag
    results["correct"] = medwatch.non_serious_flag == expected_non_serious

    return results


# ---------------------------------------------------------------------------
# Narrative Quality Evaluation
# ---------------------------------------------------------------------------

def evaluate_narrative_quality(
    narrative: str,
    crf: CRFData,
) -> dict[str, Any]:
    """Evaluate narrative quality per clinical review feedback.

    Checks:
    1. Race/ethnicity NOT included (captured in Section A)
    2. "not specified" / "not available" NOT used
    3. "the subject" used (not "the patient")
    4. CRF field codes NOT exposed (AEACN=, AEOUT=, etc.)
    """
    results: dict[str, Any] = {}
    issues = []
    narrative_lower = narrative.lower()

    # 1. Race/ethnicity check
    #    Exclude false positives from medical terms (e.g., "white blood cell")
    medical_whitelist = [
        "white blood", "white cell", "white count",
    ]
    race = (crf.dm.RACE or "").lower()
    ethnicity = (crf.dm.ETHNIC or "").lower()
    race_found = False
    if race and len(race) > 3:
        # Find all occurrences and check they're not medical terms
        idx = 0
        while True:
            idx = narrative_lower.find(race, idx)
            if idx == -1:
                break
            context = narrative_lower[idx:idx + 30]
            if not any(wl in context for wl in medical_whitelist):
                race_found = True
                issues.append(f"Race '{crf.dm.RACE}' found in narrative")
                break
            idx += len(race)
    if ethnicity and "hispanic" in ethnicity:
        eth_variants = ["hispanic", "latino", "latina"]
        for v in eth_variants:
            if v in narrative_lower:
                race_found = True
                issues.append(f"Ethnicity term '{v}' found in narrative")
                break
    results["race_excluded"] = not race_found

    # 2. "not specified" variants check
    not_specified_patterns = [
        "not specified", "not available", "not reported",
        "not provided", "no data available", "n/a",
    ]
    not_specified_found = []
    for pat in not_specified_patterns:
        if pat in narrative_lower:
            not_specified_found.append(pat)
            issues.append(f"Phrase '{pat}' found in narrative")
    results["no_not_specified"] = len(not_specified_found) == 0
    results["not_specified_phrases"] = not_specified_found

    # 3. "the subject" vs "the patient" check
    subject_count = narrative_lower.count("the subject")
    patient_count = narrative_lower.count("the patient")
    results["subject_count"] = subject_count
    results["patient_count"] = patient_count
    results["uses_subject"] = subject_count > 0 or patient_count == 0
    if patient_count > 0:
        issues.append(f"'the patient' used {patient_count} times (should be 'the subject')")

    # 4. CRF field code check
    crf_codes = [
        "AEACN=", "AEOUT=", "AESEV=", "AESER=", "AEREL=",
        "AESDTH=", "AESLIFE=", "AESHOSP=", "AESDISAB=",
        "AEACN", "AEOUT", "AESEV", "DRUG WITHDRAWN",
        "DOSE NOT CHANGED", "DOSE REDUCED", "DRUG INTERRUPTED",
        "RECOVERED/RESOLVED", "NOT RECOVERED/NOT RESOLVED",
    ]
    crf_codes_found = []
    for code in crf_codes:
        if code in narrative:  # Case-sensitive for CRF codes
            crf_codes_found.append(code)
            issues.append(f"CRF code '{code}' exposed in narrative")
    results["no_crf_codes"] = len(crf_codes_found) == 0
    results["crf_codes_found"] = crf_codes_found

    results["issues"] = issues
    results["quality_pass"] = (
        results["race_excluded"]
        and results["no_not_specified"]
        and results["uses_subject"]
        and results["no_crf_codes"]
    )

    return results


# ---------------------------------------------------------------------------
# Investigator / Reporter Mapping Evaluation
# ---------------------------------------------------------------------------

def evaluate_investigator_mapping(
    medwatch: MedWatch3500A,
    crf: CRFData,
) -> dict[str, Any]:
    """Verify investigator info is mapped to Section E reporter fields."""
    results: dict[str, Any] = {}

    inv = crf.investigator
    e = medwatch.section_e

    results["has_investigator_data"] = bool(inv.name)
    results["reporter_name_populated"] = bool(e.reporter_name)
    results["reporter_name_matches"] = e.reporter_name == inv.name if inv.name else True
    results["reporter_address_populated"] = bool(e.reporter_address)
    results["reporter_phone_populated"] = bool(e.reporter_phone)
    results["reporter_email_populated"] = bool(e.reporter_email)

    results["correct"] = (
        results["reporter_name_matches"]
        and (not inv.name or results["reporter_name_populated"])
    )

    return results


# ---------------------------------------------------------------------------
# Hospitalization Date Evaluation
# ---------------------------------------------------------------------------

def evaluate_hospitalization_dates(
    medwatch: MedWatch3500A,
    crf: CRFData,
) -> dict[str, Any]:
    """Verify hospitalization dates are correctly mapped."""
    results: dict[str, Any] = {}

    b = medwatch.section_b
    ae = crf.ae

    results["aeshosp"] = ae.AESHOSP
    results["has_hosp_start"] = b.hospitalization_start is not None
    results["has_hosp_end"] = b.hospitalization_end is not None

    if ae.AESHOSP == "Y":
        # If hospitalized, start date should be set (if available in CRF)
        results["start_matches"] = b.hospitalization_start == ae.AEHOSPSTDAT
        results["end_matches"] = b.hospitalization_end == ae.AEHOSPENDAT
        results["correct"] = results["start_matches"] and results["end_matches"]
    else:
        # Not hospitalized — both should be None
        results["correct"] = (
            b.hospitalization_start is None and b.hospitalization_end is None
        )

    return results


# ---------------------------------------------------------------------------
# CTCAE Grade Mapping Evaluation
# ---------------------------------------------------------------------------

def evaluate_ctcae_grade_mapping(
    crf: CRFData,
    narrative: Optional[str] = None,
) -> dict[str, Any]:
    """Check AETOXGR presence and narrative grade mention."""
    results: dict[str, Any] = {}

    results["aetoxgr"] = crf.ae.AETOXGR
    results["has_aetoxgr"] = crf.ae.AETOXGR is not None

    if narrative and crf.ae.AETOXGR:
        grade_str = crf.ae.AETOXGR
        grade_mentioned = (
            f"grade {grade_str}" in narrative.lower()
            or f"grade-{grade_str}" in narrative.lower()
            or f"ctcae grade {grade_str}" in narrative.lower()
            or f"ctcae {grade_str}" in narrative.lower()
        )
        results["grade_in_narrative"] = grade_mentioned
    else:
        results["grade_in_narrative"] = None

    return results


# ---------------------------------------------------------------------------
# Imaging / PFT Domain Evaluation
# ---------------------------------------------------------------------------

def evaluate_imaging_pft_domains(
    crf: CRFData,
) -> dict[str, Any]:
    """Check imaging and PFT domain data presence and format."""
    results: dict[str, Any] = {}

    results["imaging_record_count"] = len(crf.imaging.records)
    results["pft_record_count"] = len(crf.pft.records)

    # Validate imaging records have required fields
    img_valid = True
    for r in crf.imaging.records:
        if not r.IMG_MODALITY or not r.IMG_REGION or not r.IMG_FINDINGS:
            img_valid = False
            break
    results["imaging_format_ok"] = img_valid

    # Validate PFT records
    pft_valid = True
    pft_baseline_count = 0
    pft_postae_count = 0
    for r in crf.pft.records:
        if not r.PFT_TESTCD or not r.PFT_TEST or r.PFT_RESULT is None:
            pft_valid = False
            break
        if r.PFT_BLFL == "Y":
            pft_baseline_count += 1
        else:
            pft_postae_count += 1
    results["pft_format_ok"] = pft_valid
    results["pft_baseline_count"] = pft_baseline_count
    results["pft_postae_count"] = pft_postae_count

    return results


# ---------------------------------------------------------------------------
# Microbiology / Consultation Domain Evaluation
# ---------------------------------------------------------------------------

def evaluate_microbiology_consultation_domains(
    crf: CRFData,
) -> dict[str, Any]:
    """Check microbiology and consultation domain data presence and format."""
    results: dict[str, Any] = {}

    results["microbiology_record_count"] = len(crf.microbiology.records)
    results["consultation_record_count"] = len(crf.consultation.records)

    # Validate microbiology records
    mb_valid = True
    for r in crf.microbiology.records:
        if not r.MB_SPECIMEN or not r.MB_TEST or not r.MB_RESULT:
            mb_valid = False
            break
    results["microbiology_format_ok"] = mb_valid

    # Validate consultation records
    consult_valid = True
    for r in crf.consultation.records:
        if not r.CONSULT_SPECIALTY or not r.CONSULT_IMPRESSION:
            consult_valid = False
            break
    results["consultation_format_ok"] = consult_valid

    return results


# ---------------------------------------------------------------------------
# Full Pipeline Test (with AI)
# ---------------------------------------------------------------------------

def test_full_pipeline(
    patient_id: str,
    crf: CRFData,
    sentinel_output: Optional[SentinelOutput],
    settings: Settings,
) -> dict[str, Any]:
    """Run full workflow including AI components."""
    from app.agents.doc.agent import run_doc_workflow

    start_time = time.time()
    try:
        result = run_doc_workflow(crf, settings, sentinel_output)
        elapsed = time.time() - start_time

        # Non-serious AE gate — workflow returns early
        if result.get("non_serious"):
            logger.info(
                "  Non-serious AE for %s (%s) — skipped MedWatch/E2B generation",
                patient_id, result.get("aeterm"),
            )
            return {
                "success": True,
                "non_serious": True,
                "elapsed_seconds": round(elapsed, 2),
                "subjid": result.get("subjid"),
                "aeterm": result.get("aeterm"),
                "reason": result.get("reason"),
            }

        medwatch = result["medwatch"]
        e2b_xml = result["e2b_xml"]
        meddra_code = result["meddra_code"]

        return {
            "success": True,
            "elapsed_seconds": round(elapsed, 2),
            "medwatch": medwatch,
            "e2b_xml": e2b_xml,
            "meddra_code": meddra_code,
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Full pipeline failed for {patient_id}: {e}")
        return {
            "success": False,
            "elapsed_seconds": round(elapsed, 2),
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------

def generate_evaluation_report(
    patient_id: str,
    run_results: list[dict[str, Any]],
    crf: CRFData,
    sentinel_output: Optional[SentinelOutput],
) -> dict[str, Any]:
    """Generate evaluation report for a patient across multiple runs."""
    report: dict[str, Any] = {
        "patient_id": patient_id,
        "num_runs": len(run_results),
        "runs": [],
    }

    for i, result in enumerate(run_results):
        run_report: dict[str, Any] = {"run": i + 1, "success": result.get("success", False)}

        if not result.get("success"):
            run_report["error"] = result.get("error", "Unknown error")
            report["runs"].append(run_report)
            continue

        # Non-serious AE — no MedWatch/E2B to evaluate
        if result.get("non_serious"):
            run_report["non_serious"] = True
            run_report["aeterm"] = result.get("aeterm")
            run_report["reason"] = result.get("reason")
            run_report["elapsed_seconds"] = result.get("elapsed_seconds", 0)
            report["runs"].append(run_report)
            continue

        medwatch = result["medwatch"]
        e2b_xml = result["e2b_xml"]

        # Completeness
        run_report["completeness"] = evaluate_medwatch_completeness(medwatch)

        # Narrative evaluation
        narrative = medwatch.section_b.narrative
        if narrative:
            run_report["narrative"] = evaluate_narrative(narrative, crf, sentinel_output)
            run_report["narrative_quality"] = evaluate_narrative_quality(narrative, crf)

        # CM separation
        run_report["cm_separation"] = evaluate_cm_separation(medwatch, crf)

        # Non-serious gate
        run_report["non_serious_gate"] = evaluate_non_serious_gate(medwatch, crf)

        # E2B evaluation
        if e2b_xml:
            run_report["e2b"] = evaluate_e2b_xml(e2b_xml)

        run_report["elapsed_seconds"] = result.get("elapsed_seconds", 0)
        report["runs"].append(run_report)

    # Summary statistics
    successful_runs = [r for r in report["runs"] if r.get("success")]
    if successful_runs:
        report["summary"] = {
            "success_rate": f"{len(successful_runs)}/{len(run_results)}",
            "avg_elapsed": round(
                sum(r.get("elapsed_seconds", 0) for r in successful_runs) / len(successful_runs), 2
            ),
        }

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def save_outputs(
    patient_id: str,
    medwatch: MedWatch3500A,
    e2b_xml: str,
    meddra_code: MedDRACode,
) -> None:
    """Save outputs (JSON + XML + PDF) to the output/ directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # MedWatch 3500A JSON
    medwatch_path = OUTPUT_DIR / f"{patient_id}_medwatch.json"
    medwatch_path.write_text(medwatch.model_dump_json(indent=2))
    logger.info(f"  Saved: {medwatch_path}")

    # E2B XML
    e2b_path = OUTPUT_DIR / f"{patient_id}_e2b.xml"
    e2b_path.write_text(e2b_xml)
    logger.info(f"  Saved: {e2b_path}")

    # MedWatch 3500A PDF
    try:
        pdf_path = OUTPUT_DIR / f"{patient_id}_medwatch.pdf"
        generate_medwatch_pdf(medwatch, output_path=str(pdf_path))
        logger.info(f"  Saved: {pdf_path}")
    except Exception as e:
        logger.warning(f"  PDF generation failed: {e}")


def print_summary_table(reports: list[dict[str, Any]]) -> None:
    """Print evaluation summary table."""
    print("\n" + "=" * 80)
    print("DOC AGENT EVALUATION SUMMARY")
    print("=" * 80)

    for report in reports:
        pid = report["patient_id"]
        print(f"\n--- {pid} ---")
        print(f"  Runs: {report['num_runs']}")

        if "summary" in report:
            print(f"  Success rate: {report['summary']['success_rate']}")
            print(f"  Avg latency: {report['summary']['avg_elapsed']}s")

        for run in report["runs"]:
            run_num = run["run"]
            if not run.get("success"):
                print(f"  Run {run_num}: FAILED - {run.get('error', 'unknown')}")
                continue

            compl = run.get("completeness", {})
            print(f"  Run {run_num}: {compl.get('completeness', 0)}% complete "
                  f"({compl.get('filled', 0)}/{compl.get('total', 0)} fields), "
                  f"{run.get('elapsed_seconds', 0)}s")

            if "narrative" in run:
                narr = run["narrative"]
                wc = narr.get("word_count", 0)
                wc_ok = "OK" if narr.get("word_count_ok") else "WARN"
                hall = narr.get("hallucination_count", 0)
                print(f"    Narrative: {wc} words [{wc_ok}], "
                      f"{narr.get('dates_found', 0)} dates, "
                      f"hallucinations: {hall}")

            if "narrative_quality" in run:
                nq = run["narrative_quality"]
                qpass = "PASS" if nq.get("quality_pass") else "FAIL"
                race_ok = "OK" if nq.get("race_excluded") else "FAIL"
                ns_ok = "OK" if nq.get("no_not_specified") else "FAIL"
                subj_ok = "OK" if nq.get("uses_subject") else "FAIL"
                crf_ok = "OK" if nq.get("no_crf_codes") else "FAIL"
                print(f"    Quality [{qpass}]: race={race_ok}, "
                      f"no_not_specified={ns_ok}, subject={subj_ok}, crf_codes={crf_ok}")
                if nq.get("issues"):
                    for issue in nq["issues"][:3]:
                        print(f"      Issue: {issue}")

            if "cm_separation" in run:
                cms = run["cm_separation"]
                c9_ok = "OK" if cms.get("c9_clean") else "FAIL"
                bl_ok = "OK" if cms.get("baseline_complete") else "WARN"
                print(f"    CM Separation: C9 clean={c9_ok}, baseline={bl_ok} "
                      f"(baseline: {cms.get('baseline_count', 0)}, "
                      f"AE tx: {cms.get('ae_treatment_count', 0)})")
                if cms.get("leaked_ae_meds"):
                    print(f"      Leaked: {cms['leaked_ae_meds']}")

            if "non_serious_gate" in run:
                nsg = run["non_serious_gate"]
                ns_ok = "OK" if nsg.get("correct") else "FAIL"
                print(f"    Non-serious gate: {ns_ok} "
                      f"(AESER={nsg.get('aeser')}, "
                      f"flag={nsg.get('actual_non_serious')})")

            if "e2b" in run:
                e2b = run["e2b"]
                xml_ok = "OK" if e2b.get("valid_xml") else "FAIL"
                schema_ok = "OK" if e2b.get("schema_valid") else "FAIL"
                meddra_ok = "OK" if e2b.get("has_meddra_pt") else "MISSING"
                print(f"    E2B XML: {xml_ok}, Schema: {schema_ok}, MedDRA PT: {meddra_ok}, "
                      f"Labs: {e2b.get('lab_test_count', 0)}, "
                      f"Drugs: {e2b.get('drug_count', 0)}")
                if e2b.get("schema_errors"):
                    for err in e2b["schema_errors"][:3]:
                        print(f"      Schema error: {err}")

    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Doc Agent E2E Test")
    parser.add_argument("patients", nargs="*", help="Patient JSON files or IDs (default: all in data/patients/)")
    parser.add_argument("--runs", type=int, default=3, help="Repetitions per case")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI steps (deterministic only)")
    args = parser.parse_args()

    settings = Settings()

    if args.patients:
        patients = []
        for p in args.patients:
            path = Path(p)
            if path.exists():
                patients.append(path.stem)
            else:
                patients.append(p)
    else:
        patients = sorted(f.stem for f in DATA_DIR.glob("PT-*.json"))

    all_reports = []

    for patient_id in patients:
        logger.info(f"\n{'='*60}")
        logger.info(f"Testing patient: {patient_id}")
        logger.info(f"{'='*60}")

        crf, sentinel_output = load_patient(patient_id)

        if args.no_ai:
            # Deterministic pipeline test only
            logger.info("Running deterministic pipeline test...")
            det_result = test_deterministic_pipeline(crf, sentinel_output, settings)

            if det_result["mapping_ok"]:
                logger.info("  CRF → MedWatch mapping: OK")
                compl = evaluate_medwatch_completeness(det_result["medwatch"])
                logger.info(f"  Completeness: {compl['completeness']}% "
                          f"({compl['filled']}/{compl['total']})")
                if compl["empty_fields"]:
                    logger.info(f"  Empty fields: {', '.join(compl['empty_fields'][:5])}...")
            else:
                logger.error("  CRF → MedWatch mapping: FAILED")

            # CM separation
            if "cm_separation" in det_result:
                cms = det_result["cm_separation"]
                c9_ok = "OK" if cms["c9_clean"] else "FAIL"
                logger.info(f"  CM Separation: C9 clean={c9_ok} "
                          f"(baseline: {cms['baseline_count']}, AE tx: {cms['ae_treatment_count']})")
                if cms.get("leaked_ae_meds"):
                    logger.error(f"    Leaked AE meds in C9: {cms['leaked_ae_meds']}")

            # Non-serious gate
            if "non_serious_gate" in det_result:
                nsg = det_result["non_serious_gate"]
                ns_ok = "OK" if nsg["correct"] else "FAIL"
                logger.info(f"  Non-serious gate: {ns_ok} (AESER={nsg['aeser']}, flag={nsg['actual_non_serious']})")

            # Investigator mapping
            if "investigator_mapping" in det_result:
                inv = det_result["investigator_mapping"]
                inv_ok = "OK" if inv["correct"] else "FAIL"
                logger.info(f"  Investigator mapping: {inv_ok} "
                          f"(name={inv['reporter_name_populated']}, "
                          f"phone={inv['reporter_phone_populated']})")

            # Hospitalization dates
            if "hospitalization_dates" in det_result:
                hd = det_result["hospitalization_dates"]
                hd_ok = "OK" if hd["correct"] else "FAIL"
                logger.info(f"  Hospitalization dates: {hd_ok} "
                          f"(AESHOSP={hd['aeshosp']}, "
                          f"start={hd['has_hosp_start']}, end={hd['has_hosp_end']})")

            # CTCAE Grade
            if "ctcae_grade" in det_result:
                cg = det_result["ctcae_grade"]
                cg_ok = "OK" if cg["has_aetoxgr"] else "MISSING"
                logger.info(f"  CTCAE Grade: {cg_ok} (AETOXGR={cg['aetoxgr']})")

            # Imaging / PFT
            if "imaging_pft" in det_result:
                ip = det_result["imaging_pft"]
                img_ok = "OK" if ip["imaging_format_ok"] else "FAIL"
                pft_ok = "OK" if ip["pft_format_ok"] else "FAIL"
                logger.info(
                    f"  Imaging: {ip['imaging_record_count']} records [{img_ok}], "
                    f"PFT: {ip['pft_record_count']} records [{pft_ok}] "
                    f"(baseline: {ip['pft_baseline_count']}, post-AE: {ip['pft_postae_count']})"
                )

            # Microbiology / Consultation
            if "microbiology_consultation" in det_result:
                mc = det_result["microbiology_consultation"]
                mb_ok = "OK" if mc["microbiology_format_ok"] else "FAIL"
                co_ok = "OK" if mc["consultation_format_ok"] else "FAIL"
                logger.info(
                    f"  Microbiology: {mc['microbiology_record_count']} records [{mb_ok}], "
                    f"Consultation: {mc['consultation_record_count']} records [{co_ok}]"
                )

            if det_result.get("meddra_ok"):
                mc = det_result["meddra_code"]
                logger.info(f"  MedDRA: {mc.pt} ({mc.pt_code}) — SOC: {mc.soc}")
            else:
                logger.warning("  MedDRA: lookup miss (no exact match)")

            if det_result.get("e2b_ok"):
                logger.info(f"  E2B XML: Generated ({len(det_result['e2b_xml'])} chars)")
                e2b_eval = evaluate_e2b_xml(det_result["e2b_xml"])
                logger.info(f"  E2B Valid XML: {e2b_eval['valid_xml']}")
            else:
                logger.error("  E2B XML: FAILED")

            if det_result.get("xml_valid"):
                logger.info("  XML schema validation: PASSED")
            else:
                logger.error("  XML schema validation: FAILED")
                for err in det_result.get("errors", []):
                    if "schema" in err.lower():
                        logger.error(f"    {err}")

            # Save outputs
            if det_result["mapping_ok"] and det_result.get("e2b_ok"):
                save_outputs(
                    patient_id,
                    det_result["medwatch"],
                    det_result["e2b_xml"],
                    det_result.get("meddra_code", MedDRACode(pt=crf.ae.AETERM)),
                )

            for err in det_result.get("errors", []):
                logger.error(f"  Error: {err}")

        else:
            # Full pipeline with AI
            run_results = []
            for run_num in range(1, args.runs + 1):
                logger.info(f"  Run {run_num}/{args.runs}...")
                result = test_full_pipeline(patient_id, crf, sentinel_output, settings)
                run_results.append(result)

                # Non-serious AE — skip save_outputs
                if result.get("non_serious"):
                    logger.info(f"  Non-serious AE — no MedWatch/E2B/PDF generated")
                    continue

                if result.get("success") and result.get("medwatch"):
                    save_outputs(
                        patient_id,
                        result["medwatch"],
                        result.get("e2b_xml", ""),
                        result.get("meddra_code", MedDRACode(pt=crf.ae.AETERM)),
                    )

            report = generate_evaluation_report(patient_id, run_results, crf, sentinel_output)
            all_reports.append(report)

    if not args.no_ai and all_reports:
        # Save evaluation report
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_path = OUTPUT_DIR / "evaluation_report.json"
        report_path.write_text(json.dumps(all_reports, indent=2, default=str))
        logger.info(f"\nEvaluation report saved: {report_path}")

        print_summary_table(all_reports)

    logger.info("\nDone.")


if __name__ == "__main__":
    main()
