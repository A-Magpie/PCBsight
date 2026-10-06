"""
pcb_reporter.py - Multi-format Exporter and Reporter for Altium .PcbDoc files.
Generates comprehensive Markdown reports, structured JSON, CSV tables, and SVGs.
Part of the PCBsight skill suite.
"""

from __future__ import annotations
import csv
import json
import os
from typing import Dict, Any, List

import altium_monkey as am
from pcb_model import PcbAnalysisResult
from pcb_analyzer import PCBAnalyzer


class PcbReporter:
    """Generates Markdown, JSON, CSV, and SVG outputs for PCB designs."""

    def __init__(self, result: PcbAnalysisResult):
        self.res = result
        self.analyzer = PCBAnalyzer(result)

    def to_markdown(self) -> str:
        """Generates a comprehensive GitHub-flavored Markdown inspection report."""
        summary = self.analyzer.get_summary()
        dim = self.res.dimensions
        stats = self.res.statistics
        rules_sum = self.analyzer.get_clearance_and_rules_summary()
        viol_sum = self.analyzer.get_violations_summary()
        routing_sum = self.analyzer.get_routing_summary()
        silk_sum = self.analyzer.get_silkscreen_summary()
        bom = self.analyzer.get_bom_summary()

        lines: List[str] = []
        lines.append(f"# PCBsight Inspection Report: `{self.res.filename}`\n")

        # 1. Executive Summary Table
        lines.append("## 1. Board Overview & Physical Dimensions\n")
        lines.append("| Metric | Value (Metric) | Value (Imperial) |")
        lines.append("| :--- | :--- | :--- |")
        lines.append(f"| **Board Dimensions (W x H)** | **{dim.width_mm:.2f} x {dim.height_mm:.2f} mm** | **{dim.width_mils:.1f} x {dim.height_mils:.1f} mils** |")
        lines.append(f"| **Board Area** | {dim.area_sq_mm:.2f} mm² | {dim.area_sq_in:.2f} sq in |")
        lines.append(f"| **Bounding Box** | [{dim.min_x_mm:.2f}, {dim.min_y_mm:.2f}] to [{dim.max_x_mm:.2f}, {dim.max_y_mm:.2f}] mm | [{dim.min_x_mils:.1f}, {dim.min_y_mils:.1f}] to [{dim.max_x_mils:.1f}, {dim.max_y_mils:.1f}] mils |")
        lines.append(f"| **Outline Vertices / Cutouts** | {dim.vertex_count} vertices | {dim.cutout_count} cutouts/slots |")
        lines.append(f"| **Copper Layers Count** | **{self.res.copper_layer_count} layers** | - |")
        lines.append(f"| **Total Components** | **{stats.total_components}** (SMD: {stats.smd_components}, THT: {stats.tht_components}) | Top: {stats.top_components}, Bottom: {stats.bottom_components} |")
        lines.append(f"| **Pads & Vias** | {stats.total_pads} Pads (SMD: {stats.smd_pads}, THT: {stats.tht_pads}) | {stats.total_vias} Vias |")
        lines.append(f"| **Total Copper Trace Length** | **{stats.copper_track_length_mm:.2f} mm** ({stats.copper_track_length_mm/1000:.2f} m) | {stats.copper_track_length_mm*39.37:.1f} in |")
        lines.append(f"| **Total Nets / Routed Nets** | {stats.total_nets} nets / {stats.routed_nets} routed | - |")
        lines.append(f"| **Design Rules / Violations** | {stats.total_rules} active rules | **{stats.total_violations} recorded DRC violations** |")
        lines.append(f"| **Minimum Track Width** | {stats.min_track_width_mm:.3f} mm | {stats.min_track_width_mm*39.37:.2f} mils |")
        lines.append(f"| **Minimum Clearance Rule** | {stats.min_clearance_rule_mm:.3f} mm | {stats.min_clearance_rule_mm*39.37:.2f} mils |")
        lines.append(f"| **Minimum Drill Hole** | {stats.min_hole_size_mm:.3f} mm | {stats.min_hole_size_mm*39.37:.2f} mils |")
        lines.append("")

        # 2. Layer Stackup Table
        lines.append("## 2. Layer Stackup\n")
        lines.append("| Index | Layer Name | Layer ID | Function / Type | In Use | Thickness | Material |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for lyr in self.res.layers:
            used_icon = "Yes" if lyr.is_used else "No"
            thk = f"{lyr.thickness_mm:.3f} mm" if lyr.thickness_mm else "-"
            mat = lyr.material or "-"
            lines.append(f"| {lyr.index} | **{lyr.name}** | {lyr.layer_id} | {lyr.kind} | {used_icon} | {thk} | {mat} |")
        lines.append("")

        # 3. Routing & Copper Summary
        lines.append("## 3. Tracks & Copper Routing\n")
        lines.append(f"- **Total Tracks:** {len(self.res.tracks):,} segments")
        lines.append(f"- **Total Track Length:** {stats.total_track_length_mm:.2f} mm (Copper: {stats.copper_track_length_mm:.2f} mm)")
        lines.append("")
        lines.append("### Tracks by Layer")
        lines.append("| Layer | Segment Count | Routed Length (mm) |")
        lines.append("| :--- | :--- | :--- |")
        for lyr_name, count in routing_sum["tracks_by_layer"].items():
            len_val = routing_sum["length_by_layer_mm"].get(lyr_name, 0.0)
            lines.append(f"| **{lyr_name}** | {count} | {len_val:.2f} mm |")
        lines.append("")

        if routing_sum["top_routed_nets"]:
            lines.append("### Top Routed Nets by Length")
            lines.append("| Net Name | Total Length (mm) | Tracks | Vias | Pads | Diff Pair |")
            lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for tn in routing_sum["top_routed_nets"][:10]:
                dp_str = "Yes" if tn["is_diff_pair"] else "-"
                lines.append(f"| `{tn['name']}` | {tn['length_mm']:.2f} mm | {tn['tracks']} | {tn['vias']} | {tn['pads']} | {dp_str} |")
            lines.append("")

        # 4. Silkscreen Markings (مارکاژ)
        lines.append("## 4. Silkscreen & Markings (مارکاژ)\n")
        lines.append(f"- **Total Silkscreen Elements:** {silk_sum['total_silkscreen_items']}")
        lines.append(f"- **Layers:** {', '.join(f'{k}: {v}' for k, v in silk_sum['by_layer'].items())}")
        lines.append(f"- **Fonts Detected:** {', '.join(f'{k} ({v})' for k, v in silk_sum['fonts'].items())}")
        lines.append("")
        if silk_sum["standalone_texts"]:
            lines.append("### Extracted Board Markings & Labels")
            lines.append("| Text / Label | Layer | Height | Font | Coordinates (X, Y) | Rotation |")
            lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for st in silk_sum["standalone_texts"]:
                lines.append(f"| **`{st['text']}`** | {st['layer']} | {st['height_mm']:.2f} mm | {st['font']} | ({st['x_mm']:.1f}, {st['y_mm']:.1f}) | {st['rotation']}° |")
            lines.append("")

        # 5. Design Rules & Clearance
        lines.append("## 5. Design Rules & Clearance (Rule ها و کلیرنس)\n")
        lines.append(f"- **Total Design Rules:** {rules_sum['total_rules']}")
        lines.append("")
        lines.append("### Clearance Rules")
        lines.append("| Rule Name | Priority | Gap (mm / mils) | Generic Clearance | Net Scope | Layer Scope | Scope Expression |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for cr in rules_sum["clearance_rules"]:
            lines.append(
                f"| **{cr['name']}** | {cr['priority']} | **{cr['gap_mm']:.3f} mm ({cr['gap_mils']:.1f} mil)** | {cr['generic_clearance_mm']:.3f} mm | {cr['net_scope']} | {cr['layer_scope']} | `{cr['scope1']}` / `{cr['scope2']}` |"
            )
        lines.append("")

        lines.append("### Other Key Design Rules")
        lines.append("| Rule Name | Kind | Enabled | Priority | Scope | Key Constraints |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for r in rules_sum["all_rules"]:
            if "clearance" in r["kind"].lower():
                continue
            en_str = "Yes" if r["enabled"] else "No"
            # Summarize main constraints
            con_summary = ", ".join(f"{k}={v}" for k, v in list(r["constraints"].items())[:3])
            lines.append(f"| **{r['name']}** | {r['kind']} | {en_str} | {r['priority']} | `{r['scope1']}` | {con_summary} |")
        lines.append("")

        # 6. DRC Violations Table
        lines.append("## 6. DRC Violations Audit\n")
        if not self.res.violations:
            lines.append("> [!NOTE]\n> No recorded DRC violations in this board file.\n")
        else:
            lines.append(f"- **Total Recorded Violations:** **{len(self.res.violations)}**")
            lines.append(f"- **Breakdown:** {', '.join(f'{k}: {v}' for k, v in viol_sum['breakdown'].items())}\n")
            lines.append("| Violation Kind | Associated Rule | Colliding Objects | Location (mm) | Description |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for v in viol_sum["details"][:25]:
                objs = f"{v['prim1']} <-> {v['prim2']}" if v["prim2"] else (v["prim1"] or "-")
                loc = v["location_mm"] or "-"
                desc = v["description"] or "-"
                lines.append(f"| **{v['kind']}** | `{v['rule_name'] or '-'}` | {objs} | {loc} | {desc} |")
            if len(viol_sum["details"]) > 25:
                lines.append(f"| ... | *(and {len(viol_sum['details']) - 25} more violations)* | | | |")
            lines.append("")

        # 7. Drill Table
        lines.append("## 7. Drill & Hole Schedule\n")
        lines.append("| Tool # | Hole Diameter (mm) | Hole Diameter (mils) | Plating | Pads Count | Vias Count | Total Holes |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for idx, d in enumerate(self.res.drills, 1):
            pl_str = "Plated (PTH)" if d.is_plated else "Non-Plated (NPTH)"
            lines.append(f"| T{idx:02d} | **{d.hole_size_mm:.3f} mm** | **{d.hole_size_mils:.1f} mil** | {pl_str} | {d.pad_count} | {d.via_count} | **{d.total_count}** |")
        lines.append("")

        # 8. Bill of Materials (BOM)
        lines.append("## 8. Bill of Materials (BOM Summary)\n")
        lines.append("| Item | Qty | Footprint | Value | MPN / Part Number | Manufacturer | Designators |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for item in bom[:30]:
            lines.append(
                f"| {item['item_no']} | **{item['quantity']}** | `{item['footprint']}` | {item['value'] or '-'} | `{item['mpn'] or '-'}` | {item['manufacturer'] or '-'} | {item['designators_str']} |"
            )
        if len(bom) > 30:
            lines.append(f"| ... | | *(and {len(bom) - 30} more line items)* | | | | |")
        lines.append("")

        return "\n".join(lines)

    def to_json(self, filepath: str) -> None:
        """Serializes complete inspection data into a clean JSON file."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.res.to_dict(), f, indent=2)

    def to_csv_bom(self, filepath: str) -> None:
        """Exports Bill of Materials to CSV."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        bom = self.analyzer.get_bom_summary()
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Item", "Quantity", "Designators", "Footprint", "Value", "MPN", "Manufacturer", "Description", "Layer", "SMD"])
            for b in bom:
                writer.writerow([
                    b["item_no"],
                    b["quantity"],
                    b["designators_str"],
                    b["footprint"],
                    b["value"],
                    b["mpn"],
                    b["manufacturer"],
                    b["description"],
                    b["layer"],
                    "Yes" if b["is_smd"] else "No",
                ])

    def to_csv_drills(self, filepath: str) -> None:
        """Exports Drill tool schedule to CSV."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Tool", "Diameter_mm", "Diameter_mils", "Plating", "Pad_Count", "Via_Count", "Total_Count"])
            for idx, d in enumerate(self.res.drills, 1):
                writer.writerow([
                    f"T{idx:02d}",
                    f"{d.hole_size_mm:.3f}",
                    f"{d.hole_size_mils:.1f}",
                    "PTH" if d.is_plated else "NPTH",
                    d.pad_count,
                    d.via_count,
                    d.total_count,
                ])

    def to_csv_nets(self, filepath: str) -> None:
        """Exports Netlist and trace lengths to CSV."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Net_Name", "Total_Length_mm", "Total_Length_mils", "Track_Count", "Via_Count", "Pad_Count", "Diff_Pair"])
            for n in sorted(self.res.nets, key=lambda x: x.total_track_length_mm, reverse=True):
                writer.writerow([
                    n.name,
                    f"{n.total_track_length_mm:.3f}",
                    f"{n.total_track_length_mils:.1f}",
                    n.track_count,
                    n.via_count,
                    n.pad_count,
                    "Yes" if n.is_diff_pair else "No",
                ])

    def export_svgs(self, output_dir: str, include_all_layers: bool = False) -> List[str]:
        """Renders and exports SVG vector graphics for board layers."""
        os.makedirs(output_dir, exist_ok=True)
        exported_files: List[str] = []

        # Load doc with altium_monkey for rendering
        doc = am.altium_pcbdoc.AltiumPcbDoc.from_file(self.res.filepath)

        # 1. Board outline SVG
        try:
            outline_svg = doc.to_board_outline_svg()
            outline_path = os.path.join(output_dir, "board_outline.svg")
            with open(outline_path, "w", encoding="utf-8") as f:
                f.write(outline_svg)
            exported_files.append(outline_path)
        except Exception as e:
            print(f"Warning: Failed to render board outline SVG: {e}")

        # 2. Key layer SVGs
        try:
            layer_svgs = doc.to_layer_svgs()
            key_layers = {"TOP", "BOTTOM", "TOPOVERLAY", "BOTTOMOVERLAY", "DRILLS"}
            for lyr_name, svg_content in layer_svgs.items():
                if include_all_layers or lyr_name in key_layers:
                    file_name = f"layer_{lyr_name.lower()}.svg"
                    layer_path = os.path.join(output_dir, file_name)
                    with open(layer_path, "w", encoding="utf-8") as f:
                        f.write(svg_content)
                    exported_files.append(layer_path)
        except Exception as e:
            print(f"Warning: Failed to render layer SVGs: {e}")

        return exported_files
