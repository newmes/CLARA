"""
CRF Aggregator — CDISC domain aggregation from simulation JSONL + patient JSON.

Reads JSONL simulation data and patient profile JSON, returns domain-specific
row lists suitable for CRF table display and Excel export.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ─── Column definitions per domain ─────────────────────────────────────

DM_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "BRTHDAT", "label": "Date of Birth"},
    {"key": "AGE", "label": "Age"},
    {"key": "AGEU", "label": "Age Unit"},
    {"key": "SEX", "label": "Sex"},
    {"key": "RACE", "label": "Race"},
    {"key": "RACEOTH", "label": "Race Other"},
    {"key": "ETHNIC", "label": "Ethnicity"},
]

MH_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "MHTERM", "label": "Condition"},
    {"key": "MHDAT", "label": "Collection Date"},
    {"key": "MHSTDAT", "label": "Start Date"},
    {"key": "MHONGO", "label": "Ongoing"},
    {"key": "MHENDAT", "label": "End Date"},
]

AE_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "AETERM", "label": "Adverse Event"},
    {"key": "AESTDAT", "label": "Start Day"},
    {"key": "AEONGO", "label": "Ongoing"},
    {"key": "AEENDAT", "label": "End Day"},
    {"key": "AESEV", "label": "Severity"},
    {"key": "_grade", "label": "Grade"},
    {"key": "AESER", "label": "Serious"},
    {"key": "AESDTH", "label": "Results in Death"},
    {"key": "AESLIFE", "label": "Life-Threatening"},
    {"key": "AESHOSP", "label": "Hospitalization"},
    {"key": "AESDISAB", "label": "Disability"},
    {"key": "AESCONG", "label": "Congenital Anomaly"},
    {"key": "AESMIE", "label": "Other Medically Important"},
    {"key": "AEREL", "label": "Related"},
    {"key": "AEACN", "label": "Action Taken"},
    {"key": "AEACNOTH", "label": "Other Action"},
    {"key": "AEOUT", "label": "Outcome"},
    {"key": "_status", "label": "Status"},
    {"key": "_days_active", "label": "Days Active"},
]

AE_HR_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "ae", "label": "Adverse Event"},
    {"key": "grade", "label": "Grade"},
    {"key": "onset_day", "label": "Onset Day"},
    {"key": "_onset_cycle", "label": "Onset Cycle"},
    {"key": "detected_day", "label": "Detected Day"},
    {"key": "detection_delay", "label": "Detection Delay"},
    {"key": "channel", "label": "Detection Channel"},
    {"key": "status", "label": "Status"},
]

EC_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "ECREFID", "label": "Drug"},
    {"key": "ECSTDAT", "label": "Start Day"},
    {"key": "ECENDAT", "label": "End Day"},
    {"key": "ECDSTXT", "label": "Dose"},
    {"key": "ECDOSU", "label": "Dose Unit"},
    {"key": "ECDOSFRQ", "label": "Frequency"},
    {"key": "ECROUTE", "label": "Route"},
    {"key": "ECDOSADJ", "label": "Dose Adjusted"},
    {"key": "ECADJ", "label": "Adjustment Reason"},
    {"key": "ECCINTD", "label": "Interruption Duration"},
    {"key": "ECCINTDU", "label": "Duration Unit"},
    {"key": "ECTRTCMP", "label": "Completed"},
    {"key": "_dose_level", "label": "Dose Level"},
]

CM_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "CMTRT", "label": "Medication"},
    {"key": "CMINDC", "label": "Indication"},
    {"key": "CMDSTXT", "label": "Dose"},
    {"key": "CMDOSU", "label": "Dose Unit"},
    {"key": "DOSUO", "label": "Dose Unit Other"},
    {"key": "CMDOSFRM", "label": "Form"},
    {"key": "DOSFRMO", "label": "Form Other"},
    {"key": "CMDOSFRQ", "label": "Frequency"},
    {"key": "DOSFRQO", "label": "Frequency Other"},
    {"key": "CMROUTE", "label": "Route"},
    {"key": "ROUTEO", "label": "Route Other"},
    {"key": "CMSTDAT", "label": "Start Day"},
    {"key": "CMONGO", "label": "Ongoing"},
    {"key": "CMENDAT", "label": "End Day"},
    {"key": "_baseline", "label": "Baseline"},
]

VS_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "SYSBP", "label": "SBP (mmHg)"},
    {"key": "DIABP", "label": "DBP (mmHg)"},
    {"key": "PULSE", "label": "HR (bpm)"},
    {"key": "TEMP", "label": "Temp (C)"},
    {"key": "RESP", "label": "RR (/min)"},
    {"key": "HEIGHT", "label": "Height"},
    {"key": "WEIGHT", "label": "Weight (kg)"},
    {"key": "SpO2", "label": "SpO2 (%)"},
]

VS_HR_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "SBP", "label": "SBP (mmHg)"},
    {"key": "DBP", "label": "DBP (mmHg)"},
    {"key": "HR", "label": "HR (bpm)"},
    {"key": "BT", "label": "Temp (C)"},
    {"key": "RR", "label": "RR (/min)"},
    {"key": "height_cm", "label": "Height (cm)"},
    {"key": "weight_kg", "label": "Weight (kg)"},
    {"key": "SpO2", "label": "SpO2 (%)"},
    {"key": "stale_days", "label": "Stale Days"},
]

LB_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "test_name", "label": "Test"},
    {"key": "LBORRES", "label": "Result"},
    {"key": "LBORRESU", "label": "Unit"},
    {"key": "LBCAT", "label": "Category"},
    {"key": "_trend", "label": "Trend"},
]

LB_HR_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "test_name", "label": "Test"},
    {"key": "value", "label": "Result"},
    {"key": "unit", "label": "Unit"},
    {"key": "trend", "label": "Trend"},
    {"key": "stale_days", "label": "Stale Days"},
]

DS_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "DSDECOD", "label": "Disposition"},
    {"key": "DSTERM", "label": "Description"},
    {"key": "DSSTDAT", "label": "Disposition Date"},
]

DD_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "DTHDAT", "label": "Date of Death"},
    {"key": "DDDECOD", "label": "Cause of Death"},
    {"key": "DDTERM", "label": "Description"},
    {"key": "PRCDTH_DDORRES", "label": "Primary Cause"},
    {"key": "AUTOPIND_DDORRES", "label": "Autopsy Performed"},
]

TU_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "TULNKID", "label": "Lesion Link ID"},
    {"key": "TULOC", "label": "Location"},
    {"key": "TULAT", "label": "Laterality"},
    {"key": "TUDIR", "label": "Direction"},
    {"key": "TULOCDTL", "label": "Location Detail"},
    {"key": "TUMETHOD", "label": "Method"},
    {"key": "TUDAT", "label": "Assessment Date"},
    {"key": "TUEVAL", "label": "Evaluator"},
    {"key": "TUEVALID", "label": "Evaluator ID"},
    {"key": "TRORRES", "label": "Target Lesion Result"},
    {"key": "TRORRESU", "label": "Result Unit"},
    {"key": "TRSTAT", "label": "Status"},
    {"key": "TRREASND", "label": "Reason Not Done"},
    {"key": "TURESULT", "label": "Result"},
]

RS_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "RSCAT", "label": "Assessment Category"},
    {"key": "RSEVAL", "label": "Evaluator"},
    {"key": "RSEVALID", "label": "Evaluator ID"},
    {"key": "TRGRESP_RSORRES", "label": "Target Response"},
    {"key": "NTRGRESP_RSORRES", "label": "Non-Target Response"},
    {"key": "OVRLRESP_RSORRES", "label": "Overall Response"},
    {"key": "BESTRESP_RSORRES", "label": "Best Overall Response"},
    {"key": "RSRESULT", "label": "Response"},
    {"key": "RSTESTCD", "label": "Test Code"},
]

PE_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "PEPERF", "label": "Performed"},
    {"key": "PEDAT", "label": "Exam Date"},
]

EG_COLUMNS = [
    {"key": "patient_id", "label": "Subject"},
    {"key": "day", "label": "Day"},
    {"key": "_cycle", "label": "Cycle"},
    {"key": "EGPERF", "label": "Performed"},
    {"key": "EGREFID", "label": "Reference ID"},
    {"key": "EGMETHOD", "label": "Method"},
    {"key": "EGPOS", "label": "Position"},
    {"key": "EGDAT", "label": "ECG Date"},
]

DOMAIN_COLUMNS = {
    "DM": DM_COLUMNS,
    "MH": MH_COLUMNS,
    "AE": AE_COLUMNS,
    "AE_HR": AE_HR_COLUMNS,
    "EC": EC_COLUMNS,
    "CM": CM_COLUMNS,
    "VS": VS_COLUMNS,
    "VS_HR": VS_HR_COLUMNS,
    "LB": LB_COLUMNS,
    "LB_HR": LB_HR_COLUMNS,
    "DS": DS_COLUMNS,
    "DD": DD_COLUMNS,
    "TU": TU_COLUMNS,
    "RS": RS_COLUMNS,
    "PE": PE_COLUMNS,
    "EG": EG_COLUMNS,
}

# Domain display labels (code → full name)
DOMAIN_LABELS = {
    "AE": "AE — Adverse Events",
    "EC": "EC — Exposures",
    "CM": "CM — Concomitant Meds",
    "LB": "LB — Labs",
    "VS": "VS — Vital Signs",
    "DM": "DM — Demographics",
    "MH": "MH — Medical History",
    "DS": "DS — Disposition",
    "DD": "DD — Death Details",
    "TU": "TU — Tumor",
    "RS": "RS — RECIST Response",
    "PE": "PE — Physical Exam",
    "EG": "EG — ECG",
}


# ─── Internal helpers ───────────────────────────────────────────────────

def _iter_jsonl(run_path: Path, patient_id: str, mode: str = "natural"):
    """Yield parsed JSON records from a simulation JSONL file."""
    fpath = run_path / "simulations" / f"{patient_id}_{mode}.jsonl"
    if not fpath.exists():
        return
    with open(fpath, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _load_patient_json(run_path: Path, patient_id: str) -> dict | None:
    """Load patient profile JSON."""
    fpath = run_path / "patients" / f"{patient_id}.json"
    if not fpath.exists():
        return None
    with open(fpath, encoding="utf-8") as fh:
        return json.load(fh)


def _list_patient_ids(run_path: Path) -> list[str]:
    """List available patient IDs from patients/ directory."""
    patients_dir = run_path / "patients"
    if not patients_dir.exists():
        return []
    return sorted(
        f.stem for f in patients_dir.glob("PT-*.json")
    )


def _resolve_patients(run_path: Path, patient_ids: list[str] | None) -> list[str]:
    """Resolve patient list: use provided or discover all."""
    if patient_ids:
        return patient_ids
    return _list_patient_ids(run_path)


def _fmt_cycle(cycle, cycle_day) -> str | None:
    """Format cycle/cycle_day as 'C2D8' string."""
    if cycle is None or cycle_day is None:
        return None
    return f"C{cycle}D{cycle_day}"


def _paginate(rows: list[dict], page: int = 1, per_page: int = 50) -> tuple[list[dict], int]:
    """Slice rows for pagination. Returns (page_rows, total_count)."""
    total = len(rows)
    if per_page <= 0:
        return rows, total
    start = (page - 1) * per_page
    end = start + per_page
    return rows[start:end], total


# ─── Domain aggregation functions ───────────────────────────────────────

def aggregate_dm(
    run_path: Path,
    patient_ids: list[str] | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """DM domain: 1 row per patient from patient profile JSON."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        profile = _load_patient_json(run_path, pid)
        if not profile:
            continue
        dm = profile.get("DM", {})
        rows.append({
            "patient_id": pid,
            "BRTHDAT": dm.get("BRTHDAT"),
            "AGE": dm.get("AGE"),
            "AGEU": dm.get("AGEU", "YEARS"),
            "SEX": dm.get("SEX"),
            "RACE": dm.get("RACE"),
            "RACEOTH": dm.get("RACEOTH"),
            "ETHNIC": dm.get("ETHNIC"),
        })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, DM_COLUMNS


