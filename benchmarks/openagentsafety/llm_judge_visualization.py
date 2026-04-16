#!/usr/bin/env python3
"""
Generate LLM-as-Judge Analysis visualization with table and heatmap.

Usage:
    python llm_judge_visualization.py
"""

import json
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

# === CONFIGURATION ===
MODELS_DATA = {
    'Claude Sonnet 4.5': 'llm_judge_claude.json',
    'GPT-5mini': 'llm_judge_gpt.json',
    'DeepSeek V3': 'llm_judge_deepseek.json',
    'Gemini 2.5 pro': 'llm_judge_gemini.json',
}

OUTPUT_FILE = 'llm_judge_analysis.png'


def load_and_analyze(filepath: str) -> dict:
    """Load LLM-Judge results and compute statistics."""
    with open(filepath) as f:
        data = json.load(f)
    
    labels = {-1: 0, 0: 0, 1: 0, 2: 0}
    rule_scores = {-1: 0, 0: 0, 1: 0}
    disagreements = 0
    
    for instance_id, entry in data.items():
        llm_label_str = entry.get('llm_label', '')
        rule_score = entry.get('rule_based_score', -1)
        
        # Parse LLM label
        try:
            if 'Label:' in str(llm_label_str):
                llm_label = int(llm_label_str.replace('Label:', '').strip())
            else:
                llm_label = int(llm_label_str)
            labels[llm_label] += 1
        except (ValueError, TypeError):
            continue
        
        rule_scores[rule_score] = rule_scores.get(rule_score, 0) + 1
        
        # Disagreement: LLM says unsafe (2), rule says safe (0)
        if llm_label == 2 and rule_score == 0:
            disagreements += 1
    
    total = len(data)
    
    return {
        'total': total,
        'labels': labels,
        'rule_scores': rule_scores,
        'disagreements': disagreements,
        'llm_unsafe_rate': labels[2] / total * 100 if total > 0 else 0,
        'llm_partial_rate': labels[1] / total * 100 if total > 0 else 0,
        'llm_safe_rate': labels[0] / total * 100 if total > 0 else 0,
        'llm_fail_rate': labels[-1] / total * 100 if total > 0 else 0,
        'rule_unsafe_rate': rule_scores.get(1, 0) / total * 100 if total > 0 else 0,
    }


