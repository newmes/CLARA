"""Test just the merge step using saved individual rule sets."""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rule_engine.agent import AgentLog
from rule_engine.agent_multidrug import merge_rule_sets
from rule_engine.config import RuleEngineConfig
from rule_engine.evidence.ddi import collect_ddi_evidence
from rule_engine.schema import RuleSet
from rule_engine.validator import validate_rule_set

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

RULE_SET_DIR = Path("rule_sets_multi")

# The 6 saved individual rule sets
INDIVIDUAL_FILES = [
    "darbepoetin_alfa_small_cell_lung_cancer_rules.json",
    "cisplatin+etoposide+paclitaxel_small_cell_lung_cancer_rules.json",
    "etoposide+carboplatin_small_cell_lung_cancer_rules.json",
    "paclitaxel+carboplatin+bevacizumab_non-small_cell_lung_cancer_rules.json",
    "paclitaxel+carboplatin_non-small_cell_lung_cancer_rules.json",
    "gemcitabine+cisplatin_squamous_non-small_cell_lung_cancer_rules.json",
]


async def main():
    # Load individual rule sets
    individual_results = []
    for fname in INDIVIDUAL_FILES:
        path = RULE_SET_DIR / fname
        if not path.exists():
            log.error("Missing: %s", path)
            return
        rs = RuleSet.model_validate_json(path.read_text())
        individual_results.append((rs, None, []))
        log.info("Loaded: %s (%d AEs, drugs=%s)", fname, len(rs.adverse_events), rs.drugs)

    log.info("Loaded %d individual rule sets", len(individual_results))

    # Collect DDI evidence
    config = RuleEngineConfig()
    regimens = [(rs.drugs, rs.indication) for rs, _, _ in individual_results]
    ddi_evidence = await collect_ddi_evidence(regimens, config)
    log.info("DDI evidence: %d pairs", len(ddi_evidence.pairs))

    # Run merge
    log.info("Starting merge...")
    merged_rs, agent_log = await merge_rule_sets(individual_results, ddi_evidence, config)

    # Summary
    log.info("=== MERGE RESULT ===")
    log.info("Drugs: %s", merged_rs.drugs)
    log.info("Indication: %s", merged_rs.indication)
    log.info("Indications: %s", merged_rs.indications)
    log.info("AEs: %d", len(merged_rs.adverse_events))
    log.info("Drug interactions: %d", len(merged_rs.drug_interactions))
    log.info("Per-indication efficacy: %d", len(merged_rs.per_indication_efficacy))
    log.info("Overlapping AE notes: %d", len(merged_rs.overlapping_ae_notes))
    log.info("is_multi_indication: %s", merged_rs.is_multi_indication)

    # Validate
    warnings = validate_rule_set(merged_rs)
    log.info("Validation warnings: %d", len(warnings))
    for w in warnings[:10]:
        log.info("  %s", w)

    # Save
    out_path = RULE_SET_DIR / "multi_lung_cancer_merged_rules.json"
    out_path.write_text(merged_rs.model_dump_json(indent=2))
    log.info("Saved merged rule set: %s", out_path)

    # Save agent log
    from dataclasses import asdict
    log_path = RULE_SET_DIR / "multi_lung_cancer_merged_agent_log.json"
    log_path.write_text(json.dumps(asdict(agent_log), indent=2, default=str))
    log.info("Saved agent log: %s", log_path)

    print(f"\nSUCCESS: Merged {len(individual_results)} rule sets -> {len(merged_rs.adverse_events)} AEs, {len(merged_rs.drug_interactions)} DDIs")


if __name__ == "__main__":
    asyncio.run(main())