def aggregate_mh(
    run_path: Path,
    patient_ids: list[str] | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """MH domain: 1 row per medical history item from patient profile."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        profile = _load_patient_json(run_path, pid)
        if not profile:
            continue
        for mh in profile.get("MH", []):
            rows.append({
                "patient_id": pid,
                "MHTERM": mh.get("MHTERM"),
                "MHDAT": mh.get("MHDAT"),
                "MHSTDAT": mh.get("MHSTDAT"),
                "MHONGO": mh.get("MHONGO"),
                "MHENDAT": mh.get("MHENDAT"),
            })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, MH_COLUMNS


def aggregate_ae(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    source: str = "hr",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """AE domain: deduplicated by (patient_id, AETERM, AESTDAT), latest state.

    source="gt": from JSONL AE array (ground truth)
    source="hr": from hospital_record.objective.active_aes (hospital record)
    """
    patients = _resolve_patients(run_path, patient_ids)

    if source == "hr":
        return _aggregate_ae_hr(run_path, patients, mode, page, per_page)

    rows_map: dict[tuple, dict] = {}  # (pid, AETERM, AESTDAT) -> latest row
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            for ae in record.get("AE", []):
                key = (pid, ae.get("AETERM"), ae.get("AESTDAT"))
                rows_map[key] = {
                    "patient_id": pid,
                    "AETERM": ae.get("AETERM"),
                    "AESTDAT": ae.get("AESTDAT"),
                    "AEONGO": ae.get("AEONGO"),
                    "AEENDAT": ae.get("AEENDAT"),
                    "AESEV": ae.get("AESEV"),
                    "_grade": ae.get("_grade") or ae.get("AETOXGR"),
                    "AESER": ae.get("AESER"),
                    "AESDTH": ae.get("AESDTH"),
                    "AESLIFE": ae.get("AESLIFE"),
                    "AESHOSP": ae.get("AESHOSP"),
                    "AESDISAB": ae.get("AESDISAB"),
                    "AESCONG": ae.get("AESCONG"),
                    "AESMIE": ae.get("AESMIE"),
                    "AEREL": ae.get("AEREL"),
                    "AEACN": ae.get("AEACN"),
                    "AEACNOTH": ae.get("AEACNOTH"),
                    "AEOUT": ae.get("AEOUT"),
                    "_status": ae.get("_status"),
                    "_days_active": ae.get("_days_active"),
                }
    rows = sorted(rows_map.values(), key=lambda r: (r["patient_id"], r["AESTDAT"] or 0))
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, AE_COLUMNS


def _aggregate_ae_hr(
    run_path: Path,
    patients: list[str],
    mode: str,
    page: int,
    per_page: int,
) -> tuple[list[dict], int, list[dict]]:
    """AE from hospital_record.objective.active_aes — deduplicated."""
    rows_map: dict[tuple, dict] = {}
    for pid in patients:
        # Build day→cycle map for onset cycle lookup
        day_cycle: dict[int, str] = {}
        for record in _iter_jsonl(run_path, pid, mode):
            d = record.get("day")
            if d is not None:
                day_cycle[d] = _fmt_cycle(record.get("cycle"), record.get("cycle_day"))
            hr = record.get("hospital_record", {})
            hr_aes = hr.get("objective", {}).get("active_aes", [])
            day = d or hr.get("day")
            for ae in hr_aes:
                ae_name = ae.get("ae") or ae.get("AETERM", "")
                onset = ae.get("onset_day") or ae.get("AESTDAT")
                key = (pid, ae_name, onset)
                rows_map[key] = {
                    "patient_id": pid,
                    "ae": ae_name,
                    "grade": ae.get("grade") or ae.get("_grade"),
                    "onset_day": onset,
                    "_onset_cycle": None,  # filled below
                    "detected_day": ae.get("detected_day") or day,
                    "detection_delay": ae.get("detection_delay"),
                    "channel": ae.get("channel", ""),
                    "status": ae.get("status", ""),
                }
        # Fill onset cycle from day→cycle map
        for row in rows_map.values():
            if row["patient_id"] == pid and row["onset_day"]:
                row["_onset_cycle"] = day_cycle.get(row["onset_day"])
    rows = sorted(rows_map.values(), key=lambda r: (r["patient_id"], r["onset_day"] or 0))
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, AE_HR_COLUMNS


def aggregate_ec(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """EC domain: 1 row per drug exposure record per day."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            day = record.get("day")
            cycle_display = _fmt_cycle(record.get("cycle"), record.get("cycle_day"))
            for ec in record.get("EC", []):
                rows.append({
                    "patient_id": pid,
                    "day": day,
                    "_cycle": cycle_display,
                    "ECREFID": ec.get("ECREFID"),
                    "ECSTDAT": ec.get("ECSTDAT"),
                    "ECENDAT": ec.get("ECENDAT"),
                    "ECDSTXT": ec.get("ECDSTXT"),
                    "ECDOSU": ec.get("ECDOSU"),
                    "ECDOSFRQ": ec.get("ECDOSFRQ"),
                    "ECROUTE": ec.get("ECROUTE"),
                    "ECDOSADJ": ec.get("ECDOSADJ"),
                    "ECADJ": ec.get("ECADJ"),
                    "ECCINTD": ec.get("ECCINTD"),
                    "ECCINTDU": ec.get("ECCINTDU"),
                    "ECTRTCMP": ec.get("ECTRTCMP"),
                    "_dose_level": ec.get("_dose_level"),
                })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, EC_COLUMNS


def aggregate_cm(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """CM domain: deduplicated by (patient_id, drug name), latest state."""
    patients = _resolve_patients(run_path, patient_ids)
    rows_map: dict[tuple, dict] = {}
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            for cm in record.get("CM", []):
                drug = cm.get("CMTRT", "")
                key = (pid, drug)
                rows_map[key] = {
                    "patient_id": pid,
                    "CMTRT": drug,
                    "CMINDC": cm.get("CMINDC"),
                    "CMDSTXT": cm.get("CMDSTXT"),
                    "CMDOSU": cm.get("CMDOSU"),
                    "DOSUO": cm.get("DOSUO"),
                    "CMDOSFRM": cm.get("CMDOSFRM"),
                    "DOSFRMO": cm.get("DOSFRMO"),
                    "CMDOSFRQ": cm.get("CMDOSFRQ"),
                    "DOSFRQO": cm.get("DOSFRQO"),
                    "CMROUTE": cm.get("CMROUTE"),
                    "ROUTEO": cm.get("ROUTEO"),
                    "CMSTDAT": cm.get("CMSTDAT"),
                    "CMONGO": cm.get("CMONGO"),
                    "CMENDAT": cm.get("CMENDAT"),
                    "_baseline": cm.get("_baseline"),
                }
    rows = sorted(rows_map.values(), key=lambda r: (r["patient_id"], r["CMTRT"] or ""))
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, CM_COLUMNS


def aggregate_vs(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    source: str = "hr",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """VS domain: 1 row per day.

    source="gt": from JSONL VS (ground truth, every day)
    source="hr": from hospital_record.objective.vitals (observed only)
    """
    patients = _resolve_patients(run_path, patient_ids)

    if source == "hr":
        return _aggregate_vs_hr(run_path, patients, mode, page, per_page)

    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            vs = record.get("VS")
            if not vs or not vs.get("VSPERF"):
                continue
            rows.append({
                "patient_id": pid,
                "day": record.get("day"),
                "SYSBP": vs.get("SYSBP_VSORRES"),
                "DIABP": vs.get("DIABP_VSORRES"),
                "PULSE": vs.get("PULSE_VSORRES"),
                "TEMP": vs.get("TEMP_VSORRES"),
                "RESP": vs.get("RESP_VSORRES"),
                "HEIGHT": vs.get("HEIGHT_VSORRES"),
                "WEIGHT": vs.get("WEIGHT_VSORRES"),
                "SpO2": vs.get("_SpO2"),
            })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, VS_COLUMNS


def _aggregate_vs_hr(
    run_path: Path,
    patients: list[str],
    mode: str,
    page: int,
    per_page: int,
) -> tuple[list[dict], int, list[dict]]:
    """VS from hospital_record.objective.vitals — only non-stale observations."""
    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            hr = record.get("hospital_record", {})
            obj = hr.get("objective", {})
            vitals = obj.get("vitals")
            if not vitals:
                continue
            stale = obj.get("vitals_stale_days", 0)
            day = record.get("day", hr.get("day"))
            rows.append({
                "patient_id": pid,
                "day": day,
                "_cycle": _fmt_cycle(record.get("cycle"), record.get("cycle_day")),
                "SBP": vitals.get("SBP"),
                "DBP": vitals.get("DBP"),
                "HR": vitals.get("HR"),
                "BT": vitals.get("BT"),
                "RR": vitals.get("RR"),
                "height_cm": vitals.get("height_cm"),
                "weight_kg": vitals.get("weight_kg"),
                "SpO2": vitals.get("SpO2"),
                "stale_days": stale,
            })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, VS_HR_COLUMNS


def aggregate_lb(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    source: str = "hr",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """LB domain: 1 row per lab test per day.

    source="gt": from JSONL LB.results (ground truth)
    source="hr": from hospital_record.objective.labs (observed)
    """
    patients = _resolve_patients(run_path, patient_ids)

    if source == "hr":
        return _aggregate_lb_hr(run_path, patients, mode, page, per_page)

    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            lb = record.get("LB")
            if not lb or not lb.get("LBPERF"):
                continue
            day = record.get("day")
            results = lb.get("results", {})
            for test_name, vals in results.items():
                rows.append({
                    "patient_id": pid,
                    "day": day,
                    "test_name": test_name,
                    "LBORRES": vals.get("LBORRES"),
                    "LBORRESU": vals.get("LBORRESU"),
                    "LBCAT": vals.get("LBCAT"),
                    "_trend": vals.get("_trend"),
                })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, LB_COLUMNS


def _aggregate_lb_hr(
    run_path: Path,
    patients: list[str],
    mode: str,
    page: int,
    per_page: int,
) -> tuple[list[dict], int, list[dict]]:
    """LB from hospital_record.objective.labs."""
    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            hr = record.get("hospital_record", {})
            obj = hr.get("objective", {})
            labs = obj.get("labs")
            if not labs:
                continue
            stale = obj.get("labs_stale_days", 0)
            day = record.get("day", hr.get("day"))
            cycle_display = _fmt_cycle(record.get("cycle"), record.get("cycle_day"))
            for test_name, vals in labs.items():
                rows.append({
                    "patient_id": pid,
                    "day": day,
                    "_cycle": cycle_display,
                    "test_name": test_name,
                    "value": vals.get("value"),
                    "unit": vals.get("unit"),
                    "trend": vals.get("trend"),
                    "stale_days": stale,
                })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, LB_HR_COLUMNS


def aggregate_ds(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """DS domain: 0-1 row per patient (last non-null DS record)."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        last_ds = None
        last_day = None
        last_cycle = None
        for record in _iter_jsonl(run_path, pid, mode):
            ds = record.get("DS")
            if ds:
                last_ds = ds
                last_day = record.get("day")
                last_cycle = _fmt_cycle(record.get("cycle"), record.get("cycle_day"))
        if last_ds:
            rows.append({
                "patient_id": pid,
                "day": last_day,
                "_cycle": last_cycle,
                "DSDECOD": last_ds.get("DSDECOD", ""),
                "DSTERM": last_ds.get("DSTERM", ""),
                "DSSTDAT": last_ds.get("DSSTDAT"),
            })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, DS_COLUMNS


def aggregate_dd(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """DD domain: 0-1 row per patient (death detail)."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        last_dd = None
        last_day = None
        last_cycle = None
        for record in _iter_jsonl(run_path, pid, mode):
            dd = record.get("DD")
            if dd:
                last_dd = dd
                last_day = record.get("day")
                last_cycle = _fmt_cycle(record.get("cycle"), record.get("cycle_day"))
        if last_dd:
            rows.append({
                "patient_id": pid,
                "day": last_day,
                "_cycle": last_cycle,
                "DTHDAT": last_dd.get("DTHDAT"),
                "DDDECOD": last_dd.get("DDDECOD", ""),
                "DDTERM": last_dd.get("DDTERM", ""),
                "PRCDTH_DDORRES": last_dd.get("PRCDTH_DDORRES"),
                "AUTOPIND_DDORRES": last_dd.get("AUTOPIND_DDORRES"),
            })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, DD_COLUMNS


def aggregate_tu(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """TU domain: tumor assessment rows (1 per scan day)."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            tu = record.get("TU")
            if not tu:
                continue
            day = record.get("day")
            cycle_display = _fmt_cycle(record.get("cycle"), record.get("cycle_day"))
            items = tu if isinstance(tu, list) else [tu] if isinstance(tu, dict) else []
            for item in items:
                rows.append({
                    "patient_id": pid,
                    "day": day,
                    "_cycle": cycle_display,
                    "TULNKID": item.get("TULNKID"),
                    "TULOC": item.get("TULOC", ""),
                    "TULAT": item.get("TULAT"),
                    "TUDIR": item.get("TUDIR"),
                    "TULOCDTL": item.get("TULOCDTL"),
                    "TUMETHOD": item.get("TUMETHOD", ""),
                    "TUDAT": item.get("TUDAT"),
                    "TUEVAL": item.get("TUEVAL"),
                    "TUEVALID": item.get("TUEVALID"),
                    "TRORRES": item.get("TRORRES"),
                    "TRORRESU": item.get("TRORRESU"),
                    "TRSTAT": item.get("TRSTAT"),
                    "TRREASND": item.get("TRREASND"),
                    "TURESULT": item.get("TURESULT", ""),
                })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, TU_COLUMNS


def aggregate_rs(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """RS domain: RECIST response assessment (1 per scan)."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            rs = record.get("RS")
            if not rs:
                continue
            day = record.get("day")
            cycle_display = _fmt_cycle(record.get("cycle"), record.get("cycle_day"))
            items = rs if isinstance(rs, list) else [rs] if isinstance(rs, dict) else []
            for item in items:
                rows.append({
                    "patient_id": pid,
                    "day": day,
                    "_cycle": cycle_display,
                    "RSCAT": item.get("RSCAT"),
                    "RSEVAL": item.get("RSEVAL", ""),
                    "RSEVALID": item.get("RSEVALID"),
                    "TRGRESP_RSORRES": item.get("TRGRESP_RSORRES"),
                    "NTRGRESP_RSORRES": item.get("NTRGRESP_RSORRES"),
                    "OVRLRESP_RSORRES": item.get("OVRLRESP_RSORRES"),
                    "BESTRESP_RSORRES": item.get("BESTRESP_RSORRES"),
                    "RSRESULT": item.get("RSRESULT", item.get("RSORRES", "")),
                    "RSTESTCD": item.get("RSTESTCD", ""),
                })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, RS_COLUMNS


def aggregate_pe(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """PE domain: physical exam records (1 per exam day)."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            pe = record.get("PE")
            if not pe or not pe.get("PEPERF"):
                continue
            rows.append({
                "patient_id": pid,
                "day": record.get("day"),
                "_cycle": _fmt_cycle(record.get("cycle"), record.get("cycle_day")),
                "PEPERF": pe.get("PEPERF"),
                "PEDAT": pe.get("PEDAT"),
            })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, PE_COLUMNS


def aggregate_eg(
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """EG domain: ECG test records."""
    patients = _resolve_patients(run_path, patient_ids)
    rows = []
    for pid in patients:
        for record in _iter_jsonl(run_path, pid, mode):
            eg = record.get("EG")
            if not eg or not eg.get("EGPERF"):
                continue
            rows.append({
                "patient_id": pid,
                "day": record.get("day"),
                "_cycle": _fmt_cycle(record.get("cycle"), record.get("cycle_day")),
                "EGPERF": eg.get("EGPERF"),
                "EGREFID": eg.get("EGREFID"),
                "EGMETHOD": eg.get("EGMETHOD", ""),
                "EGPOS": eg.get("EGPOS", ""),
                "EGDAT": eg.get("EGDAT"),
            })
    page_rows, total = _paginate(rows, page, per_page)
    return page_rows, total, EG_COLUMNS


# ─── Dispatcher ─────────────────────────────────────────────────────────

AGGREGATE_FNS: dict[str, Any] = {
    "DM": aggregate_dm,
    "MH": aggregate_mh,
    "AE": aggregate_ae,
    "EC": aggregate_ec,
    "CM": aggregate_cm,
    "VS": aggregate_vs,
    "LB": aggregate_lb,
    "DS": aggregate_ds,
    "DD": aggregate_dd,
    "TU": aggregate_tu,
    "RS": aggregate_rs,
    "PE": aggregate_pe,
    "EG": aggregate_eg,
}


def aggregate_domain(
    domain: str,
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    source: str = "hr",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, list[dict]]:
    """Dispatch to the appropriate domain aggregation function.

    Returns (rows, total_count, columns).
    """
    fn = AGGREGATE_FNS.get(domain.upper())
    if not fn:
        return [], 0, []

    # Build kwargs based on what the function accepts
    import inspect
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {"run_path": run_path, "page": page, "per_page": per_page}
    if "patient_ids" in sig.parameters:
        kwargs["patient_ids"] = patient_ids
    if "mode" in sig.parameters:
        kwargs["mode"] = mode
    if "source" in sig.parameters:
        kwargs["source"] = source
    return fn(**kwargs)


# ─── Excel export helper ────────────────────────────────────────────────

def export_domain_to_excel(
    domain: str,
    run_path: Path,
    patient_ids: list[str] | None = None,
    mode: str = "natural",
    source: str = "hr",
) -> bytes:
    """Export a domain to Excel bytes (openpyxl).

    Returns all rows (no pagination) as .xlsx bytes.
    """
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    rows, total, columns = aggregate_domain(
        domain, run_path, patient_ids, mode, source,
        page=1, per_page=0,  # 0 = no limit
    )

    wb = Workbook()
    ws = wb.active
    ws.title = domain.upper()

    # Header style
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # Write headers
    col_keys = [c["key"] for c in columns]
    for ci, col in enumerate(columns, 1):
        cell = ws.cell(row=1, column=ci, value=col["label"])
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    # Write data
    for ri, row in enumerate(rows, 2):
        for ci, key in enumerate(col_keys, 1):
            val = row.get(key)
            if isinstance(val, bool):
                val = "Yes" if val else "No"
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.border = thin_border

    # Auto-width
    for ci, col in enumerate(columns, 1):
        max_len = len(col["label"])
        for ri in range(2, len(rows) + 2):
            val = ws.cell(row=ri, column=ci).value
            if val is not None:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = min(max_len + 2, 40)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
