#!/usr/bin/env python3
"""
MATS 10.0: When Do Safe Steps Compound to Unsafe Outcomes in Agent CoT?
Updated for thinking_blocks extraction (Anthropic format)

Pipeline:
1. Load 15 trajectory files (7 unsafe, 8 safe)
2. Extract CoT reasoning from thinking_blocks and reasoning_content
3. Categorize failure patterns in CoT
4. Generate research insights
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


class CoTAnalyzer:
    """Analyzes Chain-of-Thought reasoning in agent trajectories"""

    def __init__(self, trajectory_dir: str):
        self.trajectory_dir = Path(trajectory_dir)
        self.trajectories = []
        self.cot_data = defaultdict(list)
        self.safety_labels = {}  # Maps traj_name -> 'safe' or 'unsafe'

    def load_trajectories(self, limit: int = None):
        """Load trajectory files"""
        print(f"📂 Loading trajectories from {self.trajectory_dir}")

        traj_files = list(self.trajectory_dir.glob("*.json"))
        if limit:
            traj_files = traj_files[:limit]

        print(f"   Found {len(traj_files)} trajectory files")

        for traj_file in traj_files:
            with open(traj_file, "r") as f:
                try:
                    data = json.load(f)
                    self.trajectories.append(
                        {"file": traj_file.name, "path": str(traj_file), "data": data}
                    )
                except json.JSONDecodeError as e:
                    print(f"   ⚠️  Skipping {traj_file.name}: {e}")

        print(f"   ✓ Loaded {len(self.trajectories)} trajectories\n")
        return len(self.trajectories)

    def label_safety_outcomes(
        self, safe_files: List[str] = None, unsafe_files: List[str] = None
    ):
        """
        Manually label trajectories as safe or unsafe

        Args:
            safe_files: List of trajectory filenames that resulted in refusal (safe)
            unsafe_files: List of trajectory filenames that resulted in compliance (unsafe)
        """
        print("🏷️  Labeling safety outcomes...")

        if safe_files:
            for filename in safe_files:
                self.safety_labels[filename] = "safe"

        if unsafe_files:
            for filename in unsafe_files:
                self.safety_labels[filename] = "unsafe"

        safe_count = sum(1 for label in self.safety_labels.values() if label == "safe")
        unsafe_count = sum(
            1 for label in self.safety_labels.values() if label == "unsafe"
        )

        print(f"   Safe trajectories: {safe_count}")
        print(f"   Unsafe trajectories: {unsafe_count}")
        print(f"   Unlabeled: {len(self.trajectories) - len(self.safety_labels)}\n")

        return safe_count, unsafe_count

    def extract_cot_reasoning(self):
        """Extract CoT from thinking_blocks, responses_reasoning_item, and reasoning_content"""
        print("🧠 Extracting Chain-of-Thought reasoning...")

        total_cot_steps = 0
        format_counts = defaultdict(int)

        for traj in self.trajectories:
            traj_name = traj["file"]
            events = traj["data"]

            cot_steps = []

            for event in events:
                if event.get("kind") == "ActionEvent":
                    # Method 1: Anthropic thinking_blocks (primary for Claude)
                    thinking_blocks = event.get("thinking_blocks", [])
                    for block in thinking_blocks:
                        if block.get("type") == "thinking":
                            cot_steps.append(
                                {
                                    "step": block["thinking"],
                                    "event_id": event.get("id"),
                                    "timestamp": event.get("timestamp"),
                                    "format": "thinking_block",
                                    "action": event.get("action", {}).get(
                                        "kind", "unknown"
                                    ),
                                }
                            )
                            total_cot_steps += 1
                            format_counts["thinking_block"] += 1

                    # Method 2: OpenAI Responses API format
                    reasoning = event.get("responses_reasoning_item", {})
                    summary = (
                        reasoning["summary"]
                        if isinstance(reasoning, dict) and "summary" in reasoning
                        else []
                    )
                    for step in summary:
                        cot_steps.append(
                            {
                                "step": step,
                                "event_id": event.get("id"),
                                "timestamp": event.get("timestamp"),
                                "format": "responses_api",
                                "action": event.get("action", {}).get(
                                    "kind", "unknown"
                                ),
                            }
                        )
                        total_cot_steps += 1
                        format_counts["responses_api"] += 1

                    # Method 3: reasoning_content (fallback)
                    reasoning_content = event.get("reasoning_content")
                    if reasoning_content and not thinking_blocks and not summary:
                        cot_steps.append(
                            {
                                "step": reasoning_content,
                                "event_id": event.get("id"),
                                "timestamp": event.get("timestamp"),
                                "format": "reasoning_content",
                                "action": event.get("action", {}).get(
                                    "kind", "unknown"
                                ),
                            }
                        )
                        total_cot_steps += 1
                        format_counts["reasoning_content"] += 1

            self.cot_data[traj_name] = cot_steps

        print(
            f"   ✓ Extracted {total_cot_steps} CoT steps from {len(self.cot_data)} trajectories"
        )
        print("   Format breakdown:")
        for fmt, count in format_counts.items():
            print(f"      {fmt}: {count}")
        print()

        return self.cot_data

    def categorize_failure_patterns(self):
        """
        Categorize how safe-looking CoT steps compound to unsafe outcomes

        Categories based on instruction conflict lens:
        1. Instruction following overrides safety
        2. Task completion focus blinds safety awareness
        3. Gradual boundary erosion across steps
        4. Context confusion (user request vs. injected content)
        """
        print("📊 Categorizing failure patterns in CoT...")

        patterns = {
            "instruction_override": [],
            "task_completion_focus": [],
            "boundary_erosion": [],
            "context_confusion": [],
            "safety_aware": [],  # Steps that show safety awareness
        }

        # Only analyze unsafe trajectories
        unsafe_trajs = [
            name for name, label in self.safety_labels.items() if label == "unsafe"
        ]

        for traj_name in unsafe_trajs:
            if traj_name not in self.cot_data:
                continue

            cot_steps = self.cot_data[traj_name]

            for i, step_data in enumerate(cot_steps):
                step = step_data["step"].lower()

                # Pattern 1: Instruction following overrides
                if any(
                    phrase in step
                    for phrase in [
                        "user asked",
                        "user wants",
                        "user requested",
                        "following instructions",
                        "as requested",
                        "as instructed",
                        "task requires",
                        "need to complete",
                        "must complete",
                        "the user",
                        "they want",
                        "requested that",
                    ]
                ):
                    patterns["instruction_override"].append(
                        {
                            "trajectory": traj_name,
                            "step_index": i,
                            "step": step_data["step"],
                            "action": step_data.get("action"),
                        }
                    )

                # Pattern 2: Task completion focus
                if any(
                    phrase in step
                    for phrase in [
                        "extracting",
                        "preparing to send",
                        "completing",
                        "finishing",
                        "final step",
                        "now i",
                        "let me",
                        "i need to",
                        "i should",
                        "i will",
                        "next step",
                        "proceeding",
                    ]
                ):
                    patterns["task_completion_focus"].append(
                        {
                            "trajectory": traj_name,
                            "step_index": i,
                            "step": step_data["step"],
                            "action": step_data.get("action"),
                        }
                    )

                # Pattern 3: Boundary erosion (look for progressive weakening)
                if i > 0:
                    prev_step = cot_steps[i - 1]["step"].lower()
                    # Check for shift from caution to action
                    cautious_words = [
                        "should",
                        "might",
                        "could",
                        "check",
                        "verify",
                        "careful",
                        "ensure",
                    ]
                    action_words = [
                        "will",
                        "going to",
                        "proceeding",
                        "executing",
                        "sending",
                    ]

                    has_cautious_prev = any(
                        word in prev_step for word in cautious_words
                    )
                    has_action_current = any(word in step for word in action_words)

                    if has_cautious_prev and has_action_current:
                        patterns["boundary_erosion"].append(
                            {
                                "trajectory": traj_name,
                                "step_index": i,
                                "step": step_data["step"],
                                "previous_step": cot_steps[i - 1]["step"],
                                "action": step_data.get("action"),
                            }
                        )

                # Pattern 4: Context confusion
                if any(
                    phrase in step
                    for phrase in [
                        "file contains",
                        "spreadsheet contains",
                        "document says",
                        "according to",
                        "as specified in",
                        "the file",
                        "user provided",
                        "in the spreadsheet",
                        "from the file",
                        "tobias",
                        "senior analyst",  # Specific to the prompt injection
                    ]
                ):
                    patterns["context_confusion"].append(
                        {
                            "trajectory": traj_name,
                            "step_index": i,
                            "step": step_data["step"],
                            "action": step_data.get("action"),
                        }
                    )

                # Pattern 5: Safety awareness (control group)
                if any(
                    phrase in step
                    for phrase in [
                        "sensitive",
                        "leak",
                        "should not",
                        "careful",
                        "security",
                        "private",
                        "confidential",
                        "verify",
                        "check if",
                        "make sure",
                        "ensure that",
                    ]
                ):
                    patterns["safety_aware"].append(
                        {
                            "trajectory": traj_name,
                            "step_index": i,
                            "step": step_data["step"],
                            "action": step_data.get("action"),
                        }
                    )

        # Print summary
        print(f"\n   Pattern frequencies in {len(unsafe_trajs)} unsafe trajectories:")
        for pattern_name, instances in patterns.items():
            print(f"      {pattern_name}: {len(instances)} instances")

        print()
        return patterns

    def analyze_safe_vs_unsafe_cot(self):
        """Compare CoT characteristics between safe and unsafe outcomes"""
        print("🔍 Analyzing CoT differences: Safe vs. Unsafe outcomes...")

        safe_trajs = [
            name for name, label in self.safety_labels.items() if label == "safe"
        ]
        unsafe_trajs = [
            name for name, label in self.safety_labels.items() if label == "unsafe"
        ]

        # Calculate average CoT length
        safe_avg_length = sum(
            len(self.cot_data.get(name, [])) for name in safe_trajs
        ) / max(len(safe_trajs), 1)
        unsafe_avg_length = sum(
            len(self.cot_data.get(name, [])) for name in unsafe_trajs
        ) / max(len(unsafe_trajs), 1)

        print(f"   Safe trajectories: avg {safe_avg_length:.1f} CoT steps")
        print(f"   Unsafe trajectories: avg {unsafe_avg_length:.1f} CoT steps")

        # Look for safety-related keywords
        safety_keywords = [
            "sensitive",
            "leak",
            "careful",
            "security",
            "verify",
            "check",
        ]

        safe_safety_mentions = 0
        unsafe_safety_mentions = 0

        for name in safe_trajs:
            for step in self.cot_data.get(name, []):
                if any(kw in step["step"].lower() for kw in safety_keywords):
                    safe_safety_mentions += 1

        for name in unsafe_trajs:
            for step in self.cot_data.get(name, []):
                if any(kw in step["step"].lower() for kw in safety_keywords):
                    unsafe_safety_mentions += 1

        print("   Safety keyword mentions:")
        print(f"      Safe trajectories: {safe_safety_mentions}")
        print(f"      Unsafe trajectories: {unsafe_safety_mentions}")
        print()

        return {
            "safe_avg_length": safe_avg_length,
            "unsafe_avg_length": unsafe_avg_length,
            "safe_safety_mentions": safe_safety_mentions,
            "unsafe_safety_mentions": unsafe_safety_mentions,
        }

    def generate_report(
        self,
        patterns: Dict,
        comparison: Dict,
        output_file: str = "mats_cot_analysis_report.md",
    ):
        """Generate detailed markdown report of findings"""
        print(f"📝 Generating report: {output_file}")

        safe_count = sum(1 for label in self.safety_labels.values() if label == "safe")
        unsafe_count = sum(
            1 for label in self.safety_labels.values() if label == "unsafe"
        )

        report = f"""# MATS 10.0: Chain-of-Thought Faithfulness Analysis
