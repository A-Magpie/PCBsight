"""
pcb_analyzer.py - Deep Analytics Engine for Altium .PcbDoc files.
Extracts insights, summarizes routing, BOM, clearance rules, and violations.
Part of the PCBsight skill suite.
"""

from __future__ import annotations
from collections import Counter
from typing import Dict, List, Any, Optional

from pcb_model import (
    PcbAnalysisResult,
    ClearanceRuleEntity,
    DesignRuleEntity,
    DrcViolationEntity,
    ComponentEntity,
    TrackEntity,
    PadEntity,
    ViaEntity,
    SilkscreenEntity,
    NetEntity,
    DrillToolBin,
)


class PCBAnalyzer:
    """Provides high-level analysis and queries over parsed PCB data."""

    def __init__(self, result: PcbAnalysisResult):
        self.res = result

    def get_summary(self) -> Dict[str, Any]:
        """Returns high-level executive summary of the PCB."""
        dim = self.res.dimensions
        stats = self.res.statistics
        return {
            "filename": self.res.filename,
            "board_size_mm": f"{dim.width_mm:.2f} x {dim.height_mm:.2f} mm",
            "board_size_mils": f"{dim.width_mils:.1f} x {dim.height_mils:.1f} mils",
            "board_area_sq_mm": dim.area_sq_mm,
            "board_area_sq_in": dim.area_sq_in,
            "cutouts_count": dim.cutout_count,
            "copper_layers": self.res.copper_layer_count,
            "total_layers": len(self.res.layers),
            "total_components": stats.total_components,
            "smd_components": stats.smd_components,
            "tht_components": stats.tht_components,
            "top_components": stats.top_components,
            "bottom_components": stats.bottom_components,
            "total_tracks": stats.total_tracks,
            "total_track_length_mm": stats.total_track_length_mm,
            "copper_track_length_mm": stats.copper_track_length_mm,
            "total_pads": stats.total_pads,
            "total_vias": stats.total_vias,
            "total_nets": stats.total_nets,
            "routed_nets": stats.routed_nets,
            "total_rules": stats.total_rules,
            "total_violations": stats.total_violations,
            "min_trace_width_mm": stats.min_track_width_mm,
            "min_clearance_mm": stats.min_clearance_rule_mm,
            "min_hole_mm": stats.min_hole_size_mm,
        }

    def get_clearance_and_rules_summary(self) -> Dict[str, Any]:
        """Comprehensive analysis of clearance and design constraints."""
        cl_rules = []
        for cr in self.res.clearance_rules:
            cl_rules.append({
                "name": cr.name,
                "enabled": cr.enabled,
                "priority": cr.priority,
                "scope1": cr.scope1,
                "scope2": cr.scope2 or "All",
                "net_scope": cr.net_scope or "DifferentNets",
                "layer_scope": cr.layer_scope or "SameLayer",
                "gap_mm": cr.gap_mm,
                "gap_mils": cr.gap_mils,
                "generic_clearance_mm": cr.generic_clearance_mm,
                "generic_clearance_mils": cr.generic_clearance_mils,
                "matrix": cr.matrix_clearances,
            })

        rule_types_count = Counter(r.rule_kind for r in self.res.rules)
        all_rules_list = []
        for r in self.res.rules:
            all_rules_list.append({
                "name": r.name,
                "kind": r.rule_kind,
                "enabled": r.enabled,
                "priority": r.priority,
                "scope1": r.scope1,
                "scope2": r.scope2,
                "constraints": r.raw_constraints,
            })

        return {
            "total_rules": len(self.res.rules),
            "rule_types_breakdown": dict(rule_types_count.most_common()),
            "clearance_rules": cl_rules,
            "all_rules": all_rules_list,
        }

    def get_violations_summary(self) -> Dict[str, Any]:
        """Summarizes DRC violations, categorizing by kind and severity."""
        by_kind = Counter(v.violation_kind for v in self.res.violations)
        items = []
        for v in self.res.violations:
            items.append({
                "kind": v.violation_kind,
                "rule_name": v.rule_name,
                "prim1": f"{v.prim1_type} #{v.prim1_index}" if v.prim1_type else None,
                "prim2": f"{v.prim2_type} #{v.prim2_index}" if v.prim2_type else None,
                "description": v.description,
                "location_mm": (
                    f"({v.location1_x_mm:.2f}, {v.location1_y_mm:.2f})"
                    if v.location1_x_mm is not None
                    else None
                ),
            })
        return {
            "total_violations": len(self.res.violations),
            "breakdown": dict(by_kind),
            "details": items,
        }

    def get_routing_summary(self) -> Dict[str, Any]:
        """Analysis of copper traces, layers, and nets."""
        tracks_by_layer = Counter(t.layer_name for t in self.res.tracks)
        length_by_layer = {}
        for t in self.res.tracks:
            length_by_layer[t.layer_name] = round(
                length_by_layer.get(t.layer_name, 0.0) + t.length_mm, 2
            )

        # Trace widths
        widths_counter = Counter(round(t.width_mm, 3) for t in self.res.tracks if t.width_mm > 0)

        # Longest nets
        sorted_nets = sorted(self.res.nets, key=lambda n: n.total_track_length_mm, reverse=True)
        top_nets = [
            {
                "name": n.name,
                "length_mm": n.total_track_length_mm,
                "tracks": n.track_count,
                "vias": n.via_count,
                "pads": n.pad_count,
                "is_diff_pair": n.is_diff_pair,
            }
            for n in sorted_nets[:15]
            if n.total_track_length_mm > 0
        ]

        # Differential pairs
        diff_pairs = [
            {"name": n.name, "diff_pair_group": n.diff_pair_name, "length_mm": n.total_track_length_mm}
            for n in self.res.nets
            if n.is_diff_pair
        ]

        return {
            "total_tracks": len(self.res.tracks),
            "total_length_mm": self.res.statistics.total_track_length_mm,
            "tracks_by_layer": dict(tracks_by_layer.most_common()),
            "length_by_layer_mm": length_by_layer,
            "common_widths_mm": dict(widths_counter.most_common(5)),
            "top_routed_nets": top_nets,
            "differential_pairs": diff_pairs,
        }

    def get_silkscreen_summary(self) -> Dict[str, Any]:
        """Extracts and categorizes silkscreen / markings (مارکاژ)."""
        texts = self.res.silkscreen_items
        by_layer = Counter(t.layer_name for t in texts)
        fonts = Counter(t.font_name for t in texts)
        types = Counter(t.font_type for t in texts)

        # Standalone board texts (not component designators/comments)
        standalone = [
            {
                "text": t.text,
                "layer": t.layer_name,
                "height_mm": t.height_mm,
                "font": t.font_name,
                "x_mm": t.x_mm,
                "y_mm": t.y_mm,
                "rotation": t.rotation,
            }
            for t in texts
            if not t.is_designator and not t.is_comment and t.text.strip()
        ]

        return {
            "total_silkscreen_items": len(texts),
            "by_layer": dict(by_layer),
            "fonts": dict(fonts),
            "font_types": dict(types),
            "standalone_texts_count": len(standalone),
            "standalone_texts": standalone,
        }

    def get_bom_summary(self) -> List[Dict[str, Any]]:
        """Groups components into an aggregated Bill of Materials (BOM)."""
        bom_groups: Dict[tuple, Dict[str, Any]] = {}
        for c in self.res.components:
            mpn = c.parameters.get("Manufacturer_Part_Number") or c.parameters.get("MPN") or c.parameters.get("Part Number") or ""
            val = c.parameters.get("Value") or c.comment or ""
            mfg = c.parameters.get("Manufacturer") or c.parameters.get("Manufacturer_Name") or ""
            desc = c.description or c.parameters.get("Description") or ""
            key = (c.footprint, val, mpn, mfg)

            if key not in bom_groups:
                bom_groups[key] = {
                    "footprint": c.footprint,
                    "value": val,
                    "mpn": mpn,
                    "manufacturer": mfg,
                    "description": desc,
                    "quantity": 0,
                    "designators": [],
                    "layer": c.layer,
                    "is_smd": c.is_smd,
                }
            bom_groups[key]["quantity"] += 1
            bom_groups[key]["designators"].append(c.designator)

        # Sort by quantity descending
        bom_list = sorted(bom_groups.values(), key=lambda g: g["quantity"], reverse=True)
        for idx, item in enumerate(bom_list, 1):
            item["item_no"] = idx
            item["designators_str"] = ", ".join(sorted(item["designators"]))
        return bom_list
