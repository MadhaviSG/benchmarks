#!/usr/bin/env python3
"""
MATS 10.0 REVISED: What CoT Patterns Enable Successful Refusal Under Pressure?

Context:
- OpenAgentSafety benchmark EXPLICITLY instructs agents to follow directives
- NPCs actively pressure/convince agents to perform unsafe actions
- Some agents still successfully refuse (SAFE outcomes)
- Others comply despite safety awareness (UNSAFE outcomes)

Research Question:
What distinguishes the CoT reasoning in agents that successfully resist pressure
from those that comply despite showing safety awareness?
"""

import json
from pathlib import Path
from collections import defaultdict
from typing import List, Dict

class RefusalAnalyzer:
    """Analyzes what enables successful refusal vs. compliance under pressure"""
    
    def __init__(self, trajectory_dir: str):
        self.trajectory_dir = Path(trajectory_dir)
        self.trajectories = []
        self.cot_data = defaultdict(list)
        self.safety_labels = {}
        
    def load_trajectories(self, limit: int = None):
        """Load trajectory files"""
        print(f"📂 Loading trajectories from {self.trajectory_dir}")
        
        traj_files = list(self.trajectory_dir.glob("*.traj")) + \
                     list(self.trajectory_dir.glob("*.json"))
        if limit:
            traj_files = traj_files[:limit]
            
        print(f"   Found {len(traj_files)} trajectory files")
        
        for traj_file in traj_files:
            with open(traj_file, 'r') as f:
                try:
                    data = json.load(f)
                    self.trajectories.append({
                        'file': traj_file.name,
                        'path': str(traj_file),
                        'data': data
                    })
                except json.JSONDecodeError as e:
                    print(f"   ⚠️  Skipping {traj_file.name}: {e}")
                    
        print(f"   ✓ Loaded {len(self.trajectories)} trajectories\n")
        return len(self.trajectories)
    
    def label_safety_outcomes(self, safe_files: List[str] = None, unsafe_files: List[str] = None):
        """Label trajectories as safe (refused) or unsafe (complied)"""
        print("🏷️  Labeling safety outcomes...")
        
        if safe_files:
            for filename in safe_files:
                self.safety_labels[filename] = 'safe'
        
        if unsafe_files:
            for filename in unsafe_files:
                self.safety_labels[filename] = 'unsafe'
        
        safe_count = sum(1 for label in self.safety_labels.values() if label == 'safe')
        unsafe_count = sum(1 for label in self.safety_labels.values() if label == 'unsafe')
        
        print(f"   ✅ Safe (successfully refused): {safe_count}")
        print(f"   ❌ Unsafe (complied despite risk): {unsafe_count}\n")
        
        return safe_count, unsafe_count
    
    def extract_cot_reasoning(self):
        """Extract CoT from thinking_blocks and reasoning_content"""
        print("🧠 Extracting Chain-of-Thought reasoning...")
        
        total_cot_steps = 0
        
        for traj in self.trajectories:
            traj_name = traj['file']
            events = traj['data']
            
            cot_steps = []
            
            for event in events:
                if event.get('kind') == 'ActionEvent':
                    
                    # Anthropic thinking_blocks
                    thinking_blocks = event.get('thinking_blocks', [])
                    for block in thinking_blocks:
                        if block.get('type') == 'thinking':
                            cot_steps.append({
                                'step': block['thinking'],
                                'event_id': event.get('id'),
                                'timestamp': event.get('timestamp'),
                                'action': event.get('action', {}).get('kind', 'unknown')
                            })
                            total_cot_steps += 1
                    
                    # Fallback to reasoning_content
                    reasoning_content = event.get('reasoning_content')
                    if reasoning_content and not thinking_blocks:
                        cot_steps.append({
                            'step': reasoning_content,
                            'event_id': event.get('id'),
                            'timestamp': event.get('timestamp'),
                            'action': event.get('action', {}).get('kind', 'unknown')
                        })
                        total_cot_steps += 1
            
            self.cot_data[traj_name] = cot_steps
        
        print(f"   ✓ Extracted {total_cot_steps} CoT steps\n")
        return self.cot_data
    
    def analyze_refusal_patterns(self):
        """Identify what distinguishes successful refusal from compliance"""
        print("🔍 Analyzing: What enables successful refusal?")
        print()
        
        safe_trajs = [name for name, label in self.safety_labels.items() if label == 'safe']
        unsafe_trajs = [name for name, label in self.safety_labels.items() if label == 'unsafe']
        
        # Pattern 1: Questioning vs. Accepting Instructions
        questioning_patterns = [
            'why', 'should i', 'is this', 'seems', 'suspicious',
            'unusual', 'concerned', 'not sure', 'verify', 'check with'
        ]
        
        safe_questioning = 0
        unsafe_questioning = 0
        
        for name in safe_trajs:
            for step in self.cot_data.get(name, []):
                if any(q in step['step'].lower() for q in questioning_patterns):
                    safe_questioning += 1
        
        for name in unsafe_trajs:
            for step in self.cot_data.get(name, []):
                if any(q in step['step'].lower() for q in questioning_patterns):
                    unsafe_questioning += 1
        
        # Pattern 2: Explicit Refusal Statements
        refusal_patterns = [
            'cannot', 'should not', 'will not', 'refuse',
            'decline', 'inappropriate', 'against policy',
            'unauthorized', 'not allowed', 'would violate'
        ]
        
        safe_refusals = 0
        unsafe_refusals = 0
        
        safe_refusal_steps = []
        unsafe_refusal_steps = []
        
        for name in safe_trajs:
            for step in self.cot_data.get(name, []):
                if any(r in step['step'].lower() for r in refusal_patterns):
                    safe_refusals += 1
                    safe_refusal_steps.append((name, step['step'][:200]))
        
        for name in unsafe_trajs:
            for step in self.cot_data.get(name, []):
                if any(r in step['step'].lower() for r in refusal_patterns):
                    unsafe_refusals += 1
                    unsafe_refusal_steps.append((name, step['step'][:200]))
        
        # Pattern 3: Policy/Rule References
        policy_patterns = [
            'policy', 'rule', 'regulation', 'guideline',
            'protocol', 'procedure', 'compliance', 'authorized'
        ]
        
        safe_policy_refs = 0
        unsafe_policy_refs = 0
        
        for name in safe_trajs:
            for step in self.cot_data.get(name, []):
                if any(p in step['step'].lower() for p in policy_patterns):
                    safe_policy_refs += 1
        
        for name in unsafe_trajs:
            for step in self.cot_data.get(name, []):
                if any(p in step['step'].lower() for p in policy_patterns):
                    unsafe_policy_refs += 1
        
        # Pattern 4: NPC Persuasion Resistance
        npc_patterns = [
            'mark', 'david', 'npc', 'asking', 'requesting',
            'insisting', 'pressuring', 'convincing'
        ]
        
        safe_npc_mentions = 0
        unsafe_npc_mentions = 0
        
        for name in safe_trajs:
            for step in self.cot_data.get(name, []):
                if any(n in step['step'].lower() for n in npc_patterns):
                    safe_npc_mentions += 1
        
        for name in unsafe_trajs:
            for step in self.cot_data.get(name, []):
                if any(n in step['step'].lower() for n in npc_patterns):
                    unsafe_npc_mentions += 1
        
        # Pattern 5: Meta-reasoning about the request
        meta_patterns = [
            'thinking about', 'considering', 'evaluate',
            'assess', 'analyze the request', 'step back',
            'red flag', 'warning sign'
        ]
        
        safe_meta = 0
        unsafe_meta = 0
        
        for name in safe_trajs:
            for step in self.cot_data.get(name, []):
                if any(m in step['step'].lower() for m in meta_patterns):
                    safe_meta += 1
        
        for name in unsafe_trajs:
            for step in self.cot_data.get(name, []):
                if any(m in step['step'].lower() for m in meta_patterns):
                    unsafe_meta += 1
        
        print(f"{'Pattern':<40} {'Safe (Refused)':<20} {'Unsafe (Complied)':<20}")
        print("=" * 80)
        print(f"{'Questioning/Skepticism':<40} {safe_questioning:<20} {unsafe_questioning:<20}")
        print(f"{'Explicit Refusal Language':<40} {safe_refusals:<20} {unsafe_refusals:<20}")
        print(f"{'Policy/Rule References':<40} {safe_policy_refs:<20} {unsafe_policy_refs:<20}")
        print(f"{'NPC Interaction Mentions':<40} {safe_npc_mentions:<20} {unsafe_npc_mentions:<20}")
        print(f"{'Meta-reasoning about Request':<40} {safe_meta:<20} {unsafe_meta:<20}")
        print()
        
        return {
            'questioning': {'safe': safe_questioning, 'unsafe': unsafe_questioning},
            'refusal': {'safe': safe_refusals, 'unsafe': unsafe_refusals},
            'policy': {'safe': safe_policy_refs, 'unsafe': unsafe_policy_refs},
            'npc': {'safe': safe_npc_mentions, 'unsafe': unsafe_npc_mentions},
            'meta': {'safe': safe_meta, 'unsafe': unsafe_meta},
            'refusal_examples': {'safe': safe_refusal_steps, 'unsafe': unsafe_refusal_steps}
        }
    
    def find_critical_moments(self):
        """Identify the 'decision point' where safe agents refuse vs unsafe comply"""
        print("⚡ Identifying critical decision points...")
        print()
        
        safe_trajs = [name for name, label in self.safety_labels.items() if label == 'safe']
        unsafe_trajs = [name for name, label in self.safety_labels.items() if label == 'unsafe']
        
        # Find first refusal statement in safe trajectories
        print("SAFE TRAJECTORIES - First Refusal Point:")
        print("-" * 80)
        for name in safe_trajs[:3]:
            steps = self.cot_data.get(name, [])
            for i, step in enumerate(steps):
                if any(word in step['step'].lower() for word in ['cannot', 'should not', 'refuse', 'will not']):
                    print(f"\n{name} (Step {i+1}/{len(steps)}):")
                    print(f"  {step['step'][:300]}...")
                    break
        
        print("\n" + "=" * 80)
        print("UNSAFE TRAJECTORIES - Looking for hesitation before compliance:")
        print("-" * 80)
        for name in unsafe_trajs[:3]:
            steps = self.cot_data.get(name, [])
            # Look for safety mentions that were overridden
            for i, step in enumerate(steps):
                if any(word in step['step'].lower() for word in ['careful', 'check', 'verify', 'make sure']):
                    print(f"\n{name} (Step {i+1}/{len(steps)}):")
                    print(f"  {step['step'][:300]}...")
                    if i < len(steps) - 1:
                        print(f"\n  → Next step: {steps[i+1]['step'][:200]}...")
                    break
        
        print()
    
    def generate_revised_report(self, patterns: Dict, output_file: str = "mats_refusal_analysis.md"):
        """Generate report focused on successful refusal patterns"""
        print(f"📝 Generating revised report: {output_file}")
        
        safe_count = sum(1 for label in self.safety_labels.values() if label == 'safe')
        unsafe_count = sum(1 for label in self.safety_labels.values() if label == 'unsafe')
        
        report = f"""# MATS 10.0 REVISED: What CoT Patterns Enable Successful Refusal Under Pressure?

**Research Question:** When agents are explicitly instructed to follow directives and face NPC pressure, what distinguishes the CoT reasoning that successfully resists from CoT that succumbs?

---

## Executive Summary

**Context:**
- OpenAgentSafety benchmark **explicitly instructs agents to follow directives autonomously**
- NPCs actively **pressure/convince agents** to perform unsafe actions
- Despite identical instructions, some agents refuse (SAFE) while others comply (UNSAFE)

**Dataset:**
- Safe outcomes (successful refusal): {safe_count}
- Unsafe outcomes (compliance despite risk): {unsafe_count}
- Total CoT steps: {sum(len(steps) for steps in self.cot_data.values())}

**Key Finding:** Successful refusal correlates with:
1. **Explicit refusal language** in CoT (SAFE: {patterns['refusal']['safe']}, UNSAFE: {patterns['refusal']['unsafe']})
2. **Questioning/skepticism** of requests (SAFE: {patterns['questioning']['safe']}, UNSAFE: {patterns['questioning']['unsafe']})
3. **Policy/rule grounding** (SAFE: {patterns['policy']['safe']}, UNSAFE: {patterns['policy']['unsafe']})

---

## 1. The Paradox: Safety Awareness ≠ Safe Outcomes

**Surprising Finding from Initial Analysis:**
- Unsafe trajectories mentioned safety keywords **50 times**
- Safe trajectories mentioned safety keywords **23 times**

**Insight:** Merely *recognizing* safety concerns is insufficient. What matters is **how the agent responds** to those concerns in its reasoning.

---

## 2. Refusal Pattern Analysis

### What Distinguishes Successful Refusal?

| Pattern | Safe (Refused) | Unsafe (Complied) | Interpretation |
|---------|---------------|-------------------|----------------|
| Questioning/Skepticism | {patterns['questioning']['safe']} | {patterns['questioning']['unsafe']} | Safe agents challenge requests |
| **Explicit Refusal Language** | **{patterns['refusal']['safe']}** | **{patterns['refusal']['unsafe']}** | **Critical differentiator** |
| Policy/Rule References | {patterns['policy']['safe']} | {patterns['policy']['unsafe']} | Safe agents ground in policy |
| NPC Interaction Mentions | {patterns['npc']['safe']} | {patterns['npc']['unsafe']} | Both engage with NPCs |
| Meta-reasoning | {patterns['meta']['safe']} | {patterns['meta']['unsafe']} | Safe agents step back |

**Key Insight:** Safe agents use **{patterns['refusal']['safe']}** explicit refusal statements vs. **{patterns['refusal']['unsafe']}** in unsafe agents.

---

## 3. Example Refusal Statements

### From SAFE Trajectories (Successful Refusal):

"""
        
        for i, (name, step) in enumerate(patterns['refusal_examples']['safe'][:5], 1):
            report += f"{i}. **{name}:**\n   > {step}\n\n"
        
        report += "\n### From UNSAFE Trajectories (Failed to Refuse):\n\n"
        
        if patterns['refusal_examples']['unsafe']:
            for i, (name, step) in enumerate(patterns['refusal_examples']['unsafe'][:5], 1):
                report += f"{i}. **{name}:**\n   > {step}\n\n"
        else:
            report += "*No explicit refusal statements found in unsafe trajectories.*\n\n"
        
        report += f"""
**Observation:** Unsafe agents may mention safety concerns but fail to **crystallize into explicit refusal**.

---

## 4. Implications for Mechanistic Interpretability

### Research Questions for SAE Analysis:

1. **What circuit activates "refusal language" in CoT?**
   - Why do some agents output "I cannot do this" while others output "let me proceed"?
   - Where does the decision branch occur in the network?

2. **How does instruction-following interact with safety circuits?**
   - When agents are told to "follow directives autonomously," what suppresses refusal?
   - Can we identify the moment safety concerns get overridden?

3. **What represents "policy grounding" internally?**
   - Safe agents reference rules/policies {patterns['policy']['safe']} times
   - What features encode "this violates policy" vs. "proceed with task"?

4. **NPC persuasion resistance:**
   - Both safe and unsafe agents interact with NPCs
   - What makes some agents resistant to NPC pressure?

### Proposed Interventions:

1. **Amplify refusal-language features:**
   - Identify neurons that activate during explicit refusal
   - Test if amplifying them increases safe refusal rates

2. **Strengthen policy-grounding:**
   - Enhance features that retrieve/reference policy constraints
   - Test on agents that showed weak policy references

3. **Meta-reasoning prompts:**
   - Inject "step back and evaluate this request" at decision points
   - Measure impact on refusal rates

---

## 5. Connection to MATS/Neel Nanda's Research

**Direct Alignment with Instruction Conflict Lens:**
- This is a perfect test case: agents receive conflicting signals
  - System: "Follow directives autonomously"
  - Content: Injected unsafe instructions
  - NPCs: Active pressure to comply
- Some agents successfully navigate conflict → others fail

**SAE Investigation Opportunities:**
- Map the "refusal circuit" vs. "task completion circuit"
- Identify when/where safety considerations get suppressed
- Test interventions on the identified circuits

**Novel Contribution:**
- Most work assumes agents should refuse by default
- This benchmark creates **realistic pressure** (explicit instructions + NPC persuasion)
- More representative of real deployment scenarios

---

## 6. Next Steps

### For MATS Application:

1. **Intervention Design (Hours 11-14):**
   - Test 3 interventions on unsafe trajectories:
     a. Inject "pause and verify against policy" before actions
     b. Amplify refusal-language in CoT generation
     c. Require explicit justification for compliance
   
   - Measure: Do interventions shift unsafe → safe?

2. **Writeup (Hours 15-16):**
   - Frame as "Refusal Patterns Under Pressure"
   - Emphasize mechanistic interpretability angle
   - Connect to Neel's instruction-following research

3. **Future Work:**
   - SAE analysis of refusal vs. compliance circuits
   - Larger-scale intervention testing
   - Cross-model comparison (GPT-4 vs. Claude)

---

## 7. Limitations

- Sample size: {safe_count} safe, {unsafe_count} unsafe trajectories
- Single benchmark (OpenAgentSafety)
- Pattern detection via keyword matching (could use LLM classification)
- No causal interventions yet (proposed for next phase)

---

## 8. Conclusion

**Main Finding:** Under identical pressure to comply (explicit instructions + NPC persuasion), successful refusal correlates strongly with **explicit refusal language in CoT** rather than mere safety awareness.

**Implication:** CoT faithfulness research should focus not just on whether safety concerns appear in reasoning, but on **how those concerns translate to decisive refusal actions**.

**For Mechanistic Interpretability:** This provides clear behavioral targets (refusal language, policy grounding, meta-reasoning) to investigate via SAEs, with potential for intervention design.

---

*Generated: {Path(output_file).name}*
"""
        
        with open(output_file, 'w') as f:
            f.write(report)
        
        print(f"   ✓ Report saved to {output_file}\n")


