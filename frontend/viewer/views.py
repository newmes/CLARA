"""
Views for Clinical Trial Simulation Viewer.

Generative Agents 스타일의 day-by-day viewer +
Concordia 스타일의 SSE 실시간 업데이트 +
Interactive Game Mode (Care Agent 대신 사람이 참여).
"""
import json
import os
import time
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

# ─── Data helpers ─────────────────────────────────────────────

DATA_DIR = settings.DATA_DIR


def _get_runs():
    """Available simulation runs, newest first."""
    runs_dir = DATA_DIR / "runs"
    if not runs_dir.exists():
        return []
    runs = []
    for d in sorted(runs_dir.iterdir(), reverse=True):
        if d.is_dir() and (d / "simulations").exists():
            modes = []
            if list((d / "simulations").glob("*_natural.jsonl")):
                modes.append("natural")
            if list((d / "simulations").glob("*_care_ai.jsonl")):
                modes.append("care_ai")
            runs.append({
                "id": d.name,
                "path": str(d),
                "modes": modes,
            })
    return runs


def _get_run_path(run_id: str) -> Path:
    return DATA_DIR / "runs" / run_id


def _load_patient_profile(run_path: Path, patient_id: str) -> dict:
    """Load patient JSON profile (demographics, persona, etc.)."""
    f = run_path / "patients" / f"{patient_id}.json"
    if f.exists():
        with open(f) as fh:
            return json.load(fh)
    return {}


def _load_rule_set(run_path: Path) -> dict:
    """Load rule_set.json for this run."""
    f = run_path / "rule_set.json"
    if f.exists():
        with open(f) as fh:
            return json.load(fh)
    return {}


def _list_patients(run_path: Path) -> list[str]:
    """List patient IDs from simulation files."""
    sim_dir = run_path / "simulations"
    if not sim_dir.exists():
        return []
    ids = set()
    for f in sim_dir.glob("*_natural.jsonl"):
        pid = f.stem.replace("_natural", "")
        ids.add(pid)
    for f in sim_dir.glob("*_care_ai.jsonl"):
        pid = f.stem.replace("_care_ai", "")
        ids.add(pid)
    return sorted(ids)


def _load_day_for_patient(run_path: Path, patient_id: str, day: int,
                          mode: str = "natural") -> dict | None:
    """Load a single day's data for a patient from JSONL."""
    f = run_path / "simulations" / f"{patient_id}_{mode}.jsonl"
    if not f.exists():
        return None
    with open(f) as fh:
        for line in fh:
            record = json.loads(line)
            if record.get("day") == day:
                return record
    return None


def _load_all_days_for_patient(run_path: Path, patient_id: str,
                               mode: str = "natural") -> list[dict]:
    """Load all days for a patient."""
    f = run_path / "simulations" / f"{patient_id}_{mode}.jsonl"
    if not f.exists():
        return []
    days = []
    with open(f) as fh:
        for line in fh:
            days.append(json.loads(line))
    return days


def _count_days(run_path: Path, mode: str | None = None) -> int:
    """Find max day across all patients.
    
    If mode is specified, only count that mode's files.
    Otherwise, count across all modes.
    """
    sim_dir = run_path / "simulations"
    max_day = 0
    patterns = []
    if mode:
        patterns.append(f"*_{mode}.jsonl")
    else:
        patterns.extend(["*_natural.jsonl", "*_care_ai.jsonl"])
    for pattern in patterns:
        for f in sim_dir.glob(pattern):
            with open(f) as fh:
                for line in fh:
                    d = json.loads(line).get("day", 0)
                    if d > max_day:
                        max_day = d
    return max_day


