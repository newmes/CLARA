"""Extended Care Agent — 동적 Mood + 확장 턴 (최대 8턴) 지원

기존 CareAgent의 T1-T4 메서드를 재사용하면서:
  1. 턴별 LLM-as-Judge mood 평가 (hardcoded delta 대체)
  2. 확장 턴 프로토콜: T4에서 final이 아닌 경우 T5-T8 추가 가능
  3. 매 Nurse 턴 후 termination 재판정 (mood 개선 반영)

학습 데이터 생성(SFT/DPO)을 위한 풍부한 메타데이터 기록.
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.agents.care_agent import CareAgent
from src.agents.llm_client import generate_json, set_caller
from src.engine.mood import (
    MoodState,
    compute_interaction_quality,
    compute_grade_distortion,
    MOOD_DIMENSIONS,
)
from src.engine.mood_evaluator import evaluate_nurse_turn, evaluate_conversation_outcome
from src.engine.sampler import Sampler
from src.logger import get_logger

_logger = get_logger("care_agent_ext")

MAX_TURNS = 8
MIN_TURNS = 4


class ExtendedCareAgent(CareAgent):
    """동적 mood 평가 + 확장 턴을 지원하는 CareAgent."""

    def __init__(
        self,
        patient: dict,
        rule_set: dict,
        mood: MoodState,
        sampler: Sampler,
        model: str = "gemini-2.0-flash",
        use_dynamic_mood: bool = True,
        max_turns: int = MAX_TURNS,
    ):
        super().__init__(patient, rule_set, mood, sampler, model)
        self.use_dynamic_mood = use_dynamic_mood
        self.max_turns = max_turns
        self._turn_evaluations: list[dict] = []
        _logger.info(f"[ExtCareAgent] {self.pid} dynamic_mood={use_dynamic_mood} max_turns={max_turns}")

    def conduct_extended_call(
        self,
        day: int,
        day_result: dict,
        day_results: list[dict],
        last_hospital_record: dict | None = None,
    ) -> dict:
        """확장 턴 프로토콜로 영상통화를 시뮬레이션한다.

        Flow:
          T1: Patient initial → termination check #1
          T2: Nurse questions → dynamic mood eval → termination check #2
          T3: Patient response
          T4: Nurse assessment
            → if should_extend AND mood OK → continue:
          T5: Nurse deeper probing
          T6: Patient additional response → termination check #3
          T7-T8: (optional) final round
        """
        set_caller(f"care_agent_ext.{self.pid}")

        obj = day_result.get("objective", {})
        active_aes = obj.get("active_aes", [])
        max_ae_grade = max((ae.get("grade", 0) for ae in active_aes), default=0)

        self._last_hospital_record = last_hospital_record

        quality = compute_interaction_quality(self.mood)
        grade_distortion = compute_grade_distortion(self.mood)
        mood_before = self.mood.to_dict()

        if max_ae_grade >= 3:
            self.mood.apply_defensiveness_override(max_ae_grade)
            quality = compute_interaction_quality(self.mood)

        turns: list[dict] = []
        self._turn_evaluations = []
        terminated_early = False
        mood_snapshots = [("init", self.mood.to_dict())]

        # ═══ T1: Patient initial report ═══
        t1 = self._patient_initial_report(day, day_result, quality, grade_distortion)
        turns.append({"turn": 1, "role": "patient", "content": t1})

        # Termination check #1 (after T1)
        if self.sampler.boolean(quality["early_termination_prob"]):
            _logger.info(f"[{self.pid}] Day {day}: T1 early termination (prob={quality['early_termination_prob']:.2f})")
            terminated_early = True
            t_final = self._nurse_final_assessment(day, turns, quality)
            turns.append({"turn": 2, "role": "nurse", "content": t_final})
            self.mood.update_turn({"irritability": +0.05, "trust_in_ai": -0.03})
        else:
            # ═══ T2: Nurse follow-up questions ═══
            t2 = self._nurse_followup_questions(day, t1, quality)
            turns.append({"turn": 2, "role": "nurse", "content": t2})

            # Dynamic mood evaluation after T2
            eval_t2 = self._apply_dynamic_mood(t2, t1, turns, 2)
            quality = compute_interaction_quality(self.mood)
            mood_snapshots.append(("post_T2", self.mood.to_dict()))

            # Termination check #2 (after T2, with updated mood)
            if self.sampler.boolean(quality["early_termination_prob"]):
                _logger.info(f"[{self.pid}] Day {day}: T2 mid-termination (prob={quality['early_termination_prob']:.2f})")
                terminated_early = True
                t_final = self._nurse_final_assessment(day, turns, quality)
                turns.append({"turn": 3, "role": "nurse", "content": t_final})
            else:
                # ═══ T3: Patient follow-up response ═══
                quality_t3 = compute_interaction_quality(self.mood)
                t3 = self._patient_followup_response(day, day_result, t2, quality_t3, grade_distortion)
                turns.append({"turn": 3, "role": "patient", "content": t3})

                if t3.get("new_info_revealed"):
                    self.mood.update_turn({"defensiveness": -0.05, "anxiety": +0.03})

                mood_snapshots.append(("post_T3", self.mood.to_dict()))

                # ═══ Decide: extend or finalize at T4 ═══
                should_extend = (
                    eval_t2.get("should_extend", False)
                    and self.mood.state.get("energy", 0) > 0.3
                    and self.mood.state.get("irritability", 1) < 0.6
                    and len(turns) < self.max_turns - 1
                )

                if should_extend:
                    # ═══ T4: Nurse deeper probing (not final) ═══
                    t4_probe = self._nurse_deeper_probing(day, turns, quality_t3)
                    turns.append({"turn": 4, "role": "nurse", "content": t4_probe})

                    eval_t4 = self._apply_dynamic_mood(t4_probe, t3, turns, 4)
                    quality_t4 = compute_interaction_quality(self.mood)
                    mood_snapshots.append(("post_T4_probe", self.mood.to_dict()))

                    # Termination check #3
                    if self.sampler.boolean(quality_t4["early_termination_prob"]):
                        _logger.info(f"[{self.pid}] Day {day}: T4 extended termination")
                        terminated_early = True
                        t_final = self._nurse_final_assessment(day, turns, quality_t4)
                        turns.append({"turn": 5, "role": "nurse", "content": t_final})
                    else:
                        # ═══ T5: Patient additional response ═══
                        quality_t5 = compute_interaction_quality(self.mood)
                        t5 = self._patient_followup_response(day, day_result, t4_probe, quality_t5, grade_distortion)
                        turns.append({"turn": 5, "role": "patient", "content": t5})

                        if t5.get("new_info_revealed"):
                            self.mood.update_turn({"defensiveness": -0.04, "trust_in_ai": +0.03})

                        mood_snapshots.append(("post_T5", self.mood.to_dict()))

                        # ═══ T6: Nurse final assessment ═══
                        quality_t6 = compute_interaction_quality(self.mood)
                        t_final = self._nurse_final_assessment(day, turns, quality_t6)
                        turns.append({"turn": 6, "role": "nurse", "content": t_final})
                else:
                    # ═══ T4: Nurse final assessment (standard) ═══
                    quality_t4 = compute_interaction_quality(self.mood)
                    t_final = self._nurse_final_assessment(day, turns, quality_t4)
                    turns.append({"turn": 4, "role": "nurse", "content": t_final})

                # Dynamic mood eval for final nurse turn
                last_patient = next(
                    (t["content"] for t in reversed(turns) if t["role"] == "patient"), {}
                )
                eval_final = self._apply_dynamic_mood(t_final, last_patient, turns, turns[-1]["turn"])
                mood_snapshots.append(("post_final", self.mood.to_dict()))

        # ═══ Assemble care_record ═══
        assessment = t_final.get("assessment", {})
        actions = t_final.get("actions", [])
        detection = t_final.get("detection", {})

        mood_after = self.mood.to_dict()
        outcome = evaluate_conversation_outcome(
            turns=turns,
            patient_mood_before=mood_before,
            patient_mood_after=mood_after,
            gt_aes=active_aes,
            detected_aes=detection.get("aes_detected", []),
        )

        care_record = {
            "day": day,
            "turns": turns,
            "n_turns": len(turns),
            "terminated_early": terminated_early,
            "interaction_quality": quality,
            "grade_distortion": grade_distortion,
            "mood_before": mood_before,
            "mood_after": mood_after,
            "mood_snapshots": mood_snapshots,
            "mood_snapshot": mood_after,
            "nurse_assessment": assessment,
            "actions": actions,
            "detection": detection,
            "turn_evaluations": self._turn_evaluations,
            "conversation_outcome": outcome,
        }

        self.call_history.append({
            "day": day,
            "severity": assessment.get("severity_level", "?"),
            "summary": assessment.get("summary", ""),
            "actions": [a.get("action", "") for a in actions],
            "terminated_early": terminated_early,
            "n_turns": len(turns),
        })
        if len(self.call_history) > self.max_history:
            self.call_history = self.call_history[-self.max_history:]

        _logger.info(
            f"[{self.pid}] Day {day} extended call: {len(turns)} turns, "
            f"early_stop={terminated_early}, outcome={outcome['overall_grade']}, "
            f"mood_improve={outcome['mood_improvement']:+.3f}"
        )

        return care_record

    # ── 동적 mood 평가 적용 ───────────────────────────────

    def _apply_dynamic_mood(
        self,
        nurse_utterance: dict,
        patient_utterance: dict,
        turns: list[dict],
        turn_number: int,
    ) -> dict:
        """Nurse 턴 후 LLM Judge로 mood를 동적으로 업데이트한다."""
        if not self.use_dynamic_mood:
            approach = nurse_utterance.get("approach_style", "neutral")
            from src.agents.care_agent import _nurse_approach_mood_effect
            delta = _nurse_approach_mood_effect(approach)
            self.mood.update_turn(delta)
            return {"mood_delta": delta, "_static": True}

        evaluation = evaluate_nurse_turn(
            nurse_utterance=nurse_utterance,
            patient_utterance=patient_utterance,
            patient_mood_before=self.mood.to_dict(),
            patient_persona=self.persona,
            turn_number=turn_number,
            conversation_history=turns[:-1],
        )

        mood_delta = evaluation.get("mood_delta", {})
        self.mood.update_turn(mood_delta)

        evaluation["turn_number"] = turn_number
        self._turn_evaluations.append(evaluation)

        _logger.info(
            f"[{self.pid}] T{turn_number} eval: quality={evaluation.get('overall_quality', 0):.2f} "
            f"empathy={evaluation.get('empathy_quality', 0):.2f} "
            f"extend={evaluation.get('should_extend', False)}"
        )

        return evaluation

    # ── 확장 턴: Nurse deeper probing ─────────────────────

    def _nurse_deeper_probing(
        self,
        day: int,
        turns: list[dict],
        quality: dict,
    ) -> dict:
        """T4 확장: Nurse가 추가 탐색 질문을 한다 (final이 아님).

        기존 T2보다 더 구체적이고 깊은 질문. 이전 대화 전체를 참조한다.
        """
        recent_summary = self._summarize_recent()
        hospital_summary = self._summarize_hospital_record()

        _nurse_hidden_keys = {"omitted_symptoms", "_turn", "_fallback"}
        conversation_parts = []
        for t in turns:
            role = t["role"].upper()
            turn_n = t["turn"]
            content = t["content"]
            if role == "PATIENT":
                content = {k: v for k, v in content.items() if k not in _nurse_hidden_keys}
            content_summary = json.dumps(content, ensure_ascii=False)[:600]
            conversation_parts.append(f"[T{turn_n} {role}]: {content_summary}")
        transcript = "\n".join(conversation_parts)

        system_prompt = f"""You are an AI nurse conducting an EXTENDED follow-up in a daily video call.
