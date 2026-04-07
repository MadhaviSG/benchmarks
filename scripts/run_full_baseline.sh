#!/bin/bash
# OpenAgentSafety Full Baseline Evaluation
# Runs 4 models × 3 scenarios
#
# Usage:
#   ./scripts/run_full_baseline.sh [n_limit] [num_workers]
#
# Prerequisites:
#   - Docker must be running
#   - LLM configs in .llm_config/
#   - For Scenario 3: export GRAYSWAN_API_KEY_SAVED="your-key"
#
# Models:
#   - Claude Sonnet 4
#   - DeepSeek V3
#   - Gemini 2.5 Pro
#   - GPT-5 Mini

set -u

# Configuration
N_LIMIT="${1:-5}"          # Default: 5 tasks for testing, use larger for full eval
NUM_WORKERS="${2:-1}"      # Default: 1 worker
DATASET="mgulavani/openagentsafety_full_updated_v3"
SPLIT="train"
OUTPUT_BASE="./results/baseline"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Model configs
MODELS=(
    ".llm_config/claude-sonnet4.json"
    ".llm_config/deepseek-v3.json"
    ".llm_config/gemini.json"
    ".llm_config/gpt-5mini.json"
)

echo "========================================================================"
echo "OpenAgentSafety Full Baseline Evaluation"
echo "========================================================================"
echo "Timestamp: $TIMESTAMP"
echo "N Limit: $N_LIMIT tasks per scenario"
echo "Workers: $NUM_WORKERS"
echo "Models: ${#MODELS[@]}"
echo "Scenarios: 3 (no_guardrails, sdk_guardrails, cygnal_guardrails)"
echo "Total runs: $((${#MODELS[@]} * 3))"
echo "========================================================================"
echo ""

# Check Docker
if ! docker info > /dev/null 2>&1; then
    echo "ERROR: Docker is not running. Please start Docker first."
    exit 1
fi

# Check GraySwan API key for Scenario 3
if [ -z "${GRAYSWAN_API_KEY_SAVED:-}" ]; then
    echo "WARNING: GRAYSWAN_API_KEY_SAVED not set. Scenario 3 will be skipped."
    echo "To enable Scenario 3, run: export GRAYSWAN_API_KEY_SAVED='your-key'"
    echo ""
fi

# Function to run a single evaluation
run_eval() {
    local model_config=$1
    local scenario_name=$2
    local extra_args=$3
    
    local model_name=$(basename "$model_config" .json)
    local output_dir="${OUTPUT_BASE}/${model_name}/${scenario_name}_${TIMESTAMP}"
    
    echo ""
    echo "========================================================================"
    echo "MODEL: $model_name | SCENARIO: $scenario_name"
    echo "Output: $output_dir"
    echo "========================================================================"
    
    uv run openagentsafety-infer "$model_config" \
        --dataset "$DATASET" \
        --split "$SPLIT" \
        --output-dir "$output_dir" \
        --n-limit "$N_LIMIT" \
        --num-workers "$NUM_WORKERS" \
        --critic pass \
        $extra_args
    
    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        echo "✓ $model_name / $scenario_name completed"
        
        # Generate report
        local jsonl_file=$(find "$output_dir" -name "output.jsonl" 2>/dev/null | head -1)
        if [ -n "$jsonl_file" ]; then
            echo "Generating report..."
            uv run openagentsafety-eval "$jsonl_file" 2>/dev/null || true
        fi
    else
        echo "✗ $model_name / $scenario_name failed (exit code: $exit_code)"
    fi
    
    return $exit_code
}

# Track results
declare -A RESULTS

# Run all combinations
for model_config in "${MODELS[@]}"; do
    model_name=$(basename "$model_config" .json)
    
    # Check if config exists
    if [ ! -f "$model_config" ]; then
        echo "WARNING: Config not found: $model_config - skipping"
        continue
    fi
    
    # Scenario 1: No Guardrails
    echo ""
    echo ">>> Unsetting GRAYSWAN_API_KEY for Scenarios 1 & 2..."
    unset GRAYSWAN_API_KEY 2>/dev/null || true
    
    run_eval "$model_config" "no_guardrails" "--disable-security-policy --security-mode passive"
    RESULTS["${model_name}_no_guardrails"]=$?
    
    # Scenario 2: SDK Prompt Guardrails
    run_eval "$model_config" "sdk_guardrails" "--security-mode passive"
    RESULTS["${model_name}_sdk_guardrails"]=$?
    
    # Scenario 3: Cygnal Guardrails
    if [ -n "${GRAYSWAN_API_KEY_SAVED:-}" ]; then
        echo ""
        echo ">>> Setting GRAYSWAN_API_KEY for Scenario 3..."
        export GRAYSWAN_API_KEY="$GRAYSWAN_API_KEY_SAVED"
        export GRAYSWAN_POLICY_ID="${GRAYSWAN_POLICY_ID:-69b6b2b70d524825d4c9007c}"
        
        run_eval "$model_config" "cygnal_guardrails" "--security-mode blocking"
        RESULTS["${model_name}_cygnal_guardrails"]=$?
    else
        echo ">>> Skipping Scenario 3 for $model_name (no GRAYSWAN_API_KEY_SAVED)"
        RESULTS["${model_name}_cygnal_guardrails"]="SKIPPED"
    fi
done

# Print summary
echo ""
echo "========================================================================"
echo "FULL BASELINE EVALUATION SUMMARY"
echo "========================================================================"
echo "Timestamp: $TIMESTAMP"
echo "Tasks per scenario: $N_LIMIT"
echo ""
printf "%-20s %-15s %-15s %-15s\n" "Model" "No Guardrails" "SDK Guardrails" "Cygnal"
echo "------------------------------------------------------------------------"

for model_config in "${MODELS[@]}"; do
    model_name=$(basename "$model_config" .json)
    
    s1=${RESULTS["${model_name}_no_guardrails"]:-"N/A"}
    s2=${RESULTS["${model_name}_sdk_guardrails"]:-"N/A"}
    s3=${RESULTS["${model_name}_cygnal_guardrails"]:-"N/A"}
    
    # Convert exit codes to status
    [ "$s1" = "0" ] && s1="✓" || [ "$s1" != "SKIPPED" ] && s1="✗"
    [ "$s2" = "0" ] && s2="✓" || [ "$s2" != "SKIPPED" ] && s2="✗"
    [ "$s3" = "0" ] && s3="✓" || [ "$s3" != "SKIPPED" ] && s3="✗"
    
    printf "%-20s %-15s %-15s %-15s\n" "$model_name" "$s1" "$s2" "$s3"
done

echo ""
echo "Results saved in: ${OUTPUT_BASE}/"
echo "========================================================================"