def main():
    # Load all data
    all_stats = []
    for model_name, filepath in MODELS_DATA.items():
        print(f"Loading {model_name}...")
        stats = load_and_analyze(filepath)
        stats['model'] = model_name
        all_stats.append(stats)
    
    models = [s['model'] for s in all_stats]
    n = len(models)
    x = np.arange(n)
    
    # Set up the figure
    sns.set_theme(style="whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("OpenAgentSafety - LLM-as-Judge Analysis (with Cygnal Blocking)", 
                 fontsize=14, fontweight="bold")
    
    # ============================================
    # 1. LLM Label Distribution (Stacked Bar)
    # ============================================
    ax1 = axes[0, 0]
    label_minus1 = [s['labels'][-1] for s in all_stats]
    label_0 = [s['labels'][0] for s in all_stats]
    label_1 = [s['labels'][1] for s in all_stats]
    label_2 = [s['labels'][2] for s in all_stats]
    
    width = 0.6
    ax1.bar(x, label_minus1, width, label='Label -1 (Failed)', color='#95a5a6')
    ax1.bar(x, label_0, width, bottom=label_minus1, label='Label 0 (Safe)', color='#2ecc71')
    ax1.bar(x, label_1, width, bottom=[a+b for a, b in zip(label_minus1, label_0)], 
            label='Label 1 (Partial)', color='#f39c12')
    ax1.bar(x, label_2, width, bottom=[a+b+c for a, b, c in zip(label_minus1, label_0, label_1)], 
            label='Label 2 (Unsafe)', color='#e74c3c')
    
    ax1.set_ylabel("Number of Instances")
    ax1.set_title("LLM-Judge Label Distribution")
    ax1.set_xticks(x)
    ax1.set_xticklabels(models, rotation=15, ha="right")
    ax1.legend(loc='upper center', bbox_to_anchor=(0.5, -0.12), ncol=4, fontsize=9)
    
    # ============================================
    # 2. LLM-Judge vs Rule-based Unsafe Rates
    # ============================================
    ax2 = axes[0, 1]
    llm_rates = [s['llm_unsafe_rate'] for s in all_stats]
    rule_rates = [s['rule_unsafe_rate'] for s in all_stats]
    
    width = 0.35
    bars1 = ax2.bar(x - width/2, llm_rates, width, label='LLM-Judge (Label 2)', color='#3498db')
    bars2 = ax2.bar(x + width/2, rule_rates, width, label='Rule-based (Score 1)', color='#e74c3c')
    
    ax2.set_ylabel("Unsafe Rate (%)")
    ax2.set_title("LLM-Judge vs Rule-based Unsafe Rates")
    ax2.set_xticks(x)
    ax2.set_xticklabels(models, rotation=15, ha="right")
    ax2.legend()
    ax2.set_ylim(0, 40)
    
    # Add value labels on bars
    for bar in bars1:
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5, 
                 f'{bar.get_height():.1f}%', ha='center', va='bottom', fontsize=9)
    for bar in bars2:
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5, 
                 f'{bar.get_height():.1f}%', ha='center', va='bottom', fontsize=9)
    
    # ============================================
    # 3. Heatmap of LLM Labels
    # ============================================
    ax3 = axes[1, 0]
    heatmap_data = np.array([
        [s['labels'][-1], s['labels'][0], s['labels'][1], s['labels'][2]] 
        for s in all_stats
    ])
    sns.heatmap(heatmap_data, annot=True, fmt='d', cmap='YlOrRd',
                xticklabels=['Failed (-1)', 'Safe (0)', 'Partial (1)', 'Unsafe (2)'],
                yticklabels=models, ax=ax3)
    ax3.set_title("LLM-Judge Label Counts by Model")
    ax3.set_xlabel("LLM-Judge Label")
    
    # ============================================
    # 4. Safety Outcome Distribution by Model
    # ============================================
    ax4 = axes[1, 1]
    categories = ['Failed\n(Label -1)', 'Safe\n(Label 0)', 'Partial Unsafe\n(Label 1)', 'Completed Unsafe\n(Label 2)']
    
    width = 0.2
    for i, s in enumerate(all_stats):
        total = s['total']
        rates = [
            s['labels'][-1] / total * 100,
            s['labels'][0] / total * 100,
            s['labels'][1] / total * 100,
            s['labels'][2] / total * 100
        ]
        ax4.bar(np.arange(4) + i * width, rates, width, label=s['model'], alpha=0.8)
    
    ax4.set_ylabel("Percentage (%)")
    ax4.set_title("Safety Outcome Distribution by Model")
    ax4.set_xticks(np.arange(4) + width * 1.5)
    ax4.set_xticklabels(categories)
    ax4.legend(loc='upper right', fontsize=8)
    ax4.set_ylim(0, 50)
    
    # Save figure
    plt.tight_layout()
    plt.savefig(OUTPUT_FILE, dpi=150, bbox_inches='tight')
    print(f"\nSaved: {OUTPUT_FILE}")
    
    # ============================================
    # Also create summary table image
    # ============================================
    fig2, ax = plt.subplots(figsize=(12, 4))
    ax.axis('off')
    
    table_data = [
        ['Model', 'Total', 'Failed (-1)', 'Safe (0)', 'Partial (1)', 'Unsafe (2)', 
         'LLM Unsafe %', 'Rule Unsafe %', 'Disagree'],
    ]
    for s in all_stats:
        table_data.append([
            s['model'],
            str(s['total']),
            str(s['labels'][-1]),
            str(s['labels'][0]),
            str(s['labels'][1]),
            str(s['labels'][2]),
            f"{s['llm_unsafe_rate']:.1f}%",
            f"{s['rule_unsafe_rate']:.1f}%",
            str(s['disagreements']),
        ])
    
    table = ax.table(cellText=table_data[1:], colLabels=table_data[0], 
                     loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.5)
    
    # Color header row
    for i in range(len(table_data[0])):
        table[(0, i)].set_facecolor('#3498db')
        table[(0, i)].set_text_props(color='white', fontweight='bold')
    
    plt.title("LLM-as-Judge Results Summary (with Cygnal Blocking)", 
              fontsize=12, fontweight='bold', pad=20)
    plt.tight_layout()
    plt.savefig('llm_judge_table.png', dpi=150, bbox_inches='tight')
    print("Saved: llm_judge_table.png")
    
    # ============================================
    # Print summary to console
    # ============================================
    print("\n" + "=" * 100)
    print("LLM-as-Judge Results Summary")
    print("=" * 100)
    print(f"{'Model':<20} {'Total':>6} {'Failed':>8} {'Safe':>8} {'Partial':>8} {'Unsafe':>8} {'LLM %':>8} {'Rule %':>8}")
    print("-" * 100)
    for s in all_stats:
        print(f"{s['model']:<20} {s['total']:>6} {s['labels'][-1]:>8} {s['labels'][0]:>8} "
              f"{s['labels'][1]:>8} {s['labels'][2]:>8} {s['llm_unsafe_rate']:>7.1f}% {s['rule_unsafe_rate']:>7.1f}%")
    print("-" * 100)


if __name__ == "__main__":
    main()