The standard questions have been asked. Now you're probing DEEPER based on what you've learned so far.

CLINICAL CONTEXT:
- Drug: {self.rule_set.get('drug_name', '?')}
- Indication: {self.rule_set.get('indication', '?')}
- Known AEs: {json.dumps(self.known_aes, ensure_ascii=False)}

HOSPITAL RECORD:
{hospital_summary}

PATIENT INTERACTION METRICS:
- Under-report probability: {quality['under_report_prob']:.2f}
- Video cooperation: {quality['video_cooperation']:.2f}
- Engagement: {quality['engagement']:.2f}

MOTIVATIONAL INTERVIEWING STRATEGY:
- Use OPEN-ENDED questions to invite the patient to share more
- AFFIRM the patient's willingness to talk and their coping efforts
- REFLECT back what you've heard to build trust
- SUMMARIZE to show you've been listening carefully
- Be GENTLE — the patient is still guarded but opening up
- Focus on UNRESOLVED concerns from the earlier exchange
- Ask about daily FUNCTIONING (eating, sleeping, walking) — patients find this easier to answer

Maximum 2 focused questions. Keep it warm and unhurried.

Output JSON only."""

        user_prompt = f"""DAY {day} — EXTENDED TURN (deeper probing)

CONVERSATION SO FAR:
{transcript}

RECENT CALL HISTORY:
{recent_summary}

OUTPUT:
{{
    "approach_style": "empathetic|neutral|concerned",
    "reflection": "string (reflect back what patient said — show you listened)",
    "affirmation": "string (acknowledge something positive about the patient)",
    "questions": [
        {{
            "question": "string (open-ended, non-threatening)",
            "target_ae": "string|null",
            "requires_visual": true/false,
            "rationale": "string"
        }}
    ],
    "visual_request": {{
        "requested": true/false,
        "body_area": "string|null",
        "reason": "string|null"
    }},
    "conversation_summary": "string (brief summary of what you've learned so far)"
}}"""

        try:
            result = generate_json(system_prompt, user_prompt, model=self.model, max_tokens=2048)
        except Exception as e:
            _logger.error(f"[{self.pid}] Extended probing LLM failed: {e}")
            result = {
                "approach_style": "empathetic",
                "reflection": "I appreciate you sharing all of this with me.",
                "affirmation": "You're doing well managing these challenges.",
                "questions": [],
                "visual_request": {"requested": False, "body_area": None, "reason": None},
                "conversation_summary": "Continued assessment.",
                "_fallback": True,
            }
        result["_turn_type"] = "extended_probing"
        return result