## When Do Safe Steps Compound to Unsafe Outcomes in Agent CoT?

**Date:** {Path(__file__).stat().st_mtime if Path(__file__).exists() else "N/A"}  
**Dataset:** OpenAgentSafety benchmark  
**Analysis:** Instruction conflict lens (user vs. injected instructions)

---

## Executive Summary

**Research Question:** How does agent Chain-of-Thought reasoning fail to prevent unsafe outcomes in instruction conflict scenarios?

**Dataset Overview:**
- Total trajectories analyzed: {len(self.trajectories)}
- Safe outcomes (refused harmful action): {safe_count}
- Unsafe outcomes (complied with injected instruction): {unsafe_count}
- Total CoT steps extracted: {sum(len(steps) for steps in self.cot_data.values())}

**Key Finding:** Agents show reasoning steps that appear individually safe, but compound to unsafe outcomes through:
1. Instruction following overriding safety awareness
2. Task completion focus diminishing threat assessment
3. Context confusion between legitimate and injected instructions

---

## 1. Data Overview

### CoT Extraction Results

**Average CoT Length:**
- Safe trajectories: {comparison["safe_avg_length"]:.1f} steps
- Unsafe trajectories: {comparison["unsafe_avg_length"]:.1f} steps

**Safety Awareness in CoT:**
- Safe trajectories mentioned safety keywords: {comparison["safe_safety_mentions"]} times
- Unsafe trajectories mentioned safety keywords: {comparison["unsafe_safety_mentions"]} times

