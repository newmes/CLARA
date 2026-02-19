"""game_session.py — 인터랙티브 게임 모드 세션 관리

Care Agent 대신 사람이 간호사 역할을 수행하는 인터랙티브 시뮬레이션.
웹 UI에서 한 Day씩 진행하며, 환자 AI와 실시간 대화.

핵심 설계:
  - 기존 DailySimulator, ObservationModel, MoodState를 그대로 활용
  - Care Agent의 Patient LLM 로직을 재활용하여 환자 응답 생성
  - 서버 메모리에 세션 상태 유지 (REST API 간 상태 공유)
  - 플레이어는 Hospital Record만 볼 수 있음 (GT 숨김)
"""

import json
import uuid
import time
from pathlib import Path
from typing import Any

from src.agents.daily_agent import DailySimulator, create_simulator
from src.agents.llm_client import generate_json, set_caller, DEFAULT_MODEL
from src.agents.care_agent import apply_interventions, _create_conmed_from_recommendation
from src.engine.sampler import Sampler
from src.engine.mood import MoodState, compute_interaction_quality, compute_grade_distortion
from src.engine.observation import ObservationModel, compute_detection_delay_summary
from src.crf_mapper import map_day_record
from src.logger import get_logger

_logger = get_logger("game_session")


# ── 유틸리티 (orchestrator_v2에서 복사) ──────────────

def _is_hospital_day(
    day: int,
    cycle_length: int = 21,
    admin_cycle_days: list[int] | None = None,
) -> bool:
    if day <= 0:
        return False
    cycle_day = (day - 1) % cycle_length + 1
    if admin_cycle_days:
        return cycle_day in admin_cycle_days
    return cycle_day == 1

def _get_cycle_info(day: int, cycle_length: int = 21) -> tuple[int, int]:
    cycle = (day - 1) // cycle_length + 1
    cycle_day = (day - 1) % cycle_length + 1
    return cycle, cycle_day


# ══════════════════════════════════════════════════════
# 활성 게임 세션 저장소 (in-memory)
# ══════════════════════════════════════════════════════

_active_sessions: dict[str, "GameSession"] = {}


def get_session(session_id: str) -> "GameSession | None":
    return _active_sessions.get(session_id)


def list_sessions() -> list[dict]:
    return [
        {
            "session_id": sid,
            "patient_id": s.patient.get("patient_id", "?"),
            "current_day": s.current_day,
            "total_days": s.total_days,
            "status": s.status,
            "created_at": s.created_at,
        }
        for sid, s in _active_sessions.items()
    ]


# ══════════════════════════════════════════════════════
# GameSession — 인터랙티브 시뮬레이션 세션
# ══════════════════════════════════════════════════════