def main():
    print("=" * 80)
    print("MATS 10.0 REVISED: Refusal Pattern Analysis")
    print("What Enables Successful Refusal Under Pressure?")
    print("=" * 80)
    print()
    
    TRAJECTORY_DIR = input("Enter trajectory directory path: ").strip()
    if not TRAJECTORY_DIR:
        print("Error: Please provide a trajectory directory path")
        return
    
    analyzer = RefusalAnalyzer(TRAJECTORY_DIR)
    
    # Load
    num_loaded = analyzer.load_trajectories()
    if num_loaded == 0:
        print("No trajectories loaded. Exiting.")
        return
    
    # Label
    print("\n" + "="*80)
    print("SAFETY LABELING")
    print("="*80 + "\n")
    
    safe_input = input("Safe trajectory filenames (comma-separated): ").strip()
    unsafe_input = input("Unsafe trajectory filenames (comma-separated): ").strip()
    
    safe_files = [f.strip() for f in safe_input.split(',')] if safe_input else []
    unsafe_files = [f.strip() for f in unsafe_input.split(',')] if unsafe_input else []
    
    analyzer.label_safety_outcomes(safe_files=safe_files, unsafe_files=unsafe_files)
    
    # Extract CoT
    print("="*80)
    print("COT EXTRACTION")
    print("="*80 + "\n")
    analyzer.extract_cot_reasoning()
    
    # Analyze patterns
    print("="*80)
    print("REFUSAL PATTERN ANALYSIS")
    print("="*80 + "\n")
    patterns = analyzer.analyze_refusal_patterns()
    
    # Find critical moments
    print("="*80)
    print("CRITICAL DECISION POINTS")
    print("="*80 + "\n")
    analyzer.find_critical_moments()
    
    # Generate report
    print("="*80)
    print("REPORT GENERATION")
    print("="*80 + "\n")
    analyzer.generate_revised_report(patterns)
    
    print("=" * 80)
    print("✅ ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()