def _extract_day_events(day_data: dict) -> list[dict]:
    """Extract notable events from a day's data for the summary panel."""
    events = []
    pid = day_data.get("patient_id", "?")

    # AE events
    for ae in day_data.get("AE", []):
        ae_term = ae.get("AETERM", "unknown")
        grade = ae.get("_grade", "?")
        status = ae.get("_status", "")
        days_active = ae.get("_days_active", 0)

        if days_active <= 1:
            events.append({
                "type": "ae_onset",
                "severity": "high" if grade >= 3 else "medium",
                "icon": "🔴" if grade >= 3 else "🟡",
                "text": f"{pid}: {ae_term} Grade {grade} onset",
            })
        elif "worsened" in status:
            events.append({
                "type": "ae_worsened",
                "severity": "high" if grade >= 3 else "medium",
                "icon": "🔴" if grade >= 3 else "🟡",
                "text": f"{pid}: {ae_term} worsened to Grade {grade}",
            })

    # Resolved AEs (checking _status)
    for ae in day_data.get("AE", []):
        if ae.get("_status") == "resolved":
            events.append({
                "type": "ae_resolved",
                "severity": "low",
                "icon": "🟢",
                "text": f"{pid}: {ae.get('AETERM', '?')} resolved",
            })

    # Dose modifications
    for ec in day_data.get("EC", []):
        if ec.get("ECDOSADJ"):
            drug = ec.get("ECREFID", "?")
            adj = ec.get("ECADJ", "modified")
            events.append({
                "type": "dose_mod",
                "severity": "medium",
                "icon": "💊",
                "text": f"{pid}: {drug} {adj}",
            })

    # Treatment administration
    for ec in day_data.get("EC", []):
        if ec.get("ECTRTCMP") and not ec.get("ECDOSADJ"):
            drug = ec.get("ECREFID", "?")
            events.append({
                "type": "treatment",
                "severity": "info",
                "icon": "💉",
                "text": f"{pid}: {drug} administered",
            })

    # RECIST scan
    rs = day_data.get("RS")
    if rs:
        if isinstance(rs, list):
            for r in rs:
                events.append({
                    "type": "recist",
                    "severity": "info",
                    "icon": "📋",
                    "text": f"{pid}: RECIST scan — {r.get('RSORRESU', '?')}",
                })
        elif isinstance(rs, dict):
            events.append({
                "type": "recist",
                "severity": "info",
                "icon": "📋",
                "text": f"{pid}: RECIST scan — {rs.get('RSORRESU', '?')}",
            })

    # Discontinuation
    ds = day_data.get("DS")
    if ds:
        events.append({
            "type": "discontinuation",
            "severity": "high",
            "icon": "⛔",
            "text": f"{pid}: Discontinued — {ds.get('DSDECOD', '?')}",
        })

    # Video call / Care record
    for cr in day_data.get("care_record", []):
        assessment = cr.get("nurse_assessment", {})
        severity_level = assessment.get("severity_level", "green")
        summary_text = assessment.get("summary", "Care AI interaction")
        sev_map = {"green": "info", "yellow": "info", "orange": "medium", "red": "high"}
        icon_map = {"green": "📹", "yellow": "📹", "orange": "🟠", "red": "🔴"}
        events.append({
            "type": "video_call",
            "severity": sev_map.get(severity_level, "info"),
            "icon": icon_map.get(severity_level, "📹"),
            "text": f"{pid}: Video call [{severity_level.upper()}] — {summary_text}",
        })
        for action in cr.get("actions", []):
            act = action.get("action", "")
            if act not in ("no_action", "monitor_closely"):
                events.append({
                    "type": "care_action",
                    "severity": "medium",
                    "icon": "🩺",
                    "text": f"{pid}: Care AI → {act}: {action.get('reason', '')}",
                })

    # Observation events
    for obs in day_data.get("observation_events", []):
        obs_type = obs.get("type", "")
        if obs_type == "self_report":
            events.append({
                "type": "self_report", "severity": "info", "icon": "📞",
                "text": f"{pid}: Self-reported symptoms to clinic",
            })
        elif obs_type == "er_visit":
            events.append({
                "type": "er_visit", "severity": "high", "icon": "🚑",
                "text": f"{pid}: Emergency room visit",
            })

    return events