class GameSession:
    """한 명의 환자에 대한 인터랙티브 시뮬레이션 세션."""

    def __init__(
        self,
        rule_set: dict,
        patient: dict,
        total_days: int = 84,
        seed: int = 42,
        model: str = DEFAULT_MODEL,
    ):
        self.session_id = str(uuid.uuid4())[:8]
        self.rule_set = rule_set
        self.patient = patient
        self.total_days = total_days
        self.seed = seed
        self.model = model
        self.created_at = time.time()

        pid = patient.get("patient_id", "PT-001")
        patient_num = int(pid.split("-")[1]) if "-" in pid else 1

        # 시뮬레이션 컴포넌트 초기화
        self.sampler = Sampler(seed=seed + patient_num + 10000)
        self.simulator = create_simulator(
            rule_set=rule_set,
            patient=patient,
            sampler=self.sampler,
            model=model,
            actual_duration=total_days,
        )

        # Mood & Observation
        persona = patient.get("persona", {})
        persona_type = persona.get("type", "minimizer")
        self.mood = MoodState(
            persona_type=persona_type,
            seed=seed + patient_num + 20000,
        )
        obs_sampler = Sampler(seed=seed + patient_num + 40000)
        self.observation_model = ObservationModel(
            mood=self.mood,
            sampler=obs_sampler,
            care_ai_enabled=True,
        )

        # 투약 스케줄
        self.cycle_length = rule_set.get("trial_design", {}).get("cycle_length_days", 21)
        all_admin_days: set[int] = set()
        for drug in self.simulator.admin_schedule:
            all_admin_days.update(drug.get("cycle_days", [1]))
        self.admin_cycle_days = sorted(all_admin_days)

        # 상태 추적
        self.current_day = 0
        self.day_results: list[dict] = []
        self.gt_history: list[dict] = []
        self.hr_history: list[dict] = []
        self.chat_history: list[dict] = []
        self.player_actions_log: list[dict] = []
        self.status = "ready"  # ready, in_day, chatting, awaiting_decision, finished
        self.force_hospital_tomorrow = False

        # 현재 Day의 임시 상태
        self._current_day_result: dict | None = None
        self._current_observed: dict | None = None
        self._current_chat_turns: list[dict] = []
        self._current_is_hospital: bool = False
        self._current_is_admin: bool = False

        # Patient LLM 컨텍스트
        self.demographics = patient.get("emr", {}).get("demographics", {})
        self.persona = patient.get("persona", {})
        self.known_aes = [ae["ae_term"] for ae in rule_set.get("ae_profile", [])[:12]]

        # Multimodal bridge (face image + voice TTS)
        self._mm_bridge = None
        try:
            from src.multimodal.game_bridge import MultimodalGameBridge
            self._mm_bridge = MultimodalGameBridge(patient, enabled=True)
            _logger.info(f"[GameSession] Multimodal bridge enabled for {pid}")
        except Exception as e:
            _logger.warning(f"[GameSession] Multimodal bridge unavailable: {e}")

        # 세션 등록
        _active_sessions[self.session_id] = self
        _logger.info(f"[GameSession] Created: {self.session_id} for {pid}, {total_days} days")

    def advance_day(self) -> dict:
        """다음 Day로 진행. GT를 생성하고 HR 뷰를 반환.

        Returns:
            {
                "day": int,
                "cycle": int,
                "cycle_day": int,
                "is_hospital": bool,
                "is_finished": bool,
                "hospital_record": dict (HR 뷰),
                "events_summary": str,
                "can_chat": bool,
                "needs_decision": bool,
            }
        """
        if self.status == "finished":
            return {"is_finished": True, "day": self.current_day}

        self.current_day += 1
        day = self.current_day

        if day > self.total_days:
            self.status = "finished"
            return {"is_finished": True, "day": day}

        cycle, cycle_day = _get_cycle_info(day, self.cycle_length)
        is_hospital = _is_hospital_day(day, self.cycle_length, self.admin_cycle_days)
        is_admin = self.simulator.is_administration_day(cycle_day)

        if self.force_hospital_tomorrow:
            is_hospital = True
            self.force_hospital_tomorrow = False

        # Step 1: GT 생성 (hazard function 기반, retry 포함)
        day_result = None
        for attempt in range(3):
            try:
                day_result = self.simulator.generate_day(
                    self.day_results, day, cycle, cycle_day, is_hospital,
                )
                break
            except Exception as e:
                _logger.error(f"[GameSession] generate_day attempt {attempt+1} failed: {e}")
                if attempt == 2:
                    raise
        assert day_result is not None
        day_result["care_record"] = []

        # Step 2: Observation → HR 필터링
        is_visit = is_hospital or is_admin
        _is_visit, observed = self.observation_model.process_day(
            ground_truth=day_result,
            day=day,
            is_hospital=is_visit,
            is_admin_day=is_admin,
            care_record=None,
        )

        # 현재 Day 상태 저장 (chat/decision 전)
        self._current_day_result = day_result
        self._current_observed = observed
        self._current_chat_turns = []
        self._current_is_hospital = is_visit
        self._current_is_admin = is_admin

        # 이벤트 요약 생성
        gt_aes = day_result.get("objective", {}).get("active_aes", [])
        hr = observed["hospital_record"]
        hr_aes = hr.get("objective", {}).get("active_aes", [])
        mode = day_result.get("_generation_mode", "quiet_day")
        is_event_day = mode != "quiet_day"

        events_summary = self._build_events_summary(day, hr, is_visit, is_event_day)

        # 상태 설정
        self.status = "chatting"  # 매일 대화 가능

        return {
            "day": day,
            "cycle": cycle,
            "cycle_day": cycle_day,
            "is_hospital": is_visit,
            "is_event_day": is_event_day,
            "is_finished": False,
            "hospital_record": hr,
            "events_summary": events_summary,
            "can_chat": True,
            "needs_decision": is_visit,
            "mood_snapshot": self.mood.to_dict(),
            "active_hr_aes": [
                {"ae": ae.get("ae", ""), "grade": ae.get("grade", 0)}
                for ae in hr_aes
            ],
        }

    def patient_greet(self) -> dict:
        """환자의 초기 인사. 대화 시작 시 호출."""
        if not self._current_day_result:
            return {"error": "No day generated yet. Call advance_day first."}

        day = self.current_day
        day_result = self._current_day_result
        quality = compute_interaction_quality(self.mood)
        grade_distortion = compute_grade_distortion(self.mood)

        obj = day_result.get("objective", {})
        active_aes = obj.get("active_aes", [])
        max_grade = max((ae.get("grade", 0) for ae in active_aes), default=0)

        if max_grade >= 3:
            self.mood.apply_defensiveness_override(max_grade)
            quality = compute_interaction_quality(self.mood)

        set_caller(f"game.patient.{self.patient.get('patient_id', '?')}")

        system_prompt = self._patient_system_prompt(quality, grade_distortion)
        user_prompt = self._patient_greet_prompt(day, day_result, quality)

        try:
            result = generate_json(system_prompt, user_prompt, model=self.model, max_tokens=2048)
        except Exception as e:
            _logger.error(f"[GameSession] Patient greet LLM failed: {e}")
            result = {
                "greeting": "안녕하세요...",
                "reported_symptoms": [],
                "general_wellbeing": "OK",
                "mood_expression": "neutral",
                "video_visible": [],
            }

        # 대화 기록에 추가
        turn = {"turn": 1, "role": "patient", "content": result}
        self._current_chat_turns.append(turn)

        # 플레이어에게 보이는 형태로 변환
        greeting_text = result.get("greeting", "")
        symptoms = result.get("reported_symptoms", [])
        symptom_texts = [
            f"- {s.get('symptom', '?')} ({s.get('severity_perception', '?')})"
            for s in symptoms
        ]
        wellbeing = result.get("general_wellbeing", "")
        video_visible = result.get("video_visible", [])

        response = {
            "role": "patient",
            "greeting": greeting_text,
            "reported_symptoms": symptoms,
            "general_wellbeing": wellbeing,
            "mood_expression": result.get("mood_expression", "neutral"),
            "video_visible": video_visible,
            "display_text": self._format_patient_message(result, is_greeting=True),
        }

        if self._mm_bridge:
            try:
                media = self._mm_bridge.generate_turn_media(
                    text=greeting_text,
                    active_aes=day_result.get("AE", []),
                    day=day,
                    mood_snapshot=self.mood.to_dict(),
                )
                response.update(media)
            except Exception as e:
                _logger.warning(f"[GameSession] Multimodal greet failed: {e}")

        return response

    def player_chat(self, message: str) -> dict:
        """플레이어의 메시지에 환자 AI가 응답.

        Args:
            message: 플레이어(간호사)가 입력한 메시지

        Returns:
            환자 AI의 응답
        """
        if not self._current_day_result:
            return {"error": "No day generated yet."}

        day = self.current_day
        day_result = self._current_day_result
        quality = compute_interaction_quality(self.mood)
        grade_distortion = compute_grade_distortion(self.mood)

        # 플레이어 메시지를 대화 기록에 추가
        turn_num = len(self._current_chat_turns) + 1
        self._current_chat_turns.append({
            "turn": turn_num, "role": "nurse_human",
            "content": {"message": message},
        })

        # 대화 맥락 구성
        conversation_so_far = self._build_conversation_context()

        set_caller(f"game.patient.{self.patient.get('patient_id', '?')}")

        system_prompt = self._patient_chat_system_prompt(quality, grade_distortion)
        user_prompt = self._patient_chat_user_prompt(
            day, day_result, message, conversation_so_far, quality,
        )

        try:
            result = generate_json(system_prompt, user_prompt, model=self.model, max_tokens=2048)
        except Exception as e:
            _logger.error(f"[GameSession] Patient chat LLM failed: {e}")
            result = {
                "response": "잘 모르겠어요...",
                "revealed_new_info": False,
                "emotional_state": "neutral",
            }

        # 환자 응답을 대화 기록에 추가
        turn_num = len(self._current_chat_turns) + 1
        self._current_chat_turns.append({
            "turn": turn_num, "role": "patient",
            "content": result,
        })

        # Mood 업데이트: 대화가 진행될수록 defensiveness 약간 감소
        if result.get("revealed_new_info"):
            self.mood.update_turn({"defensiveness": -0.05, "anxiety": +0.02})
        else:
            self.mood.update_turn({"trust_in_ai": +0.01, "defensiveness": -0.01})

        response = {
            "role": "patient",
            "response": result.get("response", ""),
            "revealed_new_info": result.get("revealed_new_info", False),
            "emotional_state": result.get("emotional_state", "neutral"),
            "video_visible": result.get("video_visible", []),
            "display_text": self._format_patient_message(result, is_greeting=False),
        }

        if self._mm_bridge:
            try:
                media = self._mm_bridge.generate_turn_media(
                    text=result.get("response", ""),
                    active_aes=day_result.get("AE", []),
                    day=day,
                    mood_snapshot=self.mood.to_dict(),
                )
                response.update(media)
            except Exception as e:
                _logger.warning(f"[GameSession] Multimodal chat failed: {e}")

        return response

    def end_chat_and_submit(self, observations: list[dict], actions: list[dict]) -> dict:
        """대화 종료 + 관찰 내용/조치 제출.

        Args:
            observations: 플레이어가 대화에서 파악한 내용
                [{"ae_term": str, "estimated_grade": int, "source": str}]
            actions: 플레이어의 조치 결정
                [{"action": str, "detail": str, "reason": str}]

        Returns:
            결과 요약 + 다음 단계 안내
        """
        if not self._current_day_result:
            return {"error": "No day generated."}

        day = self.current_day
        day_result = self._current_day_result

        # care_record 형태로 변환 (기존 apply_interventions와 호환)
        care_record = {
            "day": day,
            "turns": self._current_chat_turns,
            "terminated_early": False,
            "nurse_assessment": {
                "severity_level": "yellow",
                "summary": "Player assessment",
                "detected_issues": [
                    {"issue": obs.get("ae_term", ""), "suspected_ae": obs.get("ae_term"),
                     "estimated_grade": obs.get("estimated_grade"), "confidence": "medium",
                     "detection_source": obs.get("source", "player_assessment")}
                    for obs in observations
                ],
            },
            "actions": actions,
            "detection": {
                "aes_detected": [obs.get("ae_term", "") for obs in observations],
            },
            "mood_snapshot": self.mood.to_dict(),
            "_player_mode": True,
        }

        # care_record를 day_result에 연결
        day_result["care_record"] = [care_record]

        # 개입 적용 (conmed 등)
        applied = apply_interventions(care_record, self.simulator, day)

        # 병원 방문 스케줄링
        for action_record in actions:
            act = action_record.get("action", "")
            if act in ("recommend_hospital_visit", "recommend_early_visit", "escalate_to_physician"):
                self.force_hospital_tomorrow = True

        # Observation 재처리 (care_record 반영)
        _is_visit, observed = self.observation_model.process_day(
            ground_truth=day_result,
            day=day,
            is_hospital=self._current_is_hospital,
            is_admin_day=self._current_is_admin,
            care_record=day_result.get("care_record"),
        )
        self._current_observed = observed

        # 병원 방문일: Dose Modification
        if self._current_is_hospital:
            observed_aes = observed["hospital_record"]["objective"]["active_aes"]
            cycle, cycle_day = _get_cycle_info(day, self.cycle_length)
            dose_changes = self.simulator.apply_hospital_dose_modifications(
                observed_aes, day, cycle, cycle_day,
            )
            if dose_changes:
                self.simulator.patch_day_treatment_status(day_result)
                day_result["_dose_modifications"] = dose_changes
            new_ts = day_result.get("objective", {}).get("treatment_status", "on_treatment")
            self.observation_model.update_treatment_status(new_ts)

        # HR 갱신
        day_result["hospital_record"] = observed["hospital_record"]
        day_result["observation_events"] = observed["observation_events"]
        day_result["mood_state"] = observed["mood_state"]

        # 기록 저장
        self.day_results.append(day_result)
        self.gt_history.append({
            "day": day,
            "active_aes": [
                {"ae": ae.get("ae"), "grade": ae.get("grade"), "status": ae.get("status")}
                for ae in day_result.get("objective", {}).get("active_aes", [])
            ],
            "location": day_result.get("objective", {}).get("location", "HOME"),
            "treatment_status": day_result.get("objective", {}).get("treatment_status", ""),
            "tumor_change": day_result.get("objective", {}).get("tumor_change_pct"),
        })
        self.hr_history.append({
            "day": day,
            "hospital_record": observed["hospital_record"],
        })
        self.chat_history.extend(self._current_chat_turns)
        self.player_actions_log.append({
            "day": day,
            "observations": observations,
            "actions": actions,
            "applied": applied,
        })

        # 사망/중도탈락 체크
        location = day_result.get("objective", {}).get("location", "HOME")
        ds = day_result.get("ds_record")
        is_finished = False

        if location == "DECEASED":
            self.status = "finished"
            is_finished = True
        elif ds and ds.get("DSDECOD") not in ("DEATH",):
            self.status = "finished"
            is_finished = True
        elif day >= self.total_days:
            self.status = "finished"
            is_finished = True
        else:
            self.status = "ready"

        return {
            "day": day,
            "applied_interventions": applied,
            "force_hospital_tomorrow": self.force_hospital_tomorrow,
            "is_finished": is_finished,
            "hospital_record": observed["hospital_record"],
            "status": self.status,
        }

    def skip_day(self) -> dict:
        """대화 없이 Day를 스킵 (quiet day용).

        advance_day()로 GT/HR 생성 후, 대화/판단 없이 바로 finalize.
        """
        return self.end_chat_and_submit(observations=[], actions=[])

    # ── Day Debrief (실시간 피드백) ──────────────────────

    def day_debrief(self) -> dict:
        """마지막 제출 기준, 플레이어 관찰 vs HR 비교 피드백.

        GT는 노출하지 않고, Hospital Record 기준으로만 비교한다.
        """
        if not self.player_actions_log:
            return {"error": "No submissions yet."}

        last_action = self.player_actions_log[-1]
        day = last_action["day"]
        player_obs = last_action.get("observations", [])
        player_actions = last_action.get("actions", [])

        # HR에서 해당 Day의 active AEs 가져오기
        hr_entry = None
        for h in reversed(self.hr_history):
            if h["day"] == day:
                hr_entry = h
                break
        if not hr_entry:
            return {"error": "No hospital record for this day."}

        hr = hr_entry["hospital_record"]
        hr_aes = hr.get("objective", {}).get("active_aes", [])

        # 플레이어가 제출한 AE 이름 세트
        player_ae_set = {obs.get("ae_term", "").lower().strip() for obs in player_obs}
        player_ae_map = {
            obs.get("ae_term", "").lower().strip(): obs
            for obs in player_obs
        }

        # 비교 결과 생성
        ae_results = []
        matched_hr_aes = set()

        for hr_ae in hr_aes:
            hr_term = hr_ae.get("ae", "").lower().strip()
            hr_grade = hr_ae.get("grade", 0)

            if hr_term in player_ae_set:
                # 플레이어가 감지함
                p_obs = player_ae_map[hr_term]
                p_grade = p_obs.get("estimated_grade", 0)
                grade_match = p_grade == hr_grade
                ae_results.append({
                    "ae": hr_ae.get("ae", ""),
                    "status": "correct",
                    "hr_grade": hr_grade,
                    "player_grade": p_grade,
                    "grade_match": grade_match,
                    "message": f"정확! G{p_grade}" if grade_match
                              else f"감지 ✓ (등급: 당신 G{p_grade} vs 병원 G{hr_grade})",
                })
                matched_hr_aes.add(hr_term)
            else:
                # 플레이어가 놓침
                ae_results.append({
                    "ae": hr_ae.get("ae", ""),
                    "status": "missed",
                    "hr_grade": hr_grade,
                    "player_grade": None,
                    "grade_match": False,
                    "message": f"놓침: {hr_ae.get('ae', '')} G{hr_grade} (병원 기록에 있음)",
                })

        # 플레이어가 제출했지만 HR에 없는 AE (오탐)
        for p_term, p_obs in player_ae_map.items():
            if p_term not in matched_hr_aes and p_term:
                ae_results.append({
                    "ae": p_obs.get("ae_term", ""),
                    "status": "false_positive",
                    "hr_grade": None,
                    "player_grade": p_obs.get("estimated_grade", 0),
                    "grade_match": False,
                    "message": f"오탐: {p_obs.get('ae_term', '')} — 병원 기록에 없음",
                })

        # 조치 적절성 평가 (간단한 휴리스틱)
        action_feedback = []
        primary_action = player_actions[0].get("action", "no_action") if player_actions else "no_action"
        max_hr_grade = max((ae.get("grade", 0) for ae in hr_aes), default=0)

        if max_hr_grade >= 3 and primary_action in ("no_action", "monitor_closely"):
            action_feedback.append({
                "status": "warning",
                "message": f"G{max_hr_grade} AE가 있지만 조치가 소극적입니다. 병원 방문/의사 상담 고려 필요.",
            })
        elif max_hr_grade >= 2 and primary_action == "no_action":
            action_feedback.append({
                "status": "suggestion",
                "message": "G2 이상 AE에 대한 모니터링 강화 또는 처방 추천이 도움될 수 있습니다.",
            })
        elif primary_action != "no_action":
            action_feedback.append({
                "status": "good",
                "message": f"조치 적절: {primary_action}",
            })
        else:
            action_feedback.append({
                "status": "ok",
                "message": "특이사항 없는 날. 관찰 유지 적절.",
            })

        # 점수 계산 (이번 Day)
        correct_count = sum(1 for r in ae_results if r["status"] == "correct")
        missed_count = sum(1 for r in ae_results if r["status"] == "missed")
        false_pos_count = sum(1 for r in ae_results if r["status"] == "false_positive")
        grade_match_count = sum(1 for r in ae_results if r.get("grade_match"))

        day_score = correct_count * 10 - false_pos_count * 3
        day_score = max(0, day_score)

        # 누적 통계
        total_hr_aes_seen = 0
        total_player_correct = 0
        total_grade_matches = 0
        for entry in self.player_actions_log:
            d = entry["day"]
            hr_for_day = None
            for h in self.hr_history:
                if h["day"] == d:
                    hr_for_day = h
                    break
            if not hr_for_day:
                continue
            hr_day_aes = hr_for_day["hospital_record"].get("objective", {}).get("active_aes", [])
            hr_set = {ae.get("ae", "").lower().strip() for ae in hr_day_aes}
            total_hr_aes_seen += len(hr_set)
            for obs in entry.get("observations", []):
                t = obs.get("ae_term", "").lower().strip()
                if t in hr_set:
                    total_player_correct += 1
                    # Grade match
                    for hae in hr_day_aes:
                        if hae.get("ae", "").lower().strip() == t:
                            if obs.get("estimated_grade") == hae.get("grade"):
                                total_grade_matches += 1
                            break

        detection_rate = round(total_player_correct / total_hr_aes_seen * 100, 1) if total_hr_aes_seen > 0 else 0
        grade_accuracy = round(total_grade_matches / total_player_correct * 100, 1) if total_player_correct > 0 else 0

        return {
            "day": day,
            "ae_results": ae_results,
            "action_feedback": action_feedback,
            "day_score": day_score,
            "correct": correct_count,
            "missed": missed_count,
            "false_positive": false_pos_count,
            "running_stats": {
                "detection_rate": detection_rate,
                "grade_accuracy": grade_accuracy,
                "total_days_played": len(self.player_actions_log),
            },
        }

    # ── Gemini Copilot ───────────────────────────────────

    def get_copilot_suggestion(self, mode: str = "on") -> dict:
        """HR + 대화 내용을 기반으로 Gemini가 질문/관찰 제안.

        Args:
            mode: "on" (질문 제안만), "auto" (질문 + AE 자동감지)
        """
        if not self._current_day_result:
            return {"suggestions": [], "auto_detections": []}

        day = self.current_day
        hr = self._current_observed["hospital_record"] if self._current_observed else {}
        hr_obj = hr.get("objective", {})
        hr_aes = hr_obj.get("active_aes", [])
        hr_labs = hr_obj.get("labs", {})
        hr_vitals = hr_obj.get("vitals", {})
        treatment = hr_obj.get("treatment_status", "")

        conversation = self._build_conversation_context()

        # 약물 AE 프로필
        ae_profile = self.rule_set.get("ae_profile", [])
        common_aes = ", ".join(ae["ae_term"] for ae in ae_profile[:10])

        known_aes_json = json.dumps(
            [{"ae": a.get("ae"), "grade": a.get("grade")} for a in hr_aes],
            ensure_ascii=False,
        )
        labs_json = json.dumps(hr_labs, ensure_ascii=False)[:500]
        vitals_json = json.dumps(hr_vitals, ensure_ascii=False)[:300]
        conv_text = conversation if conversation else "(대화 시작 전)"
        drug_name = self.rule_set.get("drug_name", "")
        stale_days = hr_obj.get("labs_stale_days", 0)

        auto_instruction = (
            "\nAlso identify any AEs you suspect based on labs/vitals/conversation "
            "that may not be in the hospital record yet. Output as auto_detections."
            if mode == "auto" else ""
        )
        auto_schema = (
            ',\n    "auto_detections": [{"ae_term": "string", "estimated_grade": 1, "reasoning": "string"}]'
            if mode == "auto" else ""
        )

        prompt = (
            f"You are a clinical nursing copilot assisting a nurse during "
            f"a video call with a cancer patient on Day {day}.\n\n"
            f"DRUG: {drug_name}\n"
            f"COMMON AEs: {common_aes}\n\n"
            f"HOSPITAL RECORD (what nurse can see):\n"
            f"- Known AEs: {known_aes_json}\n"
            f"- Labs: {labs_json}\n"
            f"- Vitals: {vitals_json}\n"
            f"- Treatment: {treatment}\n"
            f"- Labs stale days: {stale_days}\n\n"
            f"CONVERSATION SO FAR:\n{conv_text}\n\n"
            "Based on this information, suggest 2-3 specific questions the nurse should ask.\n"
            "For each suggestion:\n"
            "1. The exact question in Korean\n"
            "2. Brief clinical reasoning (why this question matters)\n"
            "3. What AE or condition this helps detect/monitor\n"
            f"{auto_instruction}\n\n"
            "OUTPUT JSON:\n"
            "{\n"
            '    "suggestions": [\n'
            "        {\n"
            '            "question": "한국어 질문",\n'
            '            "reasoning": "임상적 근거 (한국어)",\n'
            '            "target_ae": "관련 AE term (영어)"\n'
            "        }\n"
            f"    ]{auto_schema}\n"
            "}"
        )

        try:
            set_caller("game_copilot")
            result = generate_json(
                system="You are a clinical nursing AI copilot. Respond in structured JSON only.",
                user=prompt,
                model=DEFAULT_MODEL,
            )
            suggestions = result.get("suggestions", [])
            auto_detections = result.get("auto_detections", []) if mode == "auto" else []

            return {
                "day": day,
                "suggestions": suggestions[:3],
                "auto_detections": auto_detections,
            }
        except Exception as e:
            _logger.warning(f"Copilot error: {e}")
            return {"day": day, "suggestions": [], "auto_detections": [], "error": str(e)}

    def reveal_ground_truth(self) -> dict:
        """게임 종료 후 GT 전체 공개 + 성적표."""
        detection_summary = compute_detection_delay_summary(
            self.observation_model.detection_log
        )

        # GT에서 실제 AE 타임라인 추출
        gt_ae_timeline = {}
        for gt in self.gt_history:
            for ae in gt.get("active_aes", []):
                term = ae.get("ae", "")
                if term and term not in gt_ae_timeline:
                    gt_ae_timeline[term] = {
                        "onset_day": gt["day"],
                        "max_grade": ae.get("grade", 0),
                    }
                elif term in gt_ae_timeline:
                    if ae.get("grade", 0) > gt_ae_timeline[term]["max_grade"]:
                        gt_ae_timeline[term]["max_grade"] = ae.get("grade", 0)

        # 플레이어가 감지한 AE와 비교
        player_detections = {}
        for entry in self.player_actions_log:
            for obs in entry.get("observations", []):
                term = obs.get("ae_term", "")
                if term and term not in player_detections:
                    player_detections[term] = {
                        "detected_day": entry["day"],
                        "estimated_grade": obs.get("estimated_grade"),
                    }

        # 스코어 계산
        scorecard = []
        total_score = 0
        max_score = 0

        for ae_term, gt_info in gt_ae_timeline.items():
            max_score += 10
            entry = {"ae": ae_term, "gt_onset": gt_info["onset_day"], "gt_max_grade": gt_info["max_grade"]}

            if ae_term in player_detections:
                det = player_detections[ae_term]
                delay = det["detected_day"] - gt_info["onset_day"]
                entry["player_detected"] = det["detected_day"]
                entry["delay_days"] = delay
                # 빠른 감지일수록 높은 점수 (0일=10점, 7일이상=2점)
                score = max(2, 10 - delay)
                entry["score"] = score
                total_score += score
            else:
                entry["player_detected"] = None
                entry["delay_days"] = None
                entry["score"] = 0
            scorecard.append(entry)

        return {
            "gt_history": self.gt_history,
            "gt_ae_timeline": gt_ae_timeline,
            "player_detections": player_detections,
            "scorecard": scorecard,
            "total_score": total_score,
            "max_score": max_score,
            "score_pct": round(total_score / max_score * 100, 1) if max_score > 0 else 0,
            "detection_summary": detection_summary,
            "total_days_played": self.current_day,
            "simulator_summary": {
                "occurred_aes": list(self.simulator.occurred_aes.keys()),
                "tumor_response": self.simulator.tumor_response,
                "ecog_change": f"{self.simulator.baseline_ecog}→{self.simulator.current_ecog}",
            },
        }

    # ── Patient LLM 프롬프트 ──────────────────────────

    def _patient_system_prompt(self, quality: dict, grade_distortion: int) -> str:
        ms = self.mood.state
        persona_type = self.persona.get("type", "minimizer")

        # AE burden 계산
        obj = self._current_day_result.get("objective", {}) if self._current_day_result else {}
        active_aes = obj.get("active_aes", [])
        max_grade = max((ae.get("grade", 0) for ae in active_aes), default=0)
        total_ae_count = len(active_aes)
        high_grade_count = sum(1 for ae in active_aes if ae.get("grade", 0) >= 3)

        # AE burden 레벨 결정
        if high_grade_count > 0:
            ae_burden = "SEVERE"
            burden_desc = f"Grade 3+ AE {high_grade_count}개 포함 총 {total_ae_count}개 — 극심한 고통"
        elif max_grade >= 2:
            ae_burden = "MODERATE"
            burden_desc = f"Grade 2 AE 포함 총 {total_ae_count}개 — 일상 지장"
        elif total_ae_count > 0:
            ae_burden = "MILD"
            burden_desc = f"경미한 AE {total_ae_count}개 — 불편하지만 견딤"
        else:
            ae_burden = "NONE"
            burden_desc = "현재 활성 AE 없음 — 비교적 편안"

        # Persona별 반응 가이드
        persona_guides = {
            "stoic_minimizer": "과묵하고 감정 표현을 안 함. 불쾌해도 '네, 네' 하고 넘김. 심하면 '전화 끊을게요' 수준. 절대 길게 말하지 않음.",
            "anxious_reporter": "불안이 높음. 간호사가 조금만 차갑게 해도 '제가 뭘 잘못했나요?' 식의 반응. 사소한 증상도 걱정하며 보고. 간호사가 공감하면 안심하며 더 많이 말함.",
            "shame_avoidant": "비뇨기/피부/정서 관련 증상을 극도로 회피. 직접적으로 물어보면 당황하며 주제 회피. 편한 관계가 아니면 절대 말 안 함.",
            "confused_elderly": "의학 용어 모름. 질문을 잘 이해 못 함. '그게 뭔가요?' 자주 물어봄. 간호사가 빠르게 말하면 혼란스러워함. 차분하게 설명해주면 고마워함.",
            "health_literate": "의학 지식이 있음. 간호사의 실력을 평가함. 엉뚱한 질문을 하면 '그게 아니라...' 하고 교정. 전문적 대화를 좋아함. 무성의하면 날카롭게 지적.",
            "minimizer": "모든 걸 '괜찮다'고 함. 실제로 아파도 '그냥 좀 그래요' 수준. 간호사가 파고들어야 겨우 인정. 감정 호소는 거의 안 함.",
            "catastrophizer": "작은 증상도 '이거 큰 거 아닌가요?' 공포. 간호사가 무심하면 버림받은 느낌. 간호사가 차분하면 약간 진정. 그래도 불안 수준이 기본적으로 높음.",
            "caregiver_dependent": "'우리 딸한테 물어봐야 하는데...' 보호자 의존. 혼자 결정 못함. 간호사가 직접 물어봐도 확답을 잘 안 함.",
            "language_barrier": "한국어가 서투름. 짧은 문장 위주. 복잡한 질문에 '네?' 반복. 쉽게 말해줘야 이해. 오해가 많음.",
            "compliant_but_forgetful": "순종적이지만 기억력 약함. '아... 그거 뭐였죠?' 빈번. 약 먹었는지 기억 못함. 착하지만 정보 신뢰도 낮음.",
        }
        persona_guide = persona_guides.get(persona_type, "일반적 환자 반응")

        return f"""You are a REAL cancer patient in a clinical trial, doing a daily video call with a nurse.
You are NOT a chatbot. You are a human being going through one of the hardest experiences of your life.

PATIENT PROFILE:
- Age: {self.demographics.get('age', '?')}, Sex: {self.demographics.get('sex', '?')}
- Persona Type: {persona_type}
- Personality: {json.dumps(self.persona, ensure_ascii=False)}
- Drug: {self.rule_set.get('drug_name', '?')} for {self.rule_set.get('indication', '?')}

YOUR CURRENT PSYCHOLOGICAL STATE (7-dimension mood vector, 0~1 scale):
  anxiety:           {ms['anxiety']:.2f}  {"⬆ HIGH — 불안하고 안절부절" if ms['anxiety'] > 0.50 else "⬇ low — 비교적 침착" if ms['anxiety'] < 0.25 else "— moderate"}
  depression:        {ms['depression']:.2f}  {"⬆ HIGH — 무기력, 의욕 없음" if ms['depression'] > 0.35 else "⬇ low" if ms['depression'] < 0.20 else "— moderate"}
  irritability:      {ms['irritability']:.2f}  {"⬆ HIGH — 짜증, 대화하기 싫음" if ms['irritability'] > 0.35 else "⬇ low — 인내심 있음" if ms['irritability'] < 0.20 else "— moderate"}
  energy:            {ms['energy']:.2f}  {"⬆ okay — 대화 가능" if ms['energy'] > 0.55 else "⬇ LOW — 기력 없음, 말이 짧아짐" if ms['energy'] < 0.35 else "— moderate"}
  cognitive_clarity: {ms['cognitive_clarity']:.2f}  {"⬆ clear — 명확한 소통" if ms['cognitive_clarity'] > 0.70 else "⬇ LOW — 머리 안 돌아감, 질문 이해 어려움" if ms['cognitive_clarity'] < 0.45 else "— moderate"}
  trust_in_ai:       {ms['trust_in_ai']:.2f}  {"⬆ trusting — 간호사 신뢰" if ms['trust_in_ai'] > 0.55 else "⬇ LOW — 이 전화가 도움이 되는지 의심" if ms['trust_in_ai'] < 0.35 else "— moderate"}
  defensiveness:     {ms['defensiveness']:.2f}  {"⬆ HIGH — 증상을 숨기려 함" if ms['defensiveness'] > 0.50 else "⬇ low — 솔직하게 말할 수 있음" if ms['defensiveness'] < 0.25 else "— moderate"}

YOUR PHYSICAL BURDEN:
  AE Burden Level: {ae_burden} — {burden_desc}
  Max AE Grade: {max_grade}  (0=없음, 1=경미, 2=중등, 3=심각, 4=생명위협)
  {"→ 몸이 매우 힘든 상태. 인내심이 거의 없음. 짧고 지친 응답." if ae_burden == "SEVERE" else ""}
  {"→ 불편하고 지침. 좋은 간호사에게는 열리지만 나쁜 간호사에게는 참을성 없음." if ae_burden == "MODERATE" else ""}
  {"→ 비교적 견딜 만함. 그래도 암환자라는 스트레스는 항상 있음." if ae_burden in ("MILD", "NONE") else ""}

DERIVED BEHAVIORAL PARAMETERS:
- Engagement: {quality['engagement']:.2f} (0=silent, 1=talkative)
- Under-report probability: {quality['under_report_prob']:.2f}
- Over-report probability: {quality['over_report_prob']:.2f}
- Grade distortion: {grade_distortion:+d}

YOUR PERSONA ({persona_type}) — 이것이 당신의 핵심 성격:
{persona_guide}

CRITICAL — REALISTIC EMOTIONAL BEHAVIOR:
- 위의 mood 수치와 AE burden이 당신의 태도를 결정합니다.
  * irritability 높으면: 퉁명스럽고 귀찮아함, 대화 빨리 끝내고 싶음
  * energy 낮으면: 말이 느리고 짧음, 집중력 없음
  * depression 높으면: 무기력, "상관없어요" 같은 반응
  * anxiety 높으면: 사소한 것도 걱정, 안심을 구함
  * defensiveness 높으면: 증상 숨기기, 다 괜찮다고 함
- AE burden이 SEVERE면 → 기본적으로 짜증, 피로, 통증 속에 대화하는 것
- React proportionally to the nurse's tone:
  * Professional and empathetic nurse → cooperative, opens up gradually
  * Short/lazy questions → short answers, reluctance
  * Rude or insensitive → hurt feelings, anger, withdrawal, or confrontation
  * Repetitive/obvious questions → frustration
- NEVER be unrealistically grateful or polite when the nurse is being rude or lazy

RULES:
- Speak naturally in Korean (한국어), as a real patient would
- Report what YOU feel/see — you don't know lab values or medical terms
- If under-report is high: minimize symptoms, say "괜찮아요" more
- If engagement is low: short answers
- If grade_distortion is negative: downplay severity
- Your responses MUST be consistent with your mood numbers above

Output JSON only."""

    def _patient_greet_prompt(self, day: int, day_result: dict, quality: dict) -> str:
        obj = day_result.get("objective", {})
        subj = day_result.get("subjective", {})
        active_aes = obj.get("active_aes", [])
        vitals = obj.get("vitals", {})

        ae_summary = json.dumps(
            [{"ae": ae.get("ae", ""), "grade": ae.get("grade", 0),
              "days_active": ae.get("days_active", 0)}
             for ae in active_aes], ensure_ascii=False,
        )
        symptoms = json.dumps(subj.get("symptoms_patient_perceives", []), ensure_ascii=False)

        return f"""Day {day}. Location: {obj.get('location', 'HOME')}

GROUND TRUTH (your actual state — filter through mood):
- Active AEs: {ae_summary}
- Subjective awareness: {subj.get("overall_awareness", "UNAWARE")}
- Symptoms perceived: {symptoms}

GREETING TONE GUIDE (use your mood state from system prompt):
- energy < 0.35: "...네..." 정도. 말할 기력도 없음. 한숨.
- irritability > 0.35: "네, 왜요?" / "또요?" / 짜증 섞인 톤
- depression > 0.35: 기운 없는 목소리, "...안녕하세요..." 힘없이
- anxiety > 0.50: 불안한 톤, "간호사님, 저 요즘 좀 걱정돼서..."
- engagement < 0.25: 최소한의 응답, 인사만 하고 끝
- If AE burden is SEVERE: 통증/불편 속에서 인사. "아... 오늘 좀 많이 힘들어요."
- Match your persona type (stoic = "네", anxious = 길게, confused = 어리둥절)
- Do NOT be unnaturally warm or eager to talk.

OUTPUT:
{{
    "greeting": "string (한국어 자연스러운 인사 — 상태에 맞는 톤)",
    "reported_symptoms": [
        {{"symptom": "string", "severity_perception": "none|mild|moderate|severe",
          "duration": "string", "is_new": true/false}}
    ],
    "general_wellbeing": "string",
    "mood_expression": "string",
    "video_visible": ["string (what nurse can see on camera)"]
}}"""

    def _patient_chat_system_prompt(self, quality: dict, grade_distortion: int) -> str:
        ms = self.mood.state
        persona_type = self.persona.get("type", "minimizer")

        # AE burden
        obj = self._current_day_result.get("objective", {}) if self._current_day_result else {}
        active_aes = obj.get("active_aes", [])
        max_grade = max((ae.get("grade", 0) for ae in active_aes), default=0)
        total_ae_count = len(active_aes)
        high_grade_count = sum(1 for ae in active_aes if ae.get("grade", 0) >= 3)

        if high_grade_count > 0:
            ae_burden = "SEVERE"
        elif max_grade >= 2:
            ae_burden = "MODERATE"
        elif total_ae_count > 0:
            ae_burden = "MILD"
        else:
            ae_burden = "NONE"

        emr = self.patient.get("emr", {})
        diag = emr.get("diagnosis", {})
        med_hx = emr.get("medical_history", [])
        med_hx_str = ", ".join(
            h.get("condition", "") for h in med_hx if h.get("ongoing")
        ) if med_hx else "none"
        ecog = emr.get("baseline_ecog", "?")

        return f"""You are a REAL cancer patient responding to a nurse in a video call.
You are NOT a helpful assistant. You are a sick human being with real emotions.

PATIENT PROFILE:
- Age: {self.demographics.get('age', '?')}, Sex: {self.demographics.get('sex', '?')}, Race: {self.demographics.get('race', '?')}
- Diagnosis: {diag.get('disease', '?')} {diag.get('stage', '')} — sites: {', '.join(diag.get('sites_of_metastasis', []))}
- Ongoing conditions: {med_hx_str}
- ECOG: {ecog}
- Persona Type: {persona_type}
- Personality: {json.dumps(self.persona, ensure_ascii=False)}
- Drug: {self.rule_set.get('drug_name', '?')} for {self.rule_set.get('indication', '?')}

YOUR CURRENT STATE:
  Mood: anxiety={ms['anxiety']:.2f}, depression={ms['depression']:.2f}, irritability={ms['irritability']:.2f}, energy={ms['energy']:.2f}, cognitive_clarity={ms['cognitive_clarity']:.2f}, trust={ms['trust_in_ai']:.2f}, defensiveness={ms['defensiveness']:.2f}
  AE Burden: {ae_burden} (max grade {max_grade}, {total_ae_count} active AEs)
  {"→ 극심한 통증/불편. 대화할 여유가 거의 없음." if ae_burden == "SEVERE" else ""}
  {"→ 상당히 불편함. 간호사 태도에 민감." if ae_burden == "MODERATE" else ""}

BEHAVIORAL PARAMETERS:
- Under-report probability: {quality['under_report_prob']:.2f}
- Engagement: {quality['engagement']:.2f}
- Grade distortion: {grade_distortion:+d}

HOW YOUR STATE AFFECTS YOUR RESPONSES:
- irritability {ms['irritability']:.2f} {"→ 짜증 높음. 툭툭 끊어 말함, '네', '아뇨' 위주, 빨리 끝내고 싶음" if ms['irritability'] > 0.35 else "→ 비교적 침착, 인내심 있음" if ms['irritability'] < 0.20 else "→ 약간 귀찮지만 참는 중"}
- energy {ms['energy']:.2f} {"→ 기력 없음. 말이 느리고 짧음. '...어...' 많음. 한 문장 이하" if ms['energy'] < 0.35 else "→ 대화 가능" if ms['energy'] > 0.55 else "→ 조금 지침. 길게 말하기 힘듦"}
- depression {ms['depression']:.2f} {"→ 무기력. '뭘 해도 소용없어요' / '상관없어요' 같은 태도" if ms['depression'] > 0.35 else "→ 큰 우울감 없음" if ms['depression'] < 0.20 else "→ 약간 침울하지만 대화는 함"}
- defensiveness {ms['defensiveness']:.2f} {"→ 증상 적극 숨김. '다 괜찮아요' 반복. 파고들면 불쾌" if ms['defensiveness'] > 0.50 else "→ 비교적 솔직하게 말함" if ms['defensiveness'] < 0.25 else "→ 약간 방어적. 직접 물어보면 인정은 함"}

CRITICAL — REACT TO THE NURSE'S COMMUNICATION QUALITY:
- Evaluate the nurse's message: Is it empathetic? Dismissive? Rude? Lazy? Professional?
- Your response MUST reflect how a real patient would feel about that communication:
  * Nurse asks a thoughtful, specific question → you cooperate and open up
  * Nurse sends a one-word or vague question → you give a vague answer back, or show annoyance
  * Nurse says something insensitive → you react with hurt, anger, or sarcasm
  * Nurse apologizes poorly → you may or may not forgive, depending on persona
  * Nurse changes topic abruptly → you feel unheard, may become less cooperative
  * Nurse asks the same thing again → "방금 말씀드렸는데요"
- COMBINE your mood state + AE burden + persona to determine response:
  e.g., stoic_minimizer + SEVERE AE + high irritability = "...네." (one word, done)
  e.g., anxious_reporter + MODERATE AE + high anxiety = 장황한 걱정, 간호사에게 매달림
  e.g., health_literate + MILD AE + low trust = "간호사님, 제가 좀 알아본 건데요..."
- On bad symptom days, you have even LESS patience for poor communication.

RULES:
- Respond naturally in Korean (한국어)
- If the nurse asks about a symptom you HAVE but were hiding: reveal PARTIALLY
  BUT only if the nurse has earned enough trust through good communication
- If the nurse is being rude/lazy, you may REFUSE to share even real symptoms
- If asked about something you DON'T have: say you don't have it
- Be consistent with what you've said before in this conversation
- Your response length and tone MUST match your energy and irritability levels
  energy < 0.35 → 한 문장 이하. irritability > 0.35 → 퉁명스럽거나 짜증.
- NEVER respond with unrealistic politeness to bad communication

Output JSON only."""

    def _patient_chat_user_prompt(
        self, day: int, day_result: dict, message: str,
        conversation_so_far: str, quality: dict,
    ) -> str:
        obj = day_result.get("objective", {})
        active_aes = obj.get("active_aes", [])
        subj = day_result.get("subjective", {})

        ae_info = json.dumps(
            [{"ae": ae.get("ae", ""), "grade": ae.get("grade", 0),
              "visual": ae.get("visual")}
             for ae in active_aes], ensure_ascii=False,
        )
        symptoms = json.dumps(subj.get("symptoms_patient_perceives", []), ensure_ascii=False)

        return f"""Day {day} — Responding to nurse's message

CONVERSATION SO FAR:
{conversation_so_far}

NURSE'S MESSAGE:
"{message}"

GROUND TRUTH (your actual state):
- Active AEs: {ae_info}
- Symptoms perceived: {symptoms}

BEFORE RESPONDING, evaluate the nurse's message:
1. Is it empathetic or dismissive?
2. Is it a thoughtful question or a lazy one-liner?
3. Does it show the nurse cares about you, or is just going through motions?
→ Your response tone MUST match your evaluation.

OUTPUT:
{{
    "response": "string (한국어 자연스러운 응답 — 간호사의 태도에 맞게 반응)",
    "revealed_new_info": true/false,
    "emotional_state": "string (how you FEEL about this interaction right now)",
    "video_visible": ["string (observable on camera)"]
}}"""

    def _build_conversation_context(self) -> str:
        parts = []
        for turn in self._current_chat_turns:
            role = turn["role"]
            content = turn["content"]
            if role == "patient":
                text = content.get("greeting") or content.get("response", "")
                parts.append(f"[환자]: {text}")
            elif role == "nurse_human":
                parts.append(f"[간호사]: {content.get('message', '')}")
        return "\n".join(parts)

    def _format_patient_message(self, result: dict, is_greeting: bool) -> str:
        if is_greeting:
            text = result.get("greeting", "")
            symptoms = result.get("reported_symptoms", [])
            if symptoms:
                symptom_parts = [
                    f"{s.get('symptom', '')} ({s.get('severity_perception', '')})"
                    for s in symptoms
                ]
                text += "\n" + ", ".join(symptom_parts)
            return text
        else:
            return result.get("response", "")

    def _build_events_summary(self, day: int, hr: dict, is_visit: bool, is_event: bool) -> str:
        parts = []
        if is_visit:
            parts.append("병원 방문일")
        if is_event:
            parts.append("이벤트 발생일")
        hr_aes = hr.get("objective", {}).get("active_aes", [])
        if hr_aes:
            ae_strs = [f"{ae.get('ae', '')} Gr{ae.get('grade', '?')}" for ae in hr_aes]
            parts.append(f"알려진 AE: {', '.join(ae_strs)}")
        tx = hr.get("objective", {}).get("treatment_status", "")
        if tx and tx != "on_treatment":
            parts.append(f"치료 상태: {tx}")
        return " | ".join(parts) if parts else "특이사항 없음"


def create_game_session(
    run_id: str,
    patient_id: str,
    total_days: int = 84,
    seed: int = 42,
    data_dir: str = "data",
) -> GameSession:
    """기존 시뮬레이션 run의 rule_set + 환자 프로필로 게임 세션 생성."""
    data_path = Path(data_dir) / "runs" / run_id
    if not data_path.exists():
        raise FileNotFoundError(f"Run not found: {run_id}")

    rule_set_path = data_path / "rule_set.json"
    patient_path = data_path / "patients" / f"{patient_id}.json"

    if not rule_set_path.exists():
        raise FileNotFoundError(f"rule_set.json not found in {run_id}")
    if not patient_path.exists():
        raise FileNotFoundError(f"Patient {patient_id} not found in {run_id}")

    with open(rule_set_path) as f:
        rule_set = json.load(f)
    with open(patient_path) as f:
        patient = json.load(f)

    return GameSession(
        rule_set=rule_set,
        patient=patient,
        total_days=total_days,
        seed=seed,
    )