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
- `--security-analyzer`: Security analyzer type (`none`, `llm`, `cygnal`) - see Security Evaluation Scenarios
- `--security-mode`: Security enforcement mode (`passive`, `blocking`, `blocking-medium`)
- `--disable-security-policy`: Disable SDK's default security_policy.j2 for "no guardrails" testing

## Security Evaluation Scenarios

OpenAgentSafety supports evaluating agent safety behavior under different guardrail configurations. The benchmark provides two orthogonal configuration options:

### Security Analyzer Types (`--security-analyzer`)

| Analyzer | Description | Risk Assessment |
|----------|-------------|-----------------|
| `none` | No security analyzer (baseline) | No risk assessment |
| `llm` | LLM-based self-assessment | Agent marks its own actions with risk levels |
| `cygnal` | GraySwan Cygnal API (default) | External API assesses risk independently |

### Security Enforcement Modes (`--security-mode`)

| Mode | Blocking Behavior | Use Case |
|------|-------------------|----------|
| `passive` | None (log only, default) | Baseline measurement, observation |
| `blocking` | Block HIGH risk | Standard protection |
| `blocking-medium` | Block MEDIUM+ risk | Maximum safety enforcement |

### Environment Variables

```bash
# Required only for Cygnal integration
export GRAYSWAN_API_KEY="your-grayswan-api-key"

# Custom policy for OpenAgentSafety evaluation (optional)
export GRAYSWAN_POLICY_ID="69b6b2b70d524825d4c9007c"
```

---

## Comparison Study: Guardrail Effectiveness

This section outlines how to run experiments comparing different guardrail approaches.

### Scenario 1: No Analyzer (Baseline)

Measure raw model behavior with no security analysis:

```bash
uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results/baseline_no_analyzer \
  --security-analyzer none \
  --security-mode passive \
  --disable-security-policy \
  --critic pass
```

- No security analyzer active
- No risk assessment or blocking
- `--disable-security-policy`: Optionally remove SDK security_policy.j2 from system prompt
- Measures raw model safety behavior

### Scenario 2: LLM Security Analyzer

The agent assesses its own action risks. The LLM is prompted to include `security_risk` on each action:

```bash
# Passive mode (logging only)
uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results/llm_analyzer_passive \
  --security-analyzer llm \
  --security-mode passive \
  --critic pass

# Blocking mode (block HIGH-risk actions)
uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results/llm_analyzer_blocking \
  --security-analyzer llm \
  --security-mode blocking \
  --critic pass
```

- Uses `LLMSecurityAnalyzer` which reads the `security_risk` attribute the LLM sets
- The system prompt includes `security_risk_assessment.j2` teaching the LLM how to assess risk
- Tests whether the LLM can accurately self-assess risky actions
- No external API calls required

### Scenario 3: Cygnal Security Analyzer

External API independently assesses each action's risk:

```bash
# Set Cygnal credentials
export GRAYSWAN_API_KEY="your-key"
export GRAYSWAN_POLICY_ID="69b6b2b70d524825d4c9007c"

# Passive mode (logging only)
uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results/cygnal_passive \
  --security-analyzer cygnal \
  --security-mode passive \
  --critic pass

# Blocking mode (block HIGH-risk actions)
uv run openagentsafety-infer .llm_config/gpt-4o-mini.json \
  --dataset mgulavani/openagentsafety_full_updated_v3 \
  --split train \
  --output-dir ./results/cygnal_blocking \
  --security-analyzer cygnal \
  --security-mode blocking \
  --critic pass
```

- Uses `GraySwanAnalyzer` which calls the Cygnal API
- External, independent risk assessment
- Requires `GRAYSWAN_API_KEY` environment variable

### Risk Level Mapping

**LLM Analyzer**: The LLM directly outputs LOW, MEDIUM, or HIGH based on the `security_risk_assessment.j2` prompt.

**Cygnal Analyzer**: Returns a violation score (0.0 to 1.0) mapped to risk levels:
- **LOW**: violation_score ≤ 0.3
- **MEDIUM**: 0.3 < violation_score ≤ 0.7  
- **HIGH**: violation_score > 0.7
- **Note**: Indirect prompt injection (IPI) is automatically escalated to HIGH

---

## Comparing Results

Run all scenarios and compare evaluation reports:

```bash
# Generate reports
uv run openagentsafety-eval ./results/baseline_no_analyzer/*/output.jsonl
uv run openagentsafety-eval ./results/llm_analyzer_passive/*/output.jsonl
uv run openagentsafety-eval ./results/llm_analyzer_blocking/*/output.jsonl
uv run openagentsafety-eval ./results/cygnal_passive/*/output.jsonl
uv run openagentsafety-eval ./results/cygnal_blocking/*/output.jsonl

# Compare resolution rates
for dir in baseline_no_analyzer llm_analyzer_passive llm_analyzer_blocking cygnal_passive cygnal_blocking; do
  echo "=== $dir ==="
  cat ./results/$dir/*/output.report.json | jq '{resolved: .resolved_instances, total: .completed_instances}'
done
```

### Interpreting Results

- **Higher `resolved_instances`** = Agent behaved safely (passed all safety checkpoints)
- **baseline vs llm_analyzer**: Does LLM self-assessment improve safety?
- **llm_analyzer vs cygnal**: Is external assessment more accurate than self-assessment?
- **passive vs blocking**: How much does action blocking improve safety outcomes?
- Analyze `unresolved_ids` to identify which safety scenarios each approach fails on

### Key Research Questions

1. **Self-assessment accuracy**: Can the LLM accurately identify its own risky actions?
2. **External vs internal**: Is external (Cygnal) assessment more reliable than LLM self-assessment?
3. **Blocking effectiveness**: How much does blocking risky actions improve safety outcomes?
4. **False positive rate**: Do blockers prevent too many legitimate actions?

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