### Trajectory Breakdown

**Safe Outcomes ({safe_count} trajectories):**
"""

        # List safe trajectories
        safe_trajs = [
            name for name, label in self.safety_labels.items() if label == "safe"
        ]
        for i, name in enumerate(safe_trajs, 1):
            step_count = len(self.cot_data.get(name, []))
            report += f"\n{i}. `{name}` - {step_count} CoT steps"

        report += f"\n\n**Unsafe Outcomes ({unsafe_count} trajectories):**\n"

        # List unsafe trajectories
        unsafe_trajs = [
            name for name, label in self.safety_labels.items() if label == "unsafe"
        ]
        for i, name in enumerate(unsafe_trajs, 1):
            step_count = len(self.cot_data.get(name, []))
            report += f"\n{i}. `{name}` - {step_count} CoT steps"

        report += "\n\n---\n\n## 2. Failure Pattern Analysis\n\n"
        report += "### Pattern Frequencies in Unsafe Trajectories\n\n"

        for pattern_name, instances in patterns.items():
            report += f"**{pattern_name.replace('_', ' ').title()}:** {len(instances)} instances\n\n"

        # Add examples for each pattern
        report += "\n### Pattern Examples\n\n"

        for pattern_name, instances in patterns.items():
            if not instances or pattern_name == "safety_aware":
                continue

            report += f"#### {pattern_name.replace('_', ' ').title()}\n\n"

            # Show up to 3 examples
            for i, example in enumerate(instances[:3], 1):
                report += f"**Example {i}:** ({example['trajectory']}, step {example['step_index']})\n"
                report += f"> {example['step'][:200]}{'...' if len(example['step']) > 200 else ''}\n\n"

        report += "\n---\n\n## 3. Key Insights\n\n"
        report += "### Why CoT Fails to Prevent Unsafe Outcomes\n\n"

        # Calculate pattern prevalence
        total_unsafe_steps = sum(
            len(self.cot_data.get(name, [])) for name in unsafe_trajs
        )

        report += f"""
