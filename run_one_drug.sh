#!/bin/bash
# Run simulation for one drug
# Usage: run_one_drug.sh <idx> <drug_name> <indication> <rule_set_path>
set -e
cd /data2/workspace/ClinicalTrialEngine
source .venv/bin/activate

IDX=$1
DRUG="$2"
IND="$3"
RS="$4"
PATIENTS=100
DAYS=126
WORKERS=10
SEED=42

echo "======================================================================"
echo "[$IDX] $DRUG"
echo "  Indication: $IND"
echo "  Rule set: $RS"
echo "  Start: $(date)"
echo "======================================================================"

cp "$RS" "data/rule_set_run_${IDX}.json"

python src/run_simulation_v2.py \
  --drug "$DRUG" \
  --indication "$IND" \
  --patients $PATIENTS \
  --days $DAYS \
  --workers $WORKERS \
  --seed $SEED \
  --skip-rules \
  --data-dir data

echo "======================================================================"
echo "[$IDX] $DRUG — FINISHED: $(date)"
echo "======================================================================"
