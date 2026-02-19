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
        )

        b5_prompt = format_b5_prompt(crf, settings)
        agent = Agent(name="b5_narrative", model=model, markdown=False)
        result = agent.run(b5_prompt)
        narrative = str(result.content).strip()

        c7_prompt = format_c7_prompt(crf)
        c7_agent = Agent(name="c7", model=model, markdown=False)
        c7_result = c7_agent.run(c7_prompt)
        dechallenge = str(c7_result.content).strip()

        c8_prompt = format_c8_prompt(crf)
        rechallenge = "Does not apply — single exposure period"
        if len(crf.ec) >= 2:
            c8_agent = Agent(name="c8", model=model, markdown=False)
            c8_result = c8_agent.run(c8_prompt)
            rechallenge = str(c8_result.content).strip()

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
        if "RECOVERED" in ae.AEOUT or "RESOLVING" in ae.AEOUT:
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

        meddra = code_meddra(crf.ae.AETERM, use_medgemma=use_ai)

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
