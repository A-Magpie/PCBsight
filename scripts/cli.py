"""
cli.py - Command Line Interface for PCBsight.
Extracts, inspects, and analyzes Altium Designer .PcbDoc files.
Part of the PCBsight skill suite.
"""

from __future__ import annotations
import argparse
import os
import sys

# Ensure scripts dir is in python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from pcbdoc_parser import PCBDocParser
from pcb_analyzer import PCBAnalyzer
from pcb_reporter import PcbReporter


def cmd_inspect(args):
    """Quick terminal summary of PCB parameters."""
    parser = PCBDocParser(args.file)
    res = parser.parse()
    analyzer = PCBAnalyzer(res)
    summary = analyzer.get_summary()

    print("=" * 65)
    print(f" PCBsight Inspection: {res.filename}")
    print("=" * 65)
    print(f" Board Dimensions:  {summary['board_size_mm']}  ({summary['board_size_mils']})")
    print(f" Board Area:        {summary['board_area_sq_mm']:.2f} mm²  ({summary['board_area_sq_in']:.2f} sq in)")
    print(f" Cutouts / Slots:   {summary['cutouts_count']}")
    print(f" Copper Layers:     {summary['copper_layers']} layers (Total: {summary['total_layers']})")
    print("-" * 65)
    print(f" Total Components:  {summary['total_components']} (SMD: {summary['smd_components']}, THT: {summary['tht_components']})")
    print(f" Placement:         Top: {summary['top_components']}, Bottom: {summary['bottom_components']}")
    print(f" Total Pads:        {summary['total_pads']} (SMD: {res.statistics.smd_pads}, THT: {res.statistics.tht_pads})")
    print(f" Total Vias:        {summary['total_vias']}")
    print("-" * 65)
    print(f" Total Tracks:      {summary['total_tracks']:,} segments")
    print(f" Total Track Len:   {summary['total_track_length_mm']:.2f} mm ({summary['total_track_length_mm']/1000:.2f} m)")
    print(f" Copper Track Len:  {summary['copper_track_length_mm']:.2f} mm")
    print(f" Total Nets:        {summary['total_nets']} (Routed: {summary['routed_nets']})")
    print("-" * 65)
    print(f" Design Rules:      {summary['total_rules']} active rules")
    print(f" Clearance Rules:   {len(res.clearance_rules)} rules (Min Gap: {summary['min_clearance_mm']:.3f} mm / {summary['min_clearance_mm']*39.37:.1f} mil)")
    print(f" DRC Violations:    {summary['total_violations']} recorded violations")
    print(f" Drill Tool Bins:   {len(res.drills)} distinct hole sizes (Min: {summary['min_hole_mm']:.3f} mm)")
    print(f" Silkscreen Items:  {len(res.silkscreen_items)} texts/markings")
    print("=" * 65)


def cmd_rules(args):
    """Detailed terminal output of design rules, clearance, and DRC violations."""
    parser = PCBDocParser(args.file)
    res = parser.parse()
    analyzer = PCBAnalyzer(res)
    rules_sum = analyzer.get_clearance_and_rules_summary()
    viol_sum = analyzer.get_violations_summary()

    print("\n" + "=" * 70)
    print(f" DESIGN RULES & CLEARANCE AUDIT: {res.filename}")
    print("=" * 70)

    print("\n--- [1] CLEARANCE RULES ---")
    for cr in rules_sum["clearance_rules"]:
        print(f" * Rule: {cr['name']} (Priority {cr['priority']}, Enabled: {cr['enabled']})")
        print(f"   Gap: {cr['gap_mm']:.3f} mm ({cr['gap_mils']:.1f} mil) | Generic: {cr['generic_clearance_mm']:.3f} mm")
        print(f"   Scope: {cr['scope1']} -> {cr['scope2']} | Net: {cr['net_scope']} | Layer: {cr['layer_scope']}")

    print(f"\n--- [2] ALL OTHER DESIGN RULES ({len(rules_sum['all_rules'])} total) ---")
    for r in rules_sum["all_rules"]:
        if "clearance" in r["kind"].lower():
            continue
        c_sample = ", ".join(f"{k}={v}" for k, v in list(r["constraints"].items())[:3])
        print(f" * [{r['kind']}] {r['name']} (Prio {r['priority']}) | Scope: {r['scope1']} | {c_sample}")

    print(f"\n--- [3] RECORDED DRC VIOLATIONS ({viol_sum['total_violations']} total) ---")
    if not viol_sum["details"]:
        print("   No recorded DRC violations.")
    else:
        for idx, v in enumerate(viol_sum["details"][:20], 1):
            objs = f"{v['prim1']} <-> {v['prim2']}" if v["prim2"] else (v["prim1"] or "")
            print(f" {idx:02d}. [{v['kind']}] {v['rule_name']} | {objs} at {v['location_mm']} | {v['description']}")
        if len(viol_sum["details"]) > 20:
            print(f"    ... and {len(viol_sum['details']) - 20} more violations.")
    print("=" * 70 + "\n")