def _patient_summary(profile: dict, day_data: dict | None) -> dict:
    """Build a summary dict for a patient card."""
    dm = profile.get("DM", {})
    persona = profile.get("persona", {})

    summary = {
        "patient_id": profile.get("patient_id", "?"),
        "age": dm.get("AGE", "?"),
        "sex": dm.get("SEX", "?"),
        "persona_type": persona.get("type", "unknown"),
        "persona_desc": persona.get("description", ""),
    }

    if day_data:
        obj = day_data.get("objective", {})
        summary["location"] = obj.get("location", "?")
        summary["treatment_status"] = obj.get("treatment_status", "?")
        summary["ecog"] = obj.get("ecog", "?")
        summary["tumor_change_pct"] = obj.get("tumor", {}).get(
            "estimated_change_pct", 0)

        # Active AEs
        active_aes = []
        for ae in day_data.get("AE", []):
            if ae.get("AEONGO") or ae.get("_status", "").startswith("active"):
                active_aes.append({
                    "term": ae.get("AETERM", "?"),
                    "grade": ae.get("_grade", "?"),
                    "days_active": ae.get("_days_active", 0),
                })
        summary["active_aes"] = active_aes

        # Key labs
        lb = day_data.get("LB", {}).get("results", {})
        summary["labs"] = {
            "ANC": lb.get("ANC", {}).get("LBORRES"),
            "Hb": lb.get("hemoglobin", {}).get("LBORRES"),
            "PLT": lb.get("platelets", {}).get("LBORRES"),
        }

        # Vitals
        vs = day_data.get("VS", {})
        summary["vitals"] = {
            "temp": vs.get("TEMP_VSORRES"),
            "bp": f"{vs.get('SYSBP_VSORRES', '?')}/{vs.get('DIABP_VSORRES', '?')}",
            "hr": vs.get("PULSE_VSORRES"),
        }

        # Subjective
        subj = day_data.get("subjective", {})
        summary["awareness"] = subj.get("overall_awareness", "?")
        summary["symptoms_perceived"] = subj.get(
            "symptoms_patient_perceives", [])

        # Sim metadata
        sim = day_data.get("_sim", {})
        summary["generation_mode"] = sim.get("generation_mode", "?")
        summary["mortality_risk"] = sim.get("mortality_risk", 0)

        # ── New: Mood state ──
        summary["mood"] = day_data.get("mood_state", {})

        # ── New: Hospital record (what the hospital knows) ──
        summary["hospital_record"] = day_data.get("hospital_record", {})

        # ── New: Observation events ──
        summary["observation_events"] = day_data.get("observation_events", [])

        # ── New: Care AI record ──
        care_records = day_data.get("care_record", [])
        if care_records:
            cr = care_records[0] if isinstance(care_records, list) else care_records
            summary["care_record"] = {
                "severity_level": cr.get("nurse_assessment", {}).get("severity_level", ""),
                "summary": cr.get("nurse_assessment", {}).get("summary", ""),
                "actions": cr.get("actions", []),
                "detection": cr.get("detection", {}),
                "turns": cr.get("turns", []),
                "terminated_early": cr.get("terminated_early", False),
                "mood_snapshot": cr.get("mood_snapshot", {}),
                "interaction_quality": cr.get("interaction_quality", {}),
                "grade_distortion": cr.get("grade_distortion", 0),
            }
        else:
            summary["care_record"] = None

    return summary


# ─── Page views ───────────────────────────────────────────────

def landing(request):
    """Landing page: list available simulation runs."""
    runs = _get_runs()
    context = {"runs": runs}
    return render(request, "landing/landing.html", context)


