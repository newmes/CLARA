"""Doc Agent service — high-level API for document generation.

Bridges the simulation system with the doc_agent workflow.
Can be called from Django views, orchestrator, or CLI.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .config import Settings
from .e2b_converter import convert_to_e2b_xml
from .meddra_coder import code_meddra
from .medwatch_mapper import map_crf_to_medwatch
from .medwatch_pdf import generate_medwatch_pdf
from .schemas.agent_output import MedDRACode, SentinelOutput
from .schemas.crf import CRFData
from .schemas.medwatch import MedWatch3500A
from .sim_to_crf_adapter import build_crf_for_sae, find_serious_aes

logger = logging.getLogger(__name__)

# Where generated documents are saved
DOCS_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "documents"


def _clean_narrative(narrative: str, _re) -> str:
    """Clean up 4B model B5 narrative output artifacts.

    Handles: CRF raw data echo, duplicate paragraphs/sentences,
    repetition loops, and markdown remnants.
    """
    # 0) Truncate at CRF raw data echo (model copies input data after narrative)
    crf_echo_patterns = [
        r"^Term:\s", r"^Onset:\s", r"^Severity:\s", r"^CTCAE Grade:\s",
        r"^Serious:\s", r"^Causality:\s", r"^Action taken:\s",
        r"^Outcome:\s", r"^SAE criteria:\s",
        r"^Anc:\s", r"^Hemoglobin:\s", r"^Platelets:\s",
    ]
    lines = narrative.split("\n")
    cut_idx = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        for pat in crf_echo_patterns:
            if _re.match(pat, stripped, flags=_re.IGNORECASE):
                cut_idx = i
                break
        if cut_idx < len(lines):
            break
    narrative = "\n".join(lines[:cut_idx]).strip()

    # 1) Deduplicate paragraphs (catches block-level repetition)
    paragraphs = _re.split(r"\n\n+", narrative)
    seen_paras: set[str] = set()
    unique_paras: list[str] = []
    for p in paragraphs:
        norm = p.strip()
        if not norm:
            continue
        if norm in seen_paras:
            continue
        seen_paras.add(norm)
        unique_paras.append(p)
    narrative = "\n\n".join(unique_paras)

    # 2) Deduplicate lines (catches line-level repetition)
    lines = narrative.split("\n")
    seen_lines: set[str] = set()
    unique_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            unique_lines.append(line)
            continue
        if stripped.startswith("Note:") or stripped in seen_lines:
            if stripped in seen_lines:
                continue
        seen_lines.add(stripped)
        unique_lines.append(line)
    narrative = "\n".join(unique_lines).strip()

    # 3) Detect repeated opening sentence and truncate
    opening = narrative.split(".")[0] if "." in narrative else ""
    if opening and len(opening) > 20:
        second_occurrence = narrative.find(opening, len(opening) + 1)
        if second_occurrence > 0:
            narrative = narrative[:second_occurrence].strip()

    return narrative


def _parse_c7c8(raw: str) -> str:
    """Extract answer + rationale text from C7/C8 JSON response.

    Handles thinking tokens (<unused*>), verbose analysis, and markdown
    code fences that MedGemma models may emit before the actual JSON.
    """
    import json as _json, re as _re
    text = raw.strip()
    # Strip thinking tokens (e.g. <unused94>thought...</unused94>)
    text = _re.sub(r"<unused\d+>.*?</unused\d+>", "", text, flags=_re.DOTALL)
    text = text.strip()
    # Try to find JSON object anywhere in the text
    json_match = _re.search(r"\{[^{}]*(?:\"c[78]_answer\"|\"c[78]_rationale\")[^{}]*\}", text, flags=_re.DOTALL)
    if json_match:
        try:
            obj = _json.loads(json_match.group())
            answer = obj.get("c7_answer") or obj.get("c8_answer") or ""
            rationale = obj.get("c7_rationale") or obj.get("c8_rationale") or ""
            if answer and rationale:
                return f"{answer}. {rationale}"
            return answer or rationale or raw
        except (_json.JSONDecodeError, AttributeError):
            pass
    # Fallback: strip markdown fences and try full parse
    if "```" in text:
        fenced = _re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, flags=_re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
    try:
        obj = _json.loads(text)
        answer = obj.get("c7_answer") or obj.get("c8_answer") or ""
        rationale = obj.get("c7_rationale") or obj.get("c8_rationale") or ""
        if answer and rationale:
            return f"{answer}. {rationale}"
        return answer or rationale or raw
    except (_json.JSONDecodeError, AttributeError):
        return raw


def _try_ai_generation(crf: CRFData, settings: Settings) -> dict[str, str]:
    """Attempt AI-powered narrative/dechallenge/rechallenge.

    Falls back to placeholder text if vLLM or Agno is unavailable.
    """
    try:
        from .agent import format_b5_prompt, format_c7_prompt, format_c8_prompt
        from agno.agent.agent import Agent
        from agno.models.openai.like import OpenAILike

        model = OpenAILike(
            id=settings.VLLM_MODEL_ID,
            base_url=settings.VLLM_BASE_URL,
            api_key=settings.VLLM_API_KEY,
            max_tokens=settings.MAX_TOKENS_NARRATIVE,
            temperature=settings.TEMPERATURE_NARRATIVE,
        )

        import re as _re

        b5_prompt = format_b5_prompt(crf, settings)
        agent = Agent(name="b5_narrative", model=model, markdown=False)
        result = agent.run(b5_prompt)
        narrative = str(result.content).strip()
        # Strip markdown formatting that 4B models may emit
        narrative = _re.sub(r"^#{1,6}\s+.*\n?", "", narrative, flags=_re.MULTILINE)
        narrative = _re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", narrative)
        narrative = _re.sub(r"^[-*]\s+", "", narrative, flags=_re.MULTILINE)
        narrative = _re.sub(r"\n{3,}", "\n\n", narrative).strip()
        # Post-process 4B model output artifacts
        narrative = _clean_narrative(narrative, _re)

        structured_model = OpenAILike(
            id=settings.VLLM_MODEL_ID,
            base_url=settings.VLLM_BASE_URL,
            api_key=settings.VLLM_API_KEY,
            max_tokens=settings.MAX_TOKENS_STRUCTURED,
            temperature=settings.TEMPERATURE_STRUCTURED,
        )

        c7_prompt = format_c7_prompt(crf)
        c7_agent = Agent(name="c7", model=structured_model, markdown=False)
        c7_result = c7_agent.run(c7_prompt)
        dechallenge = _parse_c7c8(str(c7_result.content))

        c8_prompt = format_c8_prompt(crf)
        # Rechallenge requires a sequential restart pattern (stop → restart)
        # Concurrent drugs (same start date) in a regimen are NOT rechallenge
        has_rechallenge_pattern = False
        if len(crf.ec) >= 2:
            for ec in crf.ec:
                if ec.ECENDAT:
                    for other in crf.ec:
                        if other is not ec and other.ECSTDAT and other.ECSTDAT > ec.ECENDAT:
                            has_rechallenge_pattern = True
                            break
                if has_rechallenge_pattern:
                    break
        rechallenge = "Does not apply — drug was not re-administered"
        if has_rechallenge_pattern:
            c8_agent = Agent(name="c8", model=structured_model, markdown=False)
            c8_result = c8_agent.run(c8_prompt)
            rechallenge = _parse_c7c8(str(c8_result.content))

        return {
            "narrative": narrative,
            "dechallenge": dechallenge,
            "rechallenge": rechallenge,
            "ai_used": True,
        }
    except Exception as exc:
        logger.warning("AI generation failed (vLLM may be offline): %s", exc)
        return _deterministic_fallback(crf, settings)


def _deterministic_fallback(crf: CRFData, settings: Settings) -> dict[str, str]:
    """Generate placeholder narrative from CRF data without LLM."""
    dm = crf.dm
    ae = crf.ae

    narrative = (
        f"The subject ({dm.SUBJID}) is a {dm.AGE}-year-old {dm.SEX.lower()} "
        f"enrolled in protocol {settings.PROTOCOL_NUMBER}. "
        f"The subject was receiving {settings.DRUG_NAME} for {settings.INDICATION}. "
        f"On {ae.AESTDAT.strftime('%d-%b-%Y').upper() if ae.AESTDAT else 'N/A'}, "
        f"the subject experienced {ae.AETERM} (CTCAE Grade {ae.AETOXGR or 'N/A'}, "
        f"severity: {ae.AESEV}). "
        f"Action taken: {ae.AEACN.lower().replace('_', ' ')}. "
        f"Outcome: {ae.AEOUT.lower()}."
    )

    # Dechallenge
    if ae.AEACN in ("DRUG WITHDRAWN", "DRUG INTERRUPTED", "DOSE REDUCED"):
        if ae.AEOUT in ("RECOVERED/RESOLVED", "RECOVERING/RESOLVING", "RECOVERED/RESOLVED WITH SEQUELAE"):
            dc = f"Yes — reaction abated after {ae.AEACN.lower().replace('_', ' ')}"
        elif ae.AEOUT == "FATAL":
            dc = "No — patient died"
        else:
            dc = f"No — reaction did not abate after {ae.AEACN.lower().replace('_', ' ')}"
    else:
        dc = "Does not apply — drug was not stopped or reduced"

    rc = "Does not apply — drug was not re-administered" if len(crf.ec) < 2 else "Unknown"

    return {
        "narrative": narrative,
        "dechallenge": dc,
        "rechallenge": rc,
        "ai_used": False,
    }


def generate_documents(
    patient_profile: dict,
    day_records: list[dict],
    target_ae_term: str,
    run_id: str = "",
    sim_start_date: date = date(2026, 1, 6),
    drug_name: str = "Enfortumab vedotin (Padcev)",
    indication: str = "Metastatic urothelial carcinoma",
    manufacturer: str = "Astellas / Seagen",
    target_ae_day: Optional[int] = None,
    use_ai: bool = True,
) -> dict[str, Any]:
    """Generate MedWatch 3500A PDF + E2B(R3) XML for a specific SAE.

    Returns:
        {
            "success": bool,
            "patient_id": str,
            "ae_term": str,
            "medwatch_pdf_path": str | None,
            "e2b_xml_path": str | None,
            "medwatch_data": dict,
            "meddra": dict,
            "ai_used": bool,
            "error": str | None,
        }
    """
    patient_id = patient_profile.get("patient_id", "UNK")

    try:
        crf = build_crf_for_sae(
            patient_profile=patient_profile,
            day_records=day_records,
            target_ae_term=target_ae_term,
            sim_start_date=sim_start_date,
            target_ae_day=target_ae_day,
        )

        if crf is None:
            return {
                "success": False,
                "patient_id": patient_id,
                "ae_term": target_ae_term,
                "error": f"SAE '{target_ae_term}' not found in patient records",
            }

        if crf.ae.AESER == "N":
            return {
                "success": False,
                "patient_id": patient_id,
                "ae_term": target_ae_term,
                "error": "AE is not serious (AESER=N). MedWatch 3500A is for SAEs only.",
            }

        settings = Settings.from_simulation(
            drug_name=drug_name,
            indication=indication,
            manufacturer=manufacturer,
        )

        medwatch = map_crf_to_medwatch(crf, settings)

        if use_ai:
            ai_result = _try_ai_generation(crf, settings)
        else:
            ai_result = _deterministic_fallback(crf, settings)

        medwatch.section_b.narrative = ai_result["narrative"]
        medwatch.section_c.dechallenge = ai_result["dechallenge"]
        medwatch.section_c.rechallenge = ai_result["rechallenge"]

        meddra = code_meddra(
            crf.ae.AETERM,
            use_medgemma=use_ai,
            base_url=settings.VLLM_BASE_URL,
            model_id=settings.VLLM_MODEL_ID,
            api_key=settings.VLLM_API_KEY,
        )

        e2b_xml = convert_to_e2b_xml(medwatch, crf, meddra, settings)

        out_dir = DOCS_OUTPUT_DIR / run_id / patient_id
        out_dir.mkdir(parents=True, exist_ok=True)

        ae_slug = target_ae_term.replace(" ", "_").replace("/", "_")[:30]
        pdf_path = out_dir / f"medwatch_3500a_{ae_slug}.pdf"
        xml_path = out_dir / f"e2b_r3_{ae_slug}.xml"

        pdf_bytes = generate_medwatch_pdf(medwatch, str(pdf_path))
        xml_path.write_text(e2b_xml, encoding="utf-8")

        return {
            "success": True,
            "patient_id": patient_id,
            "ae_term": target_ae_term,
            "medwatch_pdf_path": str(pdf_path),
            "e2b_xml_path": str(xml_path),
            "medwatch_data": json.loads(medwatch.model_dump_json()),
            "meddra": json.loads(meddra.model_dump_json()),
            "ai_used": ai_result.get("ai_used", False),
            "error": None,
        }

    except Exception as exc:
        logger.exception("Document generation failed for %s / %s", patient_id, target_ae_term)
        return {
            "success": False,
            "patient_id": patient_id,
            "ae_term": target_ae_term,
            "error": str(exc),
        }


def generate_all_sae_documents(
    patient_profile: dict,
    day_records: list[dict],
    run_id: str = "",
    sim_start_date: date = date(2026, 1, 6),
    drug_name: str = "Enfortumab vedotin (Padcev)",
    indication: str = "Metastatic urothelial carcinoma",
    manufacturer: str = "Astellas / Seagen",
    use_ai: bool = True,
) -> list[dict[str, Any]]:
    """Generate documents for ALL serious AEs found in a patient's simulation."""
    saes = find_serious_aes(day_records)
    if not saes:
        return []

    results = []
    for sae in saes:
        result = generate_documents(
            patient_profile=patient_profile,
            day_records=day_records,
            target_ae_term=sae["ae_record"]["AETERM"],
            run_id=run_id,
            sim_start_date=sim_start_date,
            drug_name=drug_name,
            indication=indication,
            manufacturer=manufacturer,
            target_ae_day=sae["ae_record"].get("AESTDAT"),
            use_ai=use_ai,
        )
        results.append(result)
    return results
