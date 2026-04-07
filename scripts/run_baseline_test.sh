#!/bin/bash
# OpenAgentSafety Baseline Test Script
# Runs 1 model × 3 scenarios × N tasks for validation
#
# Usage:
#   ./scripts/run_baseline_test.sh [model_config] [n_limit]
#
# Examples:
#   ./scripts/run_baseline_test.sh .llm_config/claude-sonnet4.json 5
#   ./scripts/run_baseline_test.sh .llm_config/gpt-5mini.json 10
#
# Prerequisites:
#   - Docker must be running
#   - For Scenario 3, set GRAYSWAN_API_KEY_SAVED before running

set -u

# Configuration
MODEL_CONFIG="${1:-.llm_config/claude-sonnet4.json}"
N_LIMIT="${2:-5}"
MODEL_NAME=$(basename "$MODEL_CONFIG" .json)
DATASET="mgulavani/openagentsafety_full_updated_v3"
SPLIT="train"
OUTPUT_BASE="./results/baseline_test"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
NUM_WORKERS=1

echo "========================================================================"
echo "OpenAgentSafety Baseline Test"
echo "========================================================================"
echo "Model Config: $MODEL_CONFIG"
echo "Model Name: $MODEL_NAME"
echo "N Limit: $N_LIMIT tasks"
echo "Timestamp: $TIMESTAMP"
echo "========================================================================"

# Check if config file exists
if [ ! -f "$MODEL_CONFIG" ]; then
    echo "ERROR: Config file not found: $MODEL_CONFIG"
    exit 1
fi

# Function to run a scenario
run_scenario() {
    local scenario_name=$1
    local scenario_num=$2
    local extra_args=$3
    
    local output_dir="${OUTPUT_BASE}/${MODEL_NAME}/${scenario_name}_${TIMESTAMP}"
    
    echo ""
    echo "========================================================================"
    echo "SCENARIO $scenario_num: $scenario_name"
    echo "Output: $output_dir"
    echo "========================================================================"
    
    # Build command
    local cmd="uv run openagentsafety-infer $MODEL_CONFIG \
        --dataset $DATASET \
        --split $SPLIT \
        --output-dir $output_dir \
        --n-limit $N_LIMIT \
        --num-workers $NUM_WORKERS \
        --critic pass \
        $extra_args"
    
    echo "Command: $cmd"
    echo "------------------------------------------------------------------------"
    
    # Run evaluation
    eval $cmd
    
    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        echo "✓ Scenario $scenario_num completed successfully"
    else
        echo "✗ Scenario $scenario_num failed with exit code $exit_code"
    fi
    
    return $exit_code
}

# Scenario 1: No Guardrails (Baseline)
echo ""
echo "Unsetting GRAYSWAN_API_KEY for Scenarios 1 & 2..."
unset GRAYSWAN_API_KEY 2>/dev/null || true

run_scenario "no_guardrails" 1 "--disable-security-policy --security-mode passive"
SCENARIO1_EXIT=$?

# Scenario 2: SDK Prompt-Based Guardrails
run_scenario "sdk_guardrails" 2 "--security-mode passive"
SCENARIO2_EXIT=$?

# Scenario 3: Cygnal Guardrails (requires GRAYSWAN_API_KEY)
echo ""
echo "Setting GRAYSWAN_API_KEY for Scenario 3..."
if [ -z "${GRAYSWAN_API_KEY_SAVED:-}" ]; then
    echo "WARNING: GRAYSWAN_API_KEY not available. Skipping Scenario 3."
    echo "To run Scenario 3, set GRAYSWAN_API_KEY before running this script."
    SCENARIO3_EXIT=1
else
    export GRAYSWAN_API_KEY="$GRAYSWAN_API_KEY_SAVED"
    export GRAYSWAN_POLICY_ID="${GRAYSWAN_POLICY_ID:-69b6b2b70d524825d4c9007c}"
    run_scenario "cygnal_guardrails" 3 "--security-mode blocking"
    SCENARIO3_EXIT=$?
fi

# Summary
echo ""
echo "========================================================================"
echo "BASELINE TEST SUMMARY"
echo "========================================================================"
echo "Model: $MODEL_NAME"
echo "Tasks per scenario: $N_LIMIT"
echo ""
echo "Scenario 1 (No Guardrails):     $([ $SCENARIO1_EXIT -eq 0 ] && echo '✓ PASSED' || echo '✗ FAILED')"
echo "Scenario 2 (SDK Guardrails):    $([ $SCENARIO2_EXIT -eq 0 ] && echo '✓ PASSED' || echo '✗ FAILED')"
echo "Scenario 3 (Cygnal Guardrails): $([ $SCENARIO3_EXIT -eq 0 ] && echo '✓ PASSED' || echo '✗ SKIPPED/FAILED')"
echo ""
echo "Results saved in: ${OUTPUT_BASE}/${MODEL_NAME}/"
echo "========================================================================"

# Generate reports if evaluations completed
echo ""
echo "Generating evaluation reports..."
for scenario in no_guardrails sdk_guardrails cygnal_guardrails; do
    output_dir="${OUTPUT_BASE}/${MODEL_NAME}/${scenario}_${TIMESTAMP}"
    if [ -d "$output_dir" ]; then
        jsonl_file=$(find "$output_dir" -name "output.jsonl" 2>/dev/null | head -1)
        if [ -n "$jsonl_file" ]; then
            echo "Generating report for $scenario..."
            uv run openagentsafety-eval "$jsonl_file" 2>/dev/null || true
        fi
    fi
done

echo ""
echo "Done! Check the results in ${OUTPUT_BASE}/${MODEL_NAME}/"