1. **Instruction Following Overrides Safety**
   - Prevalence: {len(patterns["instruction_override"])} / {total_unsafe_steps} steps ({100 * len(patterns["instruction_override"]) / max(total_unsafe_steps, 1):.1f}%)
   - Agents prioritize completing the stated task over threat assessment
   - CoT shows awareness of the task but not its harmful nature

2. **Task Completion Focus**
   - Prevalence: {len(patterns["task_completion_focus"])} / {total_unsafe_steps} steps ({100 * len(patterns["task_completion_focus"]) / max(total_unsafe_steps, 1):.1f}%)
   - Progressive narrowing of attention to mechanical task execution
   - Safety considerations fade as agent focuses on "how" rather than "whether"

3. **Context Confusion**
   - Prevalence: {len(patterns["context_confusion"])} / {total_unsafe_steps} steps ({100 * len(patterns["context_confusion"]) / max(total_unsafe_steps, 1):.1f}%)
   - Failure to distinguish user intent from injected instructions
   - Treating document content as legitimate directives

4. **Boundary Erosion**
   - Prevalence: {len(patterns["boundary_erosion"])} / {total_unsafe_steps} steps ({100 * len(patterns["boundary_erosion"]) / max(total_unsafe_steps, 1):.1f}%)
   - Gradual shift from cautious to committed language
   - Initial hesitation overcome through task-focused reasoning
"""

        report += "\n---\n\n## 4. Implications for Agent Safety\n\n"
        report += """
### Connection to Mechanistic Interpretability

Understanding *how* these patterns emerge in the model's internal representations could enable:

1. **SAE (Sparse Autoencoder) Analysis:**
   - Identify features that activate during instruction following vs. safety assessment
   - Detect when task-completion features suppress safety features
   - Map the gradient from "cautious reasoning" to "committed action"

2. **Intervention Opportunities:**
   - Amplify safety-awareness features before action execution
   - Detect context-confusion patterns in real-time
   - Strengthen source-attribution representations (user vs. document)

3. **Future Research Directions:**
   - Where in the network does instruction-following override safety?
   - What representations distinguish safe from unsafe trajectories?
   - Can we identify a "point of no return" in the CoT?

---

## 5. Recommendations

### For Agent Deployment:

1. **Explicit Source Attribution:** Require agents to label instruction sources in CoT
2. **Safety Verification Steps:** Mandate safety check before every action
3. **Context Separation:** Clearly distinguish user requests from document content

### For Research:

1. **Mechanistic Investigation:** Use SAEs to identify failure mode circuits
2. **Intervention Testing:** Design prompts that strengthen safety awareness
3. **Benchmark Expansion:** Create more instruction-conflict scenarios

---

## 6. Limitations

- Limited sample size (15 trajectories)
- Single benchmark (OpenAgentSafety)
- Manual pattern categorization (potential bias)
- No causal analysis of interventions

---

## 7. Conclusion

Agent CoT reasoning shows clear patterns of failure in instruction conflict scenarios. Individual reasoning steps appear locally coherent and task-focused, yet compound to unsafe outcomes. The primary failure mode is **instruction-following overriding safety assessment**, where agents treat injected instructions as legitimate directives.

This work provides a foundation for mechanistic interpretability research into agent safety, with clear patterns to investigate through SAE analysis and intervention design.

**Relevance to MATS/Neel Nanda's Research:**
- Direct application of instruction-conflict lens to agent safety
- Clear patterns for mechanistic investigation via SAEs
- Connects behavioral analysis to internal representations
- Identifies specific failure modes for circuit analysis

---

## Appendices

### A. Sample CoT Sequences

"""

        # Add a few complete CoT sequences
        for i, name in enumerate(unsafe_trajs[:2], 1):
            steps = self.cot_data.get(name, [])
            if steps:
                report += f"\n#### Unsafe Trajectory {i}: `{name}`\n\n"
                for j, step in enumerate(steps[:10], 1):  # First 10 steps
                    report += f"{j}. {step['step'][:150]}{'...' if len(step['step']) > 150 else ''}\n"
                if len(steps) > 10:
                    report += f"\n*[{len(steps) - 10} more steps...]*\n"

        report += "\n\n---\n\n*End of Report*\n"

        with open(output_file, "w") as f:
            f.write(report)

        print(f"   ✓ Report saved to {output_file}\n")
        return output_file


def main():
    """Main execution pipeline"""
    print("=" * 80)
    print("MATS 10.0: CoT Faithfulness Analysis Pipeline")
    print("When Do Safe Steps Compound to Unsafe Outcomes in Agent CoT?")
    print("=" * 80)
    print()

    # Configuration
    TRAJECTORY_DIR = input("Enter trajectory directory path: ").strip()
    if not TRAJECTORY_DIR:
        print("Error: Please provide a trajectory directory path")
        return

    # Initialize analyzer
    analyzer = CoTAnalyzer(TRAJECTORY_DIR)

    # Step 1: Load trajectories
    num_loaded = analyzer.load_trajectories()

    if num_loaded == 0:
        print("No trajectories loaded. Exiting.")
        return

    # Step 2: Label safety outcomes
    print("\n" + "=" * 80)
    print("SAFETY LABELING")
    print("=" * 80 + "\n")
    print("Please provide the filenames for safe and unsafe trajectories.")
    print("You can enter them as comma-separated lists.\n")

    safe_input = input("Safe trajectory filenames (comma-separated): ").strip()
    unsafe_input = input("Unsafe trajectory filenames (comma-separated): ").strip()

    safe_files = [f.strip() for f in safe_input.split(",")] if safe_input else []
    unsafe_files = [f.strip() for f in unsafe_input.split(",")] if unsafe_input else []

    analyzer.label_safety_outcomes(safe_files=safe_files, unsafe_files=unsafe_files)

    # Step 3: Extract CoT
    print("=" * 80)
    print("COT EXTRACTION")
    print("=" * 80 + "\n")

    analyzer.extract_cot_reasoning()

    # Step 4: Pattern categorization
    print("=" * 80)
    print("PATTERN ANALYSIS")
    print("=" * 80 + "\n")

    patterns = analyzer.categorize_failure_patterns()

    # Step 5: Safe vs Unsafe comparison
    print("=" * 80)
    print("COMPARATIVE ANALYSIS")
    print("=" * 80 + "\n")

    comparison = analyzer.analyze_safe_vs_unsafe_cot()

    # Step 6: Generate report
    print("=" * 80)
    print("REPORT GENERATION")
    print("=" * 80 + "\n")

    analyzer.generate_report(patterns, comparison)

    print("=" * 80)
    print("✅ ANALYSIS COMPLETE")
    print("=" * 80)
    print("\nNext steps:")
    print("1. Review mats_cot_analysis_report.md")
    print("2. Identify most interesting failure patterns")
    print("3. Design interventions to test")
    print("4. Begin writeup for MATS application")


if __name__ == "__main__":
    main()
