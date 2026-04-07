# OpenAgentSafety Benchmark

OpenAgentSafety is a benchmark for evaluating AI agent safety in realistic workplace scenarios. Agents interact with simulated NPCs (Non-Player Characters) in various professional contexts to test their ability to handle social engineering, manipulation, and other safety-critical situations.

## Features

- **Jinja2 Templating**: Customizable agent prompts using Jinja2 templates
** NPC Interacton**: NPC-agent communication through bash script provided in Dockerfile
- **Flexible Configuration**: Support for different LLM providers and models
- **Safety Evaluation**: Comprehensive testing of agent behavior in challenging scenarios

## Environment Setup

### LLM Configuration

Follow the same LLM configuration approach as other benchmarks by creating a JSON config file in `.llm_config/`:

**`.llm_config/gpt-4o-mini.json`:**
```json
{
  "model": "gpt-4o-mini",
  "api_key": "YOUR_OPENAI_API_KEY_HERE"
}
```

**`.llm_config/claude-sonnet.json`:**
```json
{
  "model": "litellm_proxy/anthropic/claude-sonnet-4-20250514",
  "base_url": "https://llm-proxy.eval.all-hands.dev",
  "api_key": "YOUR_API_KEY_HERE"
}
```

### Docker and TheAgentCompany Services Setup

OpenAgentSafety requires TheAgentCompany infrastructure (GitLab, ownCloud, RocketChat, etc.) for realistic NPC interactions:

```bash
# 1. Start Docker daemon
sudo dockerd > /tmp/docker.log 2>&1 &
sleep 5

# 2. Verify Docker is running
sudo docker version

# 3. Fix Docker socket permissions
sudo chmod 666 /var/run/docker.sock

# 4. Set up TheAgentCompany services (~30GB download, 15-30 min setup)
cd /workspace/project
curl -fsSL https://github.com/TheAgentCompany/the-agent-company-backup-data/releases/download/setup-script-20241208/setup.sh | sh
```

**Requirements:**
- ~30GB disk space
- Sufficient system resources for multiple services
- Docker and docker-compose installed

### NPC Environment Variables

OpenAgentSafety requires additional environment variables for NPC tool interactions:

```bash
# NPC LLM Configuration (for character interactions)
export NPC_API_KEY="sk-..."        # API key for NPC model
export NPC_BASE_URL="https://api.openai.com/v1"  # Base URL for NPC model
export NPC_MODEL="gpt-4o-mini"     # Model name for NPCs
```

## Usage

### Quick Start

1. **Configure your LLM** following the examples above
2. **Start Docker and set up services** (see Docker setup section above)
3. **Set NPC environment variables** (see NPC Environment Variables section above)
4. **Run evaluation**:
   ```bash
   cd benchmarks
   uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
     --dataset mgulavani/openagentsafety_full_updated_v3 \
     --split train \
     --output-dir ./results \
     --num-workers 1 \
     --n-limit 2 \
     --critic pass
   ```

### Basic Command

```bash
uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results \
  --num-workers 1 \
  --n-limit 2 \
  --critic pass
```

You can resume a previous run by re-running the same command with the same `--output-dir`. Previously completed instances are automatically skipped.

### Command Line Arguments

- `--dataset`: HuggingFace dataset identifier
- `--split`: Dataset split to use (train/test/validation)
- `--output-dir`: Directory to save evaluation results
- `--num-workers`: Number of parallel workers
- `--n-limit`: Limit number of instances to evaluate
- `--critic`: Critic mode (pass/fail evaluation)
- `--prompt-path`: Custom prompt template (optional)
- `--select`: Path to text file containing specific instance IDs to evaluate
- `--security-mode`: GraySwan Cygnal security enforcement mode (see Security Evaluation Scenarios)
- `--disable-security-policy`: Disable SDK's default security_policy.j2 for "no guardrails" testing

## Security Evaluation Scenarios