def trial_viewer(request, run_id: str, day: int = 1):
    """Main trial viewer page — Generative Agents demo style."""
    run_path = _get_run_path(run_id)
    if not run_path.exists():
        return HttpResponse("Run not found", status=404)

    # Check which modes are available
    sim_dir = run_path / "simulations"
    available_modes = []
    if list(sim_dir.glob("*_natural.jsonl")):
        available_modes.append("natural")
    if list(sim_dir.glob("*_care_ai.jsonl")):
        available_modes.append("care_ai")

    # Default to first available mode if requested mode doesn't exist
    mode = request.GET.get("mode", "")
    if mode not in available_modes:
        mode = available_modes[0] if available_modes else "natural"

    patient_ids = _list_patients(run_path)
    total_days = _count_days(run_path, mode)
    rule_set = _load_rule_set(run_path)

    # Load profiles for patient cards
    patients = []
    for pid in patient_ids:
        profile = _load_patient_profile(run_path, pid)
        day_data = _load_day_for_patient(run_path, pid, day, mode)
        patients.append(_patient_summary(profile, day_data))

    # Collect day events across all patients
    all_events = []
    for pid in patient_ids:
        day_data = _load_day_for_patient(run_path, pid, day, mode)
        if day_data:
            all_events.extend(_extract_day_events(day_data))

    # Sort events: high severity first
    severity_order = {"high": 0, "medium": 1, "info": 2, "low": 3}
    all_events.sort(key=lambda e: severity_order.get(e["severity"], 9))

    # Cycle info
    cycle_length = 21
    if rule_set:
        td = rule_set.get("trial_design", {})
        cycle_length = td.get("cycle_length_days", 21)
    cycle = (day - 1) // cycle_length + 1
    cycle_day = (day - 1) % cycle_length + 1

    drug_name = rule_set.get("drug_name", "Unknown")
    indication = rule_set.get("indication", "")

    context = {
        "run_id": run_id,
        "day": day,
        "total_days": total_days,
        "cycle": cycle,
        "cycle_day": cycle_day,
        "cycle_length": cycle_length,
        "drug_name": drug_name,
        "indication": indication,
        "mode": mode,
        "available_modes": available_modes,
        "available_modes_json": json.dumps(available_modes),
        "patients": patients,
        "patients_json": json.dumps(patients),
        "events": all_events,
        "events_json": json.dumps(all_events),
        "patient_ids": patient_ids,
        "patient_ids_json": json.dumps(patient_ids),
    }
    return render(request, "trial/trial.html", context)


def patient_state(request, run_id: str, patient_id: str, day: int = None):
    """Patient detail page — like Generative Agents persona_state."""
    run_path = _get_run_path(run_id)
    if not run_path.exists():
        return HttpResponse("Run not found", status=404)

    # Auto-detect mode if not specified
    sim_dir = run_path / "simulations"
    avail = []
    if list(sim_dir.glob("*_natural.jsonl")):
        avail.append("natural")
    if list(sim_dir.glob("*_care_ai.jsonl")):
        avail.append("care_ai")
    mode = request.GET.get("mode", "")
    if mode not in avail:
        mode = avail[0] if avail else "natural"

    profile = _load_patient_profile(run_path, patient_id)
    all_days = _load_all_days_for_patient(run_path, patient_id, mode)

    if not day and all_days:
        day = all_days[-1].get("day", 1)
    current_day_data = None
    for d in all_days:
        if d.get("day") == day:
            current_day_data = d
            break

    # Django templates disallow underscore-prefixed attributes.
    # Remap _grade, _status, _days_active etc. into template-safe keys.
    if current_day_data:
        safe_aes = []
        for ae in current_day_data.get("AE", []):
            safe_ae = {k.lstrip("_") if k.startswith("_") else k: v
                       for k, v in ae.items()}
            safe_aes.append(safe_ae)
        current_day_data = dict(current_day_data)
        current_day_data["safe_AE"] = safe_aes

    # Build AE timeline
    ae_timeline = []
    for d in all_days:
        day_num = d.get("day", 0)
        for ae in d.get("AE", []):
            ae_timeline.append({
                "day": day_num,
                "term": ae.get("AETERM"),
                "grade": ae.get("_grade"),
                "status": ae.get("_status"),
                "days_active": ae.get("_days_active"),
            })

    # Build lab trends
    lab_trends = {}
    for d in all_days:
        day_num = d.get("day", 0)
        results = d.get("LB", {}).get("results", {})
        for lab_name, lab_val in results.items():
            if lab_name not in lab_trends:
                lab_trends[lab_name] = []
            lab_trends[lab_name].append({
                "day": day_num,
                "value": lab_val.get("LBORRES"),
                "unit": lab_val.get("LBORRESU", ""),
            })

    # Build event log (memory stream, like generative agents)
    event_log = []
    for d in all_days:
        day_num = d.get("day", 0)
        sim = d.get("_sim", {})

        if sim.get("generation_mode") == "event_day":
            day_events = _extract_day_events(d)
            for evt in day_events:
                event_log.append({
                    "day": day_num,
                    "type": evt["type"],
                    "text": evt["text"],
                    "icon": evt["icon"],
                })

        # Care records
        for cr in d.get("care_record", []):
            event_log.append({
                "day": day_num,
                "type": "care",
                "text": cr.get("summary", "Video call"),
                "icon": "📹",
            })

    # Format BMI to 1 decimal
    bmi_raw = profile.get("emr", {}).get("demographics", {}).get("bmi", "?")
    bmi_display = f"{bmi_raw:.1f}" if isinstance(bmi_raw, (int, float)) else str(bmi_raw)

    # Build mood trajectory
    mood_trajectory = []
    for d in all_days:
        m = d.get("mood_state", {})
        if m:
            mood_trajectory.append({"day": d.get("day", 0), **m})

    context = {
        "run_id": run_id,
        "patient_id": patient_id,
        "day": day,
        "mode": mode,
        "profile": profile,
        "profile_json": json.dumps(profile),
        "current_day": current_day_data,
        "current_day_json": json.dumps(current_day_data),
        "ae_timeline_json": json.dumps(ae_timeline),
        "lab_trends_json": json.dumps(lab_trends),
        "mood_trajectory_json": json.dumps(mood_trajectory),
        "event_log": event_log,
        "total_days": len(all_days),
        "bmi_display": bmi_display,
    }
    return render(request, "patient_state/patient_state.html", context)


