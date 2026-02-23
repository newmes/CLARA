#!/bin/bash
# Batch simulation: 7 drugs × 100 patients × 126 days × seed 42
# Using pre-built rule sets, 10 workers

set -e
cd /data2/workspace/ClinicalTrialEngine
source .venv/bin/activate

PATIENTS=100
DAYS=126
WORKERS=10
SEED=42

declare -A DRUGS
declare -A INDICATIONS
declare -A RULESETS

RULESETS[1]="data/rule_sets/rule_set_1_Darbepoetin_alfa.json"
DRUGS[1]="Darbepoetin alfa"
INDICATIONS[1]="Small Cell Lung Cancer (SCLC), Extensive Stage"

RULESETS[2]="data/rule_sets/rule_set_2_Etoposide_Cisplatin.json"
DRUGS[2]="Etoposide + Cisplatin"
INDICATIONS[2]="extensive-stage small cell lung cancer (ES-SCLC)"

RULESETS[3]="data/rule_sets/rule_set_3_CALGB9732_Paclitaxel_Cisplatin_Etoposide.json"
DRUGS[3]="Paclitaxel + Cisplatin + Etoposide"
INDICATIONS[3]="extensive-stage small cell lung cancer (ES-SCLC)"

RULESETS[4]="data/rule_sets/rule_set_4_Carboplatin_Etoposide.json"
DRUGS[4]="Carboplatin + Etoposide"
INDICATIONS[4]="extensive-stage small cell lung cancer (ES-SCLC)"

RULESETS[6]="data/rule_sets/rule_set_6_Paclitaxel_Carboplatin_Bevacizumab.json"
DRUGS[6]="Paclitaxel + Carboplatin + Bevacizumab"
INDICATIONS[6]="non-small cell lung cancer (NSCLC)"

RULESETS[7]="data/rule_sets/rule_set_7_Paclitaxel_Carboplatin.json"
DRUGS[7]="Paclitaxel + Carboplatin"
INDICATIONS[7]="non-small cell lung cancer (NSCLC)"

RULESETS[8]="data/rule_sets/rule_set_8_Gemcitabine_Cisplatin.json"
DRUGS[8]="Gemcitabine + Cisplatin"
INDICATIONS[8]="non-small cell lung cancer (NSCLC)"

echo "=============================================="
echo "BATCH SIMULATION — 7 Drugs"
echo "Patients: $PATIENTS | Days: $DAYS | Workers: $WORKERS | Seed: $SEED"
echo "Start: $(date)"
echo "=============================================="

for idx in 1 2 3 4 6 7 8; do
  DRUG="${DRUGS[$idx]}"
  IND="${INDICATIONS[$idx]}"
  RS="${RULESETS[$idx]}"

  echo ""
  echo "======================================================================"
  echo "[$idx/7] $DRUG"
  echo "  Indication: $IND"
  echo "  Rule set: $RS"
  echo "  Start: $(date)"
  echo "======================================================================"

  cp "$RS" data/rule_set.json

  python src/run_simulation_v2.py \
    --drug "$DRUG" \
    --indication "$IND" \
    --patients $PATIENTS \
    --days $DAYS \
    --workers $WORKERS \
    --seed $SEED \
    --skip-rules

  echo "  Finished: $(date)"
  echo ""
done

echo "=============================================="
echo "ALL 7 SIMULATIONS COMPLETE"
echo "End: $(date)"
echo "=============================================="