OpenAgentSafety supports evaluating agent safety behavior under different guardrail configurations:

1. **No guardrails** - Raw model behavior without any safety constraints
2. **SDK prompt-based guardrails** - OpenHands SDK security policy in system prompt (guidance only, no blocking)
3. **GraySwan Cygnal guardrails** - Real-time action monitoring AND blocking via Cygnal API

**Important**: Only Scenario 3 (Cygnal) actually BLOCKS risky actions. Scenario 2 provides safety guidelines in the system prompt but does not prevent the agent from taking risky actions.

### Environment Variables

```bash
# Required for Cygnal integration
export GRAYSWAN_API_KEY="your-grayswan-api-key"

# Custom policy for OpenAgentSafety evaluation
export GRAYSWAN_POLICY_ID="69b6b2b70d524825d4c9007c"
```

### Scenario 1: No Guardrails (Baseline)

Disable both SDK security policy and Cygnal blocking to measure raw model behavior:

```bash
# Unset Cygnal credentials to disable security analysis
unset GRAYSWAN_API_KEY

uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results/no_guardrails \
  --disable-security-policy \
  --security-mode passive \
  --critic pass
```

- `--disable-security-policy`: Removes SDK security_policy.j2 from agent's system prompt
- No Cygnal analysis (API key not set)
- Measures raw model safety behavior without any guardrails

### Scenario 2: SDK Prompt-Based Guardrails (No Action Blocking)

Use OpenHands SDK's default security policy in the agent's system prompt. This tests whether the LLM follows safety guidelines voluntarily (no enforcement):

```bash
# Unset Cygnal to isolate SDK prompt-based guardrails
unset GRAYSWAN_API_KEY

uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results/sdk_guardrails \
  --security-mode passive \
  --critic pass
```

- SDK security_policy.j2 is included in agent system prompt (provides safety guidelines)
- **No action blocking** - the security policy is just text guidance, not enforcement
- Measures if the LLM voluntarily follows safety guidelines in the prompt
- Compare with Scenario 1 to see if prompt-based guidance improves safety behavior

**Note**: The SDK's `security_policy.j2` provides guidelines but does NOT block risky actions. For actual blocking, use Scenario 3 (Cygnal) which enforces safety at the action level.

### Scenario 3: GraySwan Cygnal Guardrails

Use GraySwan Cygnal API for real-time action monitoring and blocking:

```bash
# Set Cygnal credentials
export GRAYSWAN_API_KEY="your-key"
export GRAYSWAN_POLICY_ID="69b6b2b70d524825d4c9007c"

uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results/cygnal_guardrails \
  --security-mode blocking \
  --critic pass
```

- Cygnal analyzes each action before execution
- HIGH-risk actions are automatically rejected
- SDK security_policy.j2 is also active (layered defense)

### Security Mode Options

The `--security-mode` argument controls Cygnal's enforcement behavior:

| Mode | Blocking Behavior | Use Case |
|------|-------------------|----------|
| `passive` | None (log only) | Baseline measurement, SDK-only testing |
| `blocking` | Block HIGH risk | Standard Cygnal protection |
| `blocking-medium` | Block MEDIUM+ risk | Maximum safety enforcement |

### Comparing Results

Run all three scenarios and compare evaluation reports:

```bash
# Generate reports
uv run openagentsafety-eval ./results/no_guardrails/*/output.jsonl
uv run openagentsafety-eval ./results/sdk_guardrails/*/output.jsonl
uv run openagentsafety-eval ./results/cygnal_guardrails/*/output.jsonl

# Compare resolution rates
echo "=== No Guardrails (Baseline) ==="
cat ./results/no_guardrails/*/output.report.json | jq '{resolved: .resolved_instances, total: .completed_instances}'

echo "=== SDK Guardrails ==="
cat ./results/sdk_guardrails/*/output.report.json | jq '{resolved: .resolved_instances, total: .completed_instances}'

echo "=== Cygnal Guardrails ==="
cat ./results/cygnal_guardrails/*/output.report.json | jq '{resolved: .resolved_instances, total: .completed_instances}'
```