# ─── JSON API ─────────────────────────────────────────────────

def api_run_meta(request, run_id: str):
    """Run metadata: patients, total days, drug info."""
    run_path = _get_run_path(run_id)
    if not run_path.exists():
        return JsonResponse({"error": "not found"}, status=404)

    mode = request.GET.get("mode", None)
    patient_ids = _list_patients(run_path)
    total_days = _count_days(run_path, mode)
    rule_set = _load_rule_set(run_path)

    return JsonResponse({
        "run_id": run_id,
        "patient_ids": patient_ids,
        "total_days": total_days,
        "drug_name": rule_set.get("drug_name", ""),
        "indication": rule_set.get("indication", ""),
        "cycle_length": rule_set.get("trial_design", {}).get(
            "cycle_length_days", 21),
    })


def api_day_data(request, run_id: str, day: int):
    """All patients' data for a specific day — for AJAX day navigation."""
    run_path = _get_run_path(run_id)
    if not run_path.exists():
        return JsonResponse({"error": "not found"}, status=404)

    mode = request.GET.get("mode", "natural")
    patient_ids = _list_patients(run_path)
    patients = []
    all_events = []

    for pid in patient_ids:
        profile = _load_patient_profile(run_path, pid)
        day_data = _load_day_for_patient(run_path, pid, day, mode)
        patients.append(_patient_summary(profile, day_data))
        if day_data:
            all_events.extend(_extract_day_events(day_data))

    severity_order = {"high": 0, "medium": 1, "info": 2, "low": 3}
    all_events.sort(key=lambda e: severity_order.get(e["severity"], 9))

    return JsonResponse({
        "day": day,
        "patients": patients,
        "events": all_events,
    })


def api_patient_timeline(request, run_id: str, patient_id: str):
    """Full timeline for a patient — for charts and detailed view."""
    run_path = _get_run_path(run_id)
    if not run_path.exists():
        return JsonResponse({"error": "not found"}, status=404)

    all_days = _load_all_days_for_patient(run_path, patient_id)
    profile = _load_patient_profile(run_path, patient_id)

    return JsonResponse({
        "patient_id": patient_id,
        "profile": profile,
        "days": all_days,
    })


# ─── SSE (Server-Sent Events) for auto-play ──────────────────