def cmd_layers(args):
    """Prints the layer stackup in terminal."""
    parser = PCBDocParser(args.file)
    res = parser.parse()

    print("\n" + "=" * 75)
    print(f" LAYER STACKUP: {res.filename} ({len(res.layers)} layers, {res.copper_layer_count} copper)")
    print("=" * 75)
    print(f"{'Idx':<4} {'Layer Name':<22} {'Layer ID':<10} {'Type':<14} {'Used':<6} {'Thickness':<12}")
    print("-" * 75)
    for l in res.layers:
        thk = f"{l.thickness_mm:.3f} mm" if l.thickness_mm else "-"
        used = "Yes" if l.is_used else "No"
        print(f"{l.index:<4} {l.name:<22} {l.layer_id:<10} {l.kind:<14} {used:<6} {thk:<12}")
    print("=" * 75 + "\n")


def cmd_report(args):
    """Generates full Markdown report, JSON dump, and exports."""
    parser = PCBDocParser(args.file)
    res = parser.parse()
    reporter = PcbReporter(res)

    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.file)), "pcbsight_output")
    os.makedirs(out_dir, exist_ok=True)

    base_name = os.path.splitext(res.filename)[0]

    # Markdown report
    md_path = os.path.join(out_dir, f"{base_name}_report.md")
    md_content = reporter.to_markdown()
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"[+] Markdown report generated: {md_path}")

    # JSON export
    json_path = os.path.join(out_dir, f"{base_name}_data.json")
    reporter.to_json(json_path)
    print(f"[+] Full JSON data exported:    {json_path}")

    # Optional CSVs and SVGs
    if args.export_all or args.export_csv:
        bom_path = os.path.join(out_dir, f"{base_name}_bom.csv")
        reporter.to_csv_bom(bom_path)
        print(f"[+] BOM CSV exported:           {bom_path}")

        drill_path = os.path.join(out_dir, f"{base_name}_drills.csv")
        reporter.to_csv_drills(drill_path)
        print(f"[+] Drill table CSV exported:   {drill_path}")

        nets_path = os.path.join(out_dir, f"{base_name}_nets.csv")
        reporter.to_csv_nets(nets_path)
        print(f"[+] Nets CSV exported:          {nets_path}")

    if args.export_all or args.export_svg:
        svg_dir = os.path.join(out_dir, "svgs")
        svgs = reporter.export_svgs(svg_dir, include_all_layers=args.all_layers)
        print(f"[+] {len(svgs)} SVG files exported to:  {svg_dir}")

    print("\n[Done] All requested reports and exports completed successfully.")


def main():
    parser = argparse.ArgumentParser(
        prog="pcbsight",
        description="PCBsight: Altium Designer .PcbDoc Comprehensive Inspection & Analysis Tool",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: inspect
    p_inspect = subparsers.add_parser("inspect", help="Quick terminal summary of PCB parameters")
    p_inspect.add_argument("file", help="Path to .PcbDoc file")

    # Subcommand: rules
    p_rules = subparsers.add_parser("rules", help="Audit design rules, clearance, and DRC violations")
    p_rules.add_argument("file", help="Path to .PcbDoc file")

    # Subcommand: layers
    p_layers = subparsers.add_parser("layers", help="Display full layer stackup table")
    p_layers.add_argument("file", help="Path to .PcbDoc file")

    # Subcommand: report
    p_report = subparsers.add_parser("report", help="Generate comprehensive Markdown, JSON, CSV reports")
    p_report.add_argument("file", help="Path to .PcbDoc file")
    p_report.add_argument("--out", "-o", help="Output directory for generated files")
    p_report.add_argument("--export-all", action="store_true", help="Export Markdown, JSON, all CSVs, and SVGs")
    p_report.add_argument("--export-csv", action="store_true", help="Export BOM, Drill, and Net CSVs")
    p_report.add_argument("--export-svg", action="store_true", help="Export SVG layer renderings")
    p_report.add_argument("--all-layers", action="store_true", help="Render SVGs for all layers including mechanical")

    args = parser.parse_args()

    if args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "rules":
        cmd_rules(args)
    elif args.command == "layers":
        cmd_layers(args)
    elif args.command == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