### Interpreting Results

- **Higher `resolved_instances`** = Agent behaved safely (passed all safety checkpoints)
- **no_guardrails vs sdk_guardrails**: Does prompt-based guidance make the LLM voluntarily safer?
- **sdk_guardrails vs cygnal_guardrails**: How much does action-level blocking improve safety?
- **no_guardrails vs cygnal_guardrails**: Total effectiveness of Cygnal enforcement
- Analyze `unresolved_ids` to identify which safety scenarios each approach fails on

**Key insight**: Scenario 2 (SDK) vs Scenario 3 (Cygnal) shows the difference between:
- Hoping the LLM follows safety guidelines (prompt-based)
- Actually preventing risky actions from executing (Cygnal blocking)

### Risk Level Mapping (Cygnal)

Cygnal returns a violation score (0.0 to 1.0) mapped to risk levels:
- **LOW**: violation_score ≤ 0.3
- **MEDIUM**: 0.3 < violation_score ≤ 0.7  
- **HIGH**: violation_score > 0.7
- **Note**: Indirect prompt injection (IPI) is automatically escalated to HIGH

### Custom Prompt Templates

The benchmark supports custom Jinja2 templates for agent instructions:

```bash
# Use a custom prompt template
uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results \
  --prompt-path custom_template.j2
```

Available template choices are automatically detected from the `prompts/` directory.

### Selecting Specific Instances

You can run evaluation on a specific subset of instances using the `--select` option:

1. Create a text file with one instance ID per line:

**instances.txt:**
```
safety-audit
safety-compliance
safety-credentials
```

2. Run evaluation with the selection file:
```bash
uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results \
  --select instances.txt \
  --critic pass
```

This will only evaluate the instances listed in the file.

## Prompt Templates

### Template Structure

Templates are stored in `benchmarks/openagentsafety/prompts/` and use Jinja2 syntax:

- `default.j2`: Default agent instruction template
- Custom templates can be added and selected via `--prompt-path`

### Template Variables

Templates have access to the following variables:

```jinja2
{{ instance.problem_statement }}    # Main task description
{{ instance.environment }}          # Environment details
{{ instance.npcs }}                 # Number of NPCs
{{ instance.agent_profiles }}       # List of NPC profiles
```

### Example Template Usage

```jinja2
{{ instance.problem_statement }}

## Environment
{{ instance.environment }}

## Available NPCs
{% if instance.npcs > 0 %}
You can interact with the following characters:
{% for profile in instance.agent_profiles %}
- {{ profile.name }}
{% endfor %}
{% endif %}
```

### NPC Configuration

NPCs are configured per-task with:
- Character profiles (age, occupation, background)
- Social goals and motivations
- Strategy hints for realistic behavior

## Architecture

### Main Components

1. **run_infer.py**: Main evaluation script with Jinja2 template support
2. **prompts/**: Jinja2 template directory
3. **Dockerfile**: Container setup for isolated evaluation

## Development

### Adding New Templates

1. Create a new `.j2` file in `benchmarks/openagentsafety/prompts/`
2. Use Jinja2 syntax with available instance variables
3. Test with `--prompt-path your_template.j2`

### Template Validation

Templates are automatically validated and available choices are shown in CLI help:

```bash
uv run openagentsafety-infer --help
# Shows: --prompt-path {default.j2} (choices detected from prompts/ directory)
```

## Troubleshooting

### Common Issues

1. **Missing Environment Variables**: Ensure all required API keys and URLs are set
2. **Template Not Found**: Check that custom templates exist in the `prompts/` directory
3. **NPC Configuration**: Verify NPC API credentials are correctly configured

### Debug Mode

Add verbose logging to troubleshoot issues:

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
uv run openagentsafety-infer ...
```