def sse_stream(request, run_id: str):
    """
    Concordia-style SSE endpoint for auto-play mode.
    Client sends speed via query param: ?speed=1 (days per second).
    Streams day data as events.
    """
    run_path = _get_run_path(run_id)
    if not run_path.exists():
        return HttpResponse("Run not found", status=404)

    speed = float(request.GET.get("speed", "1"))
    start_day = int(request.GET.get("start", "1"))
    mode = request.GET.get("mode", "natural")
    total_days = _count_days(run_path, mode)
    patient_ids = _list_patients(run_path)

    def event_stream():
        for day in range(start_day, total_days + 1):
            patients = []
            all_events = []
            for pid in patient_ids:
                profile = _load_patient_profile(run_path, pid)
                day_data = _load_day_for_patient(run_path, pid, day, mode)
                patients.append(_patient_summary(profile, day_data))
                if day_data:
                    all_events.extend(_extract_day_events(day_data))

            payload = json.dumps({
                "day": day,
                "patients": patients,
                "events": all_events,
            })
            yield f"event: day\ndata: {payload}\n\n"

            if speed > 0:
                time.sleep(1.0 / speed)

        yield "event: done\ndata: {}\n\n"

    response = StreamingHttpResponse(
        event_stream(), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ═══════════════════════════════════════════════════════════════
# Interactive Game Mode — Care Agent 대신 사람이 참여하는 시뮬레이션
# ═══════════════════════════════════════════════════════════════

def game_landing(request, run_id: str):
    """게임 모드 환자 선택 화면."""
    run_path = _get_run_path(run_id)
    if not run_path.exists():
        return HttpResponse("Run not found", status=404)

    patient_ids = _list_patients(run_path)
    rule_set = _load_rule_set(run_path)
    patients = []
    for pid in patient_ids:
        profile = _load_patient_profile(run_path, pid)
        demo = profile.get("emr", {}).get("demographics", {})
        persona = profile.get("persona", {})
        patients.append({
            "id": pid,
            "age": demo.get("age", "?"),
            "sex": demo.get("sex", "?"),
            "ecog": demo.get("ecog_ps", "?"),
            "persona_type": persona.get("type", "?"),
        })

    return render(request, "game/game_landing.html", {
        "run_id": run_id,
        "patients": patients,
        "drug_name": rule_set.get("drug_name", ""),
        "indication": rule_set.get("indication", ""),
    })


def game_play(request, run_id: str, patient_id: str):
    """게임 플레이 메인 화면."""
    run_path = _get_run_path(run_id)
    if not run_path.exists():
        return HttpResponse("Run not found", status=404)

    profile = _load_patient_profile(run_path, patient_id)
    rule_set = _load_rule_set(run_path)

    return render(request, "game/game_play.html", {
        "run_id": run_id,
        "patient_id": patient_id,
        "profile": json.dumps(profile, ensure_ascii=False),
        "drug_name": rule_set.get("drug_name", ""),
        "indication": rule_set.get("indication", ""),
    })


# ── Game API (JSON) ──────────────────────────────────

@csrf_exempt
@require_POST
def api_game_start(request):
    """게임 세션 시작. run_id + patient_id로 세션 생성."""
    from src.game_session import create_game_session

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    run_id = body.get("run_id")
    patient_id = body.get("patient_id")
    total_days = body.get("total_days", 84)
    seed = body.get("seed", 42)

    if not run_id or not patient_id:
        return JsonResponse({"error": "run_id and patient_id required"}, status=400)

    try:
        session = create_game_session(
            run_id=run_id,
            patient_id=patient_id,
            total_days=total_days,
            seed=seed,
            data_dir=str(DATA_DIR),
        )
    except FileNotFoundError as e:
        return JsonResponse({"error": str(e)}, status=404)

    return JsonResponse({
        "session_id": session.session_id,
        "patient_id": patient_id,
        "total_days": total_days,
        "status": session.status,
    })


@csrf_exempt
@require_POST
def api_game_advance(request):
    """다음 Day로 진행. GT 생성 + HR 뷰 반환."""
    from src.game_session import get_session

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = body.get("session_id")
    session = get_session(session_id)
    if not session:
        return JsonResponse({"error": "Session not found"}, status=404)

    result = session.advance_day()
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


@csrf_exempt
@require_POST
def api_game_greet(request):
    """환자 초기 인사 생성."""
    from src.game_session import get_session

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = body.get("session_id")
    session = get_session(session_id)
    if not session:
        return JsonResponse({"error": "Session not found"}, status=404)

    result = session.patient_greet()
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


@csrf_exempt
@require_POST
def api_game_chat(request):
    """플레이어 메시지 → 환자 AI 응답."""
    from src.game_session import get_session

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = body.get("session_id")
    message = body.get("message", "")
    session = get_session(session_id)
    if not session:
        return JsonResponse({"error": "Session not found"}, status=404)
    if not message.strip():
        return JsonResponse({"error": "Empty message"}, status=400)

    result = session.player_chat(message)
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


@csrf_exempt
@require_POST
def api_game_end_chat(request):
    """대화 종료 + 관찰/조치 제출."""
    from src.game_session import get_session

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = body.get("session_id")
    observations = body.get("observations", [])
    actions = body.get("actions", [])
    session = get_session(session_id)
    if not session:
        return JsonResponse({"error": "Session not found"}, status=404)

    result = session.end_chat_and_submit(observations, actions)
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


@csrf_exempt
@require_POST
def api_game_skip(request):
    """대화 없이 Day 스킵."""
    from src.game_session import get_session

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = body.get("session_id")
    session = get_session(session_id)
    if not session:
        return JsonResponse({"error": "Session not found"}, status=404)

    result = session.skip_day()
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


@require_GET
def api_game_reveal(request, session_id: str):
    """게임 종료 후 GT 공개 + 성적표."""
    from src.game_session import get_session

    session = get_session(session_id)
    if not session:
        return JsonResponse({"error": "Session not found"}, status=404)

    result = session.reveal_ground_truth()
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


@require_GET
def api_game_sessions(request):
    """활성 게임 세션 목록."""
    from src.game_session import list_sessions
    return JsonResponse({"sessions": list_sessions()})


# ═══════════════════════════════════════════════════════
# A/B Comparison Dashboard
# ═══════════════════════════════════════════════════════

def compare_dashboard(request, run_id: str):
    """A/B 비교 대시보드 페이지."""
    run_path = _get_run_path(run_id)
    report_path = run_path / "comparison_report.json"

    # comparison_report.json이 없거나 낡았으면 재생성
    sim_dir = run_path / "simulations"
    needs_regen = not report_path.exists()
    if not needs_regen:
        newest_sim = max(f.stat().st_mtime for f in sim_dir.glob("*.jsonl"))
        if report_path.stat().st_mtime < newest_sim:
            needs_regen = True

    if needs_regen:
        from src.evaluator import run_evaluation
        run_evaluation(run_path)

    with open(report_path) as f:
        report = json.load(f)

    rule_set = _load_rule_set(run_path)

    return render(request, "compare/compare.html", {
        "run_id": run_id,
        "drug_name": rule_set.get("drug_name", "Unknown"),
        "indication": rule_set.get("indication", ""),
        "report_json": json.dumps(report, ensure_ascii=False),
    })


@require_GET
def api_compare_data(request, run_id: str):
    """A/B 비교 데이터 JSON API."""
    run_path = _get_run_path(run_id)
    report_path = run_path / "comparison_report.json"

    if not report_path.exists():
        from src.evaluator import run_evaluation
        run_evaluation(run_path)

    with open(report_path) as f:
        report = json.load(f)

    return JsonResponse(report, json_dumps_params={"ensure_ascii": False})


@require_GET
def api_compare_regenerate(request, run_id: str):
    """비교 리포트를 강제 재생성한다."""
    from src.evaluator import run_evaluation
    run_path = _get_run_path(run_id)
    report = run_evaluation(run_path)
    return JsonResponse({"status": "regenerated", "cohort_sizes": report["cohort_sizes"]})