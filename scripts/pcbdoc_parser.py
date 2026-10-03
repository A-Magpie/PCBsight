"""
pcbdoc_parser.py - Unified High-Fidelity Altium .PcbDoc Parser.
Combines altium_monkey object model with direct OLE stream extraction.
Part of the PCBsight skill suite.
"""

from __future__ import annotations
import math
import os
from typing import List, Dict, Any, Optional, Tuple, Set

import olefile
import altium_monkey as am
from altium_monkey.altium_pcb_enums import PcbLayer

from pcb_model import (
    dxt_to_mm,
    dxt_to_mil,
    mil_to_mm,
    MM_TO_MIL,
    parse_altium_dim_string,
    BoardOutlineVertex,
    BoardDimensions,
    LayerInfo,
    TrackEntity,
    ArcEntity,
    PadEntity,
    ViaEntity,
    SilkscreenEntity,
    ComponentEntity,
    NetEntity,
    DesignRuleEntity,
    ClearanceRuleEntity,
    DrcViolationEntity,
    DrillToolBin,
    PcbStatistics,
    PcbAnalysisResult,
)


def _safe_layer_name(layer_id: int) -> str:
    """Safely convert numeric layer identifier to human readable name."""
    try:
        return PcbLayer(layer_id).name
    except (ValueError, TypeError):
        # Known custom or extended mappings
        if layer_id == 74:
            return "MULTI_LAYER"
        return f"LAYER_{layer_id}"


def _parse_pipe_records(raw_bytes: bytes) -> List[Dict[str, str]]:
    """Parse standard Altium 4-byte length prefixed pipe-separated key-value stream."""
    records = []
    offset = 0
    total_len = len(raw_bytes)
    while offset + 4 <= total_len:
        rec_len = int.from_bytes(raw_bytes[offset : offset + 4], "little")
        offset += 4
        if offset + rec_len > total_len:
            break
        chunk = raw_bytes[offset : offset + rec_len]
        offset += rec_len
        try:
            text = chunk.decode("latin1").strip("\x00 \r\n|")
            items = text.split("|")
            record_dict = {}
            for item in items:
                if "=" in item:
                    k, v = item.split("=", 1)
                    record_dict[k.strip()] = v.strip("\x00 ")
            if record_dict:
                records.append(record_dict)
        except Exception:
            continue
    return records


class PCBDocParser:
    """Comprehensive parser for Altium Designer .PcbDoc files."""

    def __init__(self, filepath: str):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"PCB file not found: {filepath}")
        self.filepath = os.path.abspath(filepath)
        self.filename = os.path.basename(filepath)
        self.file_size = os.path.getsize(filepath)

    def parse(self) -> PcbAnalysisResult:
        """Parse the complete .PcbDoc file and return structured PcbAnalysisResult."""
        doc = am.altium_pcbdoc.AltiumPcbDoc.from_file(self.filepath)
        ole = olefile.OleFileIO(self.filepath)

        # 1. Parse Layers & Stackup
        layers = self._parse_layers(doc)
        layer_map = {l.layer_id: l.name for l in layers}
        copper_layers_count = sum(1 for l in layers if l.is_copper)

        # 2. Parse Board Outline & Dimensions
        dimensions = self._parse_board_dimensions(doc)

        # 3. Build fast lookup maps for Components & Nets
        comp_map: Dict[int, str] = {}
        for idx, c in enumerate(doc.components):
            comp_map[idx] = str(getattr(c, "designator", f"CMP_{idx}"))

        net_map: Dict[int, str] = {}
        for idx, n in enumerate(doc.nets):
            net_map[idx] = str(getattr(n, "name", f"NET_{idx}"))

        # 4. Parse Copper and Technical Primitives
        tracks = self._parse_tracks(doc, net_map, comp_map, layer_map)
        arcs = self._parse_arcs(doc, net_map, comp_map, layer_map)
        pads = self._parse_pads(doc, net_map, comp_map, layer_map)
        vias = self._parse_vias(doc, net_map, layer_map)
        silkscreen_items = self._parse_silkscreen(doc, comp_map, layer_map)

        # 5. Parse Components with Parameters & SMD vs THT
        components = self._parse_components(doc, pads)

        # 6. Parse Nets and calculate routing metrics
        nets = self._parse_nets(doc, tracks, vias, pads)

        # 7. Parse Design Rules & Clearance
        rules, clearance_rules = self._parse_rules(doc)

        # 8. Parse DRC Violations from OLE streams
        violations = self._parse_violations(ole, rules)

        # 9. Compute Drill Tool Table
        drills = self._compute_drills(pads, vias)

        # 10. Compute Summary Statistics
        stats = self._compute_statistics(
            components, tracks, pads, vias, nets, rules, violations, clearance_rules, drills
        )

        return PcbAnalysisResult(
            filename=self.filename,
            filepath=self.filepath,
            file_size_bytes=self.file_size,
            dimensions=dimensions,
            layers=layers,
            copper_layer_count=copper_layers_count,
            tracks=tracks,
            arcs=arcs,
            pads=pads,
            vias=vias,
            silkscreen_items=silkscreen_items,
            components=components,
            nets=nets,
            rules=rules,
            clearance_rules=clearance_rules,
            violations=violations,
            drills=drills,
            statistics=stats,
        )

    def _parse_layers(self, doc: am.altium_pcbdoc.AltiumPcbDoc) -> List[LayerInfo]:
        layers: List[LayerInfo] = []
        raw = getattr(doc.board, "raw_record", {})
        
        # Check V9 stack layers first
        i = 0
        found_stack = False
        while True:
            name_key = f"V9_STACK_LAYER{i}_NAME"
            if name_key not in raw:
                break
            found_stack = True
            name = raw.get(name_key, f"Layer_{i}")
            lid_str = raw.get(f"V9_STACK_LAYER{i}_LAYERID", str(i))
            try:
                lid = int(lid_str)
            except ValueError:
                lid = i
            
            used_str = raw.get(f"V9_STACK_LAYER{i}_USEDBYPRIMS", "FALSE")
            is_used = used_str.upper() == "TRUE"
            
            # Determine layer kind
            name_lower = name.lower()
            if "top layer" in name_lower or "bottom layer" in name_lower or "mid" in name_lower or "copper" in name_lower:
                kind = "Copper"
                is_copper = True
                is_tech = False
            elif "dielectric" in name_lower or "core" in name_lower or "prepreg" in name_lower:
                kind = "Dielectric"
                is_copper = False
                is_tech = False
            elif "solder" in name_lower:
                kind = "SolderMask"
                is_copper = False
                is_tech = True
            elif "overlay" in name_lower or "silk" in name_lower:
                kind = "Overlay"
                is_copper = False
                is_tech = True
            elif "paste" in name_lower:
                kind = "Paste"
                is_copper = False
                is_tech = True
            else:
                kind = "Mechanical"
                is_copper = False
                is_tech = False

            thick_str = raw.get(f"V9_STACK_LAYER{i}_COPPERTHICKNESS") or raw.get(f"V9_STACK_LAYER{i}_DIELTHICKNESS")
            thick_mils, thick_mm = parse_altium_dim_string(thick_str) if thick_str else (None, None)
            mat = raw.get(f"V9_STACK_LAYER{i}_MATERIAL")

            layers.append(
                LayerInfo(
                    index=i,
                    name=name,
                    layer_id=lid,
                    kind=kind,
                    is_copper=is_copper,
                    is_technical=is_tech,
                    is_mechanical=(kind == "Mechanical"),
                    is_used=is_used,
                    thickness_mm=thick_mm,
                    thickness_mils=thick_mils,
                    material=mat,
                )
            )
            i += 1

        # Fallback if no V9 stack found: populate standard 2-layer default
        if not found_stack:
            std_layers = [
                (0, "Top Overlay", 33, "Overlay", False, True),
                (1, "Top Solder", 37, "SolderMask", False, True),
                (2, "Top Layer", 1, "Copper", True, False),
                (3, "Bottom Layer", 32, "Copper", True, False),
                (4, "Bottom Solder", 38, "SolderMask", False, True),
                (5, "Bottom Overlay", 34, "Overlay", False, True),
            ]
            for idx, name, lid, kind, is_cu, is_tech in std_layers:
                layers.append(
                    LayerInfo(
                        index=idx,
                        name=name,
                        layer_id=lid,
                        kind=kind,
                        is_copper=is_cu,
                        is_technical=is_tech,
                        is_mechanical=False,
                        is_used=True,
                    )
                )

        # Check mechanical layers, keepouts, and multilayers
        existing_lids = {l.layer_id for l in layers}
        used_layer_ids = set()
        for t in getattr(doc, "tracks", []): used_layer_ids.add(int(t.layer))
        for p in getattr(doc, "pads", []): used_layer_ids.add(int(p.layer))
        for v in getattr(doc, "vias", []): used_layer_ids.add(int(v.layer))
        for txt in getattr(doc, "texts", []): used_layer_ids.add(int(txt.layer))
        for a in getattr(doc, "arcs", []): used_layer_ids.add(int(a.layer))

        # Check all possible layer IDs (1..100) for mechanical enablement or primitive usage
        for lid in range(1, 100):
            mech_en = raw.get(f"LAYER{lid}MECHENABLED", "FALSE").upper() == "TRUE"
            if mech_en or (lid in used_layer_ids and lid not in existing_lids):
                if lid in existing_lids:
                    for ex in layers:
                        if ex.layer_id == lid and lid in used_layer_ids:
                            ex.is_used = True
                    continue

                custom_name = (
                    raw.get(f"LAYER{lid}NAME")
                    or raw.get(f"V9_CACHE_LAYER{lid}_NAME")
                    or _safe_layer_name(lid)
                )

                if any(ex.name == custom_name for ex in layers):
                    for ex in layers:
                        if ex.name == custom_name and lid in used_layer_ids:
                            ex.is_used = True
                    continue

                if 57 <= lid <= 88:
                    kind = "Mechanical"
                    is_mech = True
                elif lid == 56:
                    kind = "KeepOut"
                    is_mech = True
                elif lid == 74:
                    kind = "MultiLayer"
                    is_mech = False
                else:
                    kind = "Mechanical" if mech_en else "Other"
                    is_mech = mech_en

                layers.append(
                    LayerInfo(
                        index=len(layers),
                        name=custom_name,
                        layer_id=lid,
                        kind=kind,
                        is_copper=False,
                        is_technical=False,
                        is_mechanical=is_mech,
                        is_used=(lid in used_layer_ids),
                    )
                )
                existing_lids.add(lid)

        return layers

    def _parse_board_dimensions(self, doc: am.altium_pcbdoc.AltiumPcbDoc) -> BoardDimensions:
        vertices: List[BoardOutlineVertex] = []
        cutouts: List[List[BoardOutlineVertex]] = []

        outline = getattr(doc.board, "outline", None)
        if outline and hasattr(outline, "vertices") and outline.vertices:
            for v in outline.vertices:
                xm = float(getattr(v, "x_mils", 0.0))
                ym = float(getattr(v, "y_mils", 0.0))
                is_arc = bool(getattr(v, "is_arc", False))
                cx_mils = float(getattr(v, "center_x_mils", 0.0))
                cy_mils = float(getattr(v, "center_y_mils", 0.0))
                r_mils = float(getattr(v, "radius_mils", 0.0))
                s_ang = getattr(v, "start_angle_deg", None)
                e_ang = getattr(v, "end_angle_deg", None)

                vertices.append(
                    BoardOutlineVertex(
                        x_mm=mil_to_mm(xm),
                        y_mm=mil_to_mm(ym),
                        x_mils=xm,
                        y_mils=ym,
                        is_arc=is_arc,
                        center_x_mm=mil_to_mm(cx_mils),
                        center_y_mm=mil_to_mm(cy_mils),
                        radius_mm=mil_to_mm(r_mils),
                        start_angle_deg=s_ang,
                        end_angle_deg=e_ang,
                    )
                )

            if hasattr(outline, "cutouts") and outline.cutouts:
                for c_loop in outline.cutouts:
                    c_verts = []
                    for cv in c_loop:
                        cxm = float(getattr(cv, "x_mils", 0.0))
                        cym = float(getattr(cv, "y_mils", 0.0))
                        c_verts.append(
                            BoardOutlineVertex(
                                x_mm=mil_to_mm(cxm),
                                y_mm=mil_to_mm(cym),
                                x_mils=cxm,
                                y_mils=cym,
                                is_arc=bool(getattr(cv, "is_arc", False)),
                            )
                        )
                    cutouts.append(c_verts)

        # Compute Bounding Box
        if vertices:
            xs_mm = [v.x_mm for v in vertices]
            ys_mm = [v.y_mm for v in vertices]
            min_x_mm = min(xs_mm)
            max_x_mm = max(xs_mm)
            min_y_mm = min(ys_mm)
            max_y_mm = max(ys_mm)

            xs_mils = [v.x_mils for v in vertices]
            ys_mils = [v.y_mils for v in vertices]
            min_x_mils = min(xs_mils)
            max_x_mils = max(xs_mils)
            min_y_mils = min(ys_mils)
            max_y_mils = max(ys_mils)

            width_mm = round(max_x_mm - min_x_mm, 3)
            height_mm = round(max_y_mm - min_y_mm, 3)
            width_mils = round(max_x_mils - min_x_mils, 2)
            height_mils = round(max_y_mils - min_y_mils, 2)

            # Shoelace formula for polygon area
            def poly_area(pts: List[BoardOutlineVertex]) -> float:
                if len(pts) < 3:
                    return 0.0
                area = 0.0
                n = len(pts)
                for i in range(n):
                    j = (i + 1) % n
                    area += pts[i].x_mm * pts[j].y_mm
                    area -= pts[j].x_mm * pts[i].y_mm
                return abs(area) / 2.0

            outer_area = poly_area(vertices)
            cutout_area = sum(poly_area(c) for c in cutouts)
            net_area_mm2 = round(max(0.0, outer_area - cutout_area), 2)
            net_area_sq_in = round(net_area_mm2 / 645.16, 3)

        else:
            # Fallback to bbox of tracks & pads if no outline defined
            all_x_mm = []
            all_y_mm = []
            for t in getattr(doc, "tracks", []):
                all_x_mm.extend([dxt_to_mm(t.start_x), dxt_to_mm(t.end_x)])
                all_y_mm.extend([dxt_to_mm(t.start_y), dxt_to_mm(t.end_y)])
            for p in getattr(doc, "pads", []):
                all_x_mm.append(dxt_to_mm(p.x))
                all_y_mm.append(dxt_to_mm(p.y))

            if all_x_mm and all_y_mm:
                min_x_mm, max_x_mm = min(all_x_mm), max(all_x_mm)
                min_y_mm, max_y_mm = min(all_y_mm), max(all_y_mm)
                width_mm = round(max_x_mm - min_x_mm, 3)
                height_mm = round(max_y_mm - min_y_mm, 3)
                width_mils = round(width_mm * MM_TO_MIL, 2)
                height_mils = round(height_mm * MM_TO_MIL, 2)
                net_area_mm2 = round(width_mm * height_mm, 2)
                net_area_sq_in = round(net_area_mm2 / 645.16, 3)
            else:
                min_x_mm = max_x_mm = min_y_mm = max_y_mm = 0.0
                width_mm = height_mm = width_mils = height_mils = 0.0
                net_area_mm2 = net_area_sq_in = 0.0

        return BoardDimensions(
            width_mm=width_mm,
            height_mm=height_mm,
            width_mils=width_mils,
            height_mils=height_mils,
            area_sq_mm=net_area_mm2,
            area_sq_in=net_area_sq_in,
            min_x_mm=round(min_x_mm, 3),
            min_y_mm=round(min_y_mm, 3),
            max_x_mm=round(max_x_mm, 3),
            max_y_mm=round(max_y_mm, 3),
            min_x_mils=round(min_x_mils, 2) if 'min_x_mils' in locals() else round(min_x_mm * MM_TO_MIL, 2),
            min_y_mils=round(min_y_mils, 2) if 'min_y_mils' in locals() else round(min_y_mm * MM_TO_MIL, 2),
            max_x_mils=round(max_x_mils, 2) if 'max_x_mils' in locals() else round(max_x_mm * MM_TO_MIL, 2),
            max_y_mils=round(max_y_mils, 2) if 'max_y_mils' in locals() else round(max_y_mm * MM_TO_MIL, 2),
            vertex_count=len(vertices),
            cutout_count=len(cutouts),
            vertices=vertices,
            cutouts=cutouts,
        )

    def _parse_tracks(
        self,
        doc: am.altium_pcbdoc.AltiumPcbDoc,
        net_map: Dict[int, str],
        comp_map: Dict[int, str],
        layer_map: Dict[int, str],
    ) -> List[TrackEntity]:
        tracks: List[TrackEntity] = []
        for idx, t in enumerate(getattr(doc, "tracks", [])):
            sx_mm = dxt_to_mm(t.start_x)
            sy_mm = dxt_to_mm(t.start_y)
            ex_mm = dxt_to_mm(t.end_x)
            ey_mm = dxt_to_mm(t.end_y)
            w_mm = dxt_to_mm(t.width)
            w_mils = dxt_to_mil(t.width)

            # Euclidean length
            dx = ex_mm - sx_mm
            dy = ey_mm - sy_mm
            len_mm = round(math.sqrt(dx * dx + dy * dy), 4)
            len_mils = round(len_mm * MM_TO_MIL, 3)

            lnum = int(t.layer)
            lname = layer_map.get(lnum, _safe_layer_name(lnum))
            net_name = net_map.get(t.net_index) if t.net_index is not None else None
            comp_des = comp_map.get(t.component_index) if t.component_index is not None else None

            tracks.append(
                TrackEntity(
                    index=idx,
                    layer_num=lnum,
                    layer_name=lname,
                    start_x_mm=sx_mm,
                    start_y_mm=sy_mm,
                    end_x_mm=ex_mm,
                    end_y_mm=ey_mm,
                    width_mm=w_mm,
                    width_mils=w_mils,
                    length_mm=len_mm,
                    length_mils=len_mils,
                    net_index=t.net_index,
                    net_name=net_name,
                    component_index=t.component_index,
                    component_designator=comp_des,
                    is_locked=bool(getattr(t, "is_locked", False)),
                    is_keepout=bool(getattr(t, "is_keepout", False)),
                    is_polygon_outline=bool(getattr(t, "is_polygon_outline", False)),
                )
            )
        return tracks

    def _parse_arcs(
        self,
        doc: am.altium_pcbdoc.AltiumPcbDoc,
        net_map: Dict[int, str],
        comp_map: Dict[int, str],
        layer_map: Dict[int, str],
    ) -> List[ArcEntity]:
        arcs: List[ArcEntity] = []
        for idx, a in enumerate(getattr(doc, "arcs", [])):
            cx_mm = dxt_to_mm(getattr(a, "center_x", getattr(a, "x", 0)))
            cy_mm = dxt_to_mm(getattr(a, "center_y", getattr(a, "y", 0)))
            r_mm = dxt_to_mm(getattr(a, "radius", 0))
            r_mils = dxt_to_mil(getattr(a, "radius", 0))
            w_mm = dxt_to_mm(getattr(a, "width", 0))
            w_mils = dxt_to_mil(getattr(a, "width", 0))
            s_ang = float(getattr(a, "start_angle", 0.0))
            e_ang = float(getattr(a, "end_angle", 360.0))

            lnum = int(a.layer)
            lname = layer_map.get(lnum, _safe_layer_name(lnum))
            net_name = net_map.get(a.net_index) if getattr(a, "net_index", None) is not None else None
            comp_des = (
                comp_map.get(a.component_index) if getattr(a, "component_index", None) is not None else None
            )

            arcs.append(
                ArcEntity(
                    index=idx,
                    layer_num=lnum,
                    layer_name=lname,
                    center_x_mm=cx_mm,
                    center_y_mm=cy_mm,
                    radius_mm=r_mm,
                    radius_mils=r_mils,
                    start_angle_deg=s_ang,
                    end_angle_deg=e_ang,
                    width_mm=w_mm,
                    width_mils=w_mils,
                    net_index=getattr(a, "net_index", None),
                    net_name=net_name,
                    component_index=getattr(a, "component_index", None),
                    component_designator=comp_des,
                )
            )
        return arcs

    def _parse_pads(
        self,
        doc: am.altium_pcbdoc.AltiumPcbDoc,
        net_map: Dict[int, str],
        comp_map: Dict[int, str],
        layer_map: Dict[int, str],
    ) -> List[PadEntity]:
        pads: List[PadEntity] = []
        shape_names = {
            1: "Round",
            2: "Rectangular",
            3: "Octagonal",
            7: "RoundedRectangle",
        }
        hole_shape_names = {0: "Round", 1: "Square", 2: "Slot"}

        for idx, p in enumerate(getattr(doc, "pads", [])):
            x_mm = dxt_to_mm(p.x)
            y_mm = dxt_to_mm(p.y)
            w_mm = dxt_to_mm(p.width)
            h_mm = dxt_to_mm(p.height)
            hole_mm = dxt_to_mm(p.hole_size)
            hole_mils = dxt_to_mil(p.hole_size)
            is_smd = hole_mm <= 0.0001
            is_plated = bool(getattr(p, "is_plated", True))

            raw_shape = getattr(p, "shape", 1)
            shape_str = shape_names.get(raw_shape, f"Shape_{raw_shape}")
            raw_hole_shape = getattr(p, "hole_shape", 0)
            hole_shape_str = hole_shape_names.get(raw_hole_shape, "Round")

            lnum = int(p.layer)
            lname = layer_map.get(lnum, _safe_layer_name(lnum))
            net_name = net_map.get(p.net_index) if p.net_index is not None else None
            comp_des = comp_map.get(p.component_index) if p.component_index is not None else None

            pads.append(
                PadEntity(
                    index=idx,
                    designator=str(getattr(p, "designator", "")),
                    x_mm=x_mm,
                    y_mm=y_mm,
                    width_mm=w_mm,
                    height_mm=h_mm,
                    shape=shape_str,
                    hole_size_mm=hole_mm,
                    hole_size_mils=hole_mils,
                    hole_shape=hole_shape_str,
                    is_plated=is_plated,
                    is_smd=is_smd,
                    layer_num=lnum,
                    layer_name=lname,
                    net_index=p.net_index,
                    net_name=net_name,
                    component_index=p.component_index,
                    component_designator=comp_des,
                    rotation=float(getattr(p, "rotation", 0.0)),
                )
            )
        return pads

    def _parse_vias(
        self,
        doc: am.altium_pcbdoc.AltiumPcbDoc,
        net_map: Dict[int, str],
        layer_map: Dict[int, str],
    ) -> List[ViaEntity]:
        vias: List[ViaEntity] = []
        for idx, v in enumerate(getattr(doc, "vias", [])):
            x_mm = dxt_to_mm(v.x)
            y_mm = dxt_to_mm(v.y)
            dia_mm = dxt_to_mm(v.diameter)
            dia_mils = dxt_to_mil(v.diameter)
            hole_mm = dxt_to_mm(v.hole_size)
            hole_mils = dxt_to_mil(v.hole_size)

            l_start = int(getattr(v, "layer_start", 1))
            l_end = int(getattr(v, "layer_end", 32))
            l_start_name = layer_map.get(l_start, _safe_layer_name(l_start))
            l_end_name = layer_map.get(l_end, _safe_layer_name(l_end))

            is_tht = (l_start == 1 and l_end == 32) or (l_start == 32 and l_end == 1)
            net_name = net_map.get(v.net_index) if v.net_index is not None else None
            ipc = getattr(v, "ipc4761_via_type", None)
            ipc_str = getattr(ipc, "name", str(ipc)) if ipc else "None"

            vias.append(
                ViaEntity(
                    index=idx,
                    x_mm=x_mm,
                    y_mm=y_mm,
                    diameter_mm=dia_mm,
                    diameter_mils=dia_mils,
                    hole_size_mm=hole_mm,
                    hole_size_mils=hole_mils,
                    layer_start=l_start,
                    layer_end=l_end,
                    layer_start_name=l_start_name,
                    layer_end_name=l_end_name,
                    is_through_hole=is_tht,
                    net_index=v.net_index,
                    net_name=net_name,
                    ipc4761_type=ipc_str,
                    is_tent_top=bool(getattr(v, "is_tent_top", False)),
                    is_tent_bottom=bool(getattr(v, "is_tent_bottom", False)),
                )
            )
        return vias

    def _parse_silkscreen(
        self,
        doc: am.altium_pcbdoc.AltiumPcbDoc,
        comp_map: Dict[int, str],
        layer_map: Dict[int, str],
    ) -> List[SilkscreenEntity]:
        texts: List[SilkscreenEntity] = []
        for idx, t in enumerate(getattr(doc, "texts", [])):
            text_str = str(getattr(t, "text_content", "") or "")
            if not text_str and hasattr(t, "text"):
                text_str = str(t.text)

            x_mm = dxt_to_mm(t.x)
            y_mm = dxt_to_mm(t.y)
            h_mm = dxt_to_mm(getattr(t, "height", 0))
            h_mils = dxt_to_mil(getattr(t, "height", 0))
            sw_mm = dxt_to_mm(getattr(t, "stroke_width", 0))
            sw_mils = dxt_to_mil(getattr(t, "stroke_width", 0))

            font_type_num = getattr(t, "font_type", 1)
            font_type = "TrueType" if font_type_num == 1 else ("Barcode" if font_type_num == 2 else "Stroke")
            font_name = str(getattr(t, "font_name", "Arial") or "Arial")

            lnum = int(t.layer)
            lname = layer_map.get(lnum, _safe_layer_name(lnum))
            comp_des = (
                comp_map.get(t.component_index) if getattr(t, "component_index", None) is not None else None
            )

            texts.append(
                SilkscreenEntity(
                    index=idx,
                    text=text_str,
                    x_mm=x_mm,
                    y_mm=y_mm,
                    height_mm=h_mm,
                    height_mils=h_mils,
                    stroke_width_mm=sw_mm,
                    stroke_width_mils=sw_mils,
                    font_name=font_name,
                    font_type=font_type,
                    rotation=float(getattr(t, "rotation", 0.0)),
                    layer_num=lnum,
                    layer_name=lname,
                    is_mirrored=bool(getattr(t, "is_mirrored", False)),
                    is_inverted=bool(getattr(t, "is_inverted", False)),
                    is_designator=bool(getattr(t, "is_designator", False)),
                    is_comment=bool(getattr(t, "is_comment", False)),
                    component_index=getattr(t, "component_index", None),
                    component_designator=comp_des,
                )
            )
        return texts

    def _parse_components(
        self, doc: am.altium_pcbdoc.AltiumPcbDoc, pads: List[PadEntity]
    ) -> List[ComponentEntity]:
        components: List[ComponentEntity] = []

        # Map pads count & SMD status to component index
        comp_pad_count: Dict[int, int] = {}
        comp_has_tht: Dict[int, bool] = {}
        for p in pads:
            if p.component_index is not None:
                comp_pad_count[p.component_index] = comp_pad_count.get(p.component_index, 0) + 1
                if not p.is_smd:
                    comp_has_tht[p.component_index] = True

        for idx, c in enumerate(getattr(doc, "components", [])):
            des = str(getattr(c, "designator", f"U{idx}"))
            footprint = str(getattr(c, "footprint", "Unknown"))
            layer = str(getattr(c, "layer", "TOP")).upper()

            # Coordinates
            x_raw = getattr(c, "x", "0")
            y_raw = getattr(c, "y", "0")
            x_mils, x_mm = parse_altium_dim_string(x_raw)
            y_mils, y_mm = parse_altium_dim_string(y_raw)

            # Rotation
            rot_raw = getattr(c, "rotation", "0")
            try:
                rotation = float(rot_raw)
            except ValueError:
                rotation = 0.0

            desc = getattr(c, "description", None)
            comment = getattr(c, "comment", None)
            params = dict(getattr(c, "parameters", {}))
            uid = getattr(c, "unique_id", None)

            pad_cnt = comp_pad_count.get(idx, 0)
            is_smd = not comp_has_tht.get(idx, False)

            components.append(
                ComponentEntity(
                    index=idx,
                    designator=des,
                    footprint=footprint,
                    layer=layer,
                    x_mm=x_mm,
                    y_mm=y_mm,
                    rotation=rotation,
                    description=desc,
                    comment=comment,
                    parameters=params,
                    is_smd=is_smd,
                    pad_count=pad_cnt,
                    unique_id=uid,
                )
            )
        return components

    def _parse_nets(
        self,
        doc: am.altium_pcbdoc.AltiumPcbDoc,
        tracks: List[TrackEntity],
        vias: List[ViaEntity],
        pads: List[PadEntity],
    ) -> List[NetEntity]:
        nets: List[NetEntity] = []

        # Count tracks, vias, pads and trace lengths per net
        net_tracks: Dict[int, int] = {}
        net_length_mm: Dict[int, float] = {}
        for t in tracks:
            if t.net_index is not None:
                net_tracks[t.net_index] = net_tracks.get(t.net_index, 0) + 1
                net_length_mm[t.net_index] = net_length_mm.get(t.net_index, 0.0) + t.length_mm

        net_vias: Dict[int, int] = {}
        for v in vias:
            if v.net_index is not None:
                net_vias[v.net_index] = net_vias.get(v.net_index, 0) + 1

        net_pads: Dict[int, int] = {}
        for p in pads:
            if p.net_index is not None:
                net_pads[p.net_index] = net_pads.get(p.net_index, 0) + 1

        diff_pair_map: Dict[str, str] = {}
        for dp in getattr(doc, "differential_pairs", []):
            dp_name = getattr(dp, "name", "DiffPair")
            pos_net = getattr(dp, "positive_net_name", None)
            neg_net = getattr(dp, "negative_net_name", None)
            if pos_net:
                diff_pair_map[pos_net] = dp_name
            if neg_net:
                diff_pair_map[neg_net] = dp_name

        for idx, n in enumerate(getattr(doc, "nets", [])):
            name = str(getattr(n, "name", f"Net_{idx}"))
            t_cnt = net_tracks.get(idx, 0)
            v_cnt = net_vias.get(idx, 0)
            p_cnt = net_pads.get(idx, 0)
            tot_len_mm = round(net_length_mm.get(idx, 0.0), 3)
            tot_len_mils = round(tot_len_mm * MM_TO_MIL, 2)

            is_dp = name in diff_pair_map
            dp_name = diff_pair_map.get(name)

            nets.append(
                NetEntity(
                    index=idx,
                    name=name,
                    is_diff_pair=is_dp,
                    diff_pair_name=dp_name,
                    track_count=t_cnt,
                    via_count=v_cnt,
                    pad_count=p_cnt,
                    total_track_length_mm=tot_len_mm,
                    total_track_length_mils=tot_len_mils,
                )
            )
        return nets

    def _parse_rules(
        self, doc: am.altium_pcbdoc.AltiumPcbDoc
    ) -> Tuple[List[DesignRuleEntity], List[ClearanceRuleEntity]]:
        rules: List[DesignRuleEntity] = []
        clearance_rules: List[ClearanceRuleEntity] = []

        for idx, r in enumerate(getattr(doc, "rules", [])):
            name = getattr(r, "name", f"Rule_{idx}")
            kind = getattr(r, "rule_kind", type(r).__name__)
            enabled = bool(getattr(r, "enabled", True))
            priority = int(getattr(r, "priority", 1))

            s1 = getattr(r, "scope1_expression", "All")
            s2 = getattr(r, "scope2_expression", None)
            net_scope = getattr(r, "net_scope", None)
            layer_scope = getattr(r, "layer_kind", None)

            # Raw constraints dictionary sanitized for JSON
            raw_props = getattr(r, "properties", None) or (r if isinstance(r, dict) else vars(r))
            clean_constraints = {}
            for k, v in raw_props.items():
                if k.startswith("_") or k in ["raw_record", "raw_record_payload", "record_leader"]:
                    continue
                if isinstance(v, bytes):
                    clean_constraints[k] = v.hex()
                elif isinstance(v, (str, int, float, bool, list, dict)) or v is None:
                    clean_constraints[k] = v
                else:
                    clean_constraints[k] = str(v)

            rule_entity = DesignRuleEntity(
                index=idx,
                name=name,
                rule_kind=kind,
                enabled=enabled,
                priority=priority,
                scope1=str(s1),
                scope2=str(s2) if s2 else None,
                net_scope=str(net_scope) if net_scope else None,
                layer_scope=str(layer_scope) if layer_scope else None,
                raw_constraints=clean_constraints,
            )
            rules.append(rule_entity)

            # If Clearance rule, construct specialized ClearanceRuleEntity
            if "clearance" in kind.lower():
                gap_raw = getattr(r, "gap", clean_constraints.get("GAP"))
                gen_raw = getattr(r, "generic_clearance", clean_constraints.get("GENERICCLEARANCE"))
                gap_mils, gap_mm = parse_altium_dim_string(gap_raw)
                gen_mils, gen_mm = parse_altium_dim_string(gen_raw)

                matrix = getattr(r, "object_clearances", {})

                cl_rule = ClearanceRuleEntity(
                    index=idx,
                    name=name,
                    rule_kind=kind,
                    enabled=enabled,
                    priority=priority,
                    scope1=str(s1),
                    scope2=str(s2) if s2 else None,
                    net_scope=str(net_scope) if net_scope else None,
                    layer_scope=str(layer_scope) if layer_scope else None,
                    raw_constraints=clean_constraints,
                    gap_mm=gap_mm,
                    gap_mils=gap_mils,
                    generic_clearance_mm=gen_mm,
                    generic_clearance_mils=gen_mils,
                    matrix_clearances=matrix,
                )
                clearance_rules.append(cl_rule)

        return rules, clearance_rules

    def _parse_violations(
        self, ole: olefile.OleFileIO, rules: List[DesignRuleEntity]
    ) -> List[DrcViolationEntity]:
        violations: List[DrcViolationEntity] = []
        rule_name_by_idx = {r.index: r.name for r in rules}

        violation_streams = [
            ("Clearance", ["TClearanceViolation", "Data"]),
            ("SilkToSilkClearance", ["TSilkToSilkClearanceViolation", "Data"]),
            ("RoutingViaStyle", ["TRoutingViaStyleViolation", "Data"]),
        ]

        for vkind, stream_path in violation_streams:
            if not ole.exists(stream_path):
                continue
            data = ole.openstream(stream_path).read()
            records = _parse_pipe_records(data)
            for rec in records:
                ridx_str = rec.get("RULEINDEX")
                ridx = int(ridx_str) if ridx_str and ridx_str.isdigit() else None
                rname = rule_name_by_idx.get(ridx) if ridx is not None else rec.get("RULENAME")

                p1_id = rec.get("PRIM1ID")
                p1_idx = int(rec["PRIM1INDEX"]) if rec.get("PRIM1INDEX", "").isdigit() else None
                p2_id = rec.get("PRIM2ID")
                p2_idx = int(rec["PRIM2INDEX"]) if rec.get("PRIM2INDEX", "").isdigit() else None
                desc = rec.get("DESCRIPTION", "")

                loc1_x = parse_altium_dim_string(rec.get("LOCATION1.X"))[1] if "LOCATION1.X" in rec else None
                loc1_y = parse_altium_dim_string(rec.get("LOCATION1.Y"))[1] if "LOCATION1.Y" in rec else None
                loc2_x = parse_altium_dim_string(rec.get("LOCATION2.X"))[1] if "LOCATION2.X" in rec else None
                loc2_y = parse_altium_dim_string(rec.get("LOCATION2.Y"))[1] if "LOCATION2.Y" in rec else None

                violations.append(
                    DrcViolationEntity(
                        violation_kind=vkind,
                        rule_index=ridx,
                        rule_name=rname,
                        prim1_type=p1_id,
                        prim1_index=p1_idx,
                        prim2_type=p2_id,
                        prim2_index=p2_idx,
                        description=desc,
                        location1_x_mm=loc1_x,
                        location1_y_mm=loc1_y,
                        location2_x_mm=loc2_x,
                        location2_y_mm=loc2_y,
                    )
                )

        return violations

    def _compute_drills(self, pads: List[PadEntity], vias: List[ViaEntity]) -> List[DrillToolBin]:
        """Aggregate holes across pads and vias into drill bins sorted by diameter."""
        bins: Dict[float, DrillToolBin] = {}

        for p in pads:
            if p.hole_size_mm > 0.001:
                sz = round(p.hole_size_mm, 3)
                if sz not in bins:
                    bins[sz] = DrillToolBin(
                        hole_size_mm=sz,
                        hole_size_mils=round(sz * MM_TO_MIL, 2),
                        is_plated=p.is_plated,
                    )
                bins[sz].pad_count += 1
                bins[sz].total_count += 1

        for v in vias:
            if v.hole_size_mm > 0.001:
                sz = round(v.hole_size_mm, 3)
                if sz not in bins:
                    bins[sz] = DrillToolBin(
                        hole_size_mm=sz,
                        hole_size_mils=round(sz * MM_TO_MIL, 2),
                        is_plated=True,
                    )
                bins[sz].via_count += 1
                bins[sz].total_count += 1

        return sorted(bins.values(), key=lambda b: b.hole_size_mm)

    def _compute_statistics(
        self,
        components: List[ComponentEntity],
        tracks: List[TrackEntity],
        pads: List[PadEntity],
        vias: List[ViaEntity],
        nets: List[NetEntity],
        rules: List[DesignRuleEntity],
        violations: List[DrcViolationEntity],
        clearance_rules: List[ClearanceRuleEntity],
        drills: List[DrillToolBin],
    ) -> PcbStatistics:
        tot_comps = len(components)
        smd_comps = sum(1 for c in components if c.is_smd)
        tht_comps = tot_comps - smd_comps
        top_comps = sum(1 for c in components if c.layer == "TOP")
        bot_comps = sum(1 for c in components if c.layer == "BOTTOM")

        tot_tracks = len(tracks)
        tot_len_mm = sum(t.length_mm for t in tracks)
        # Copper tracks are layers TOP, BOTTOM, or MID...
        cu_len_mm = sum(
            t.length_mm
            for t in tracks
            if t.layer_name.startswith("TOP")
            or t.layer_name.startswith("BOTTOM")
            or "MID" in t.layer_name
        )

        tot_pads = len(pads)
        smd_pads = sum(1 for p in pads if p.is_smd)
        tht_pads = tot_pads - smd_pads

        tot_vias = len(vias)
        tot_nets = len(nets)
        routed_nets = sum(1 for n in nets if n.track_count > 0)

        min_w_mm = min((t.width_mm for t in tracks if t.width_mm > 0), default=0.0)
        min_clear_mm = min((cr.gap_mm for cr in clearance_rules if cr.gap_mm > 0), default=0.0)
        min_hole_mm = min((d.hole_size_mm for d in drills), default=0.0)

        return PcbStatistics(
            total_components=tot_comps,
            smd_components=smd_comps,
            tht_components=tht_comps,
            top_components=top_comps,
            bottom_components=bot_comps,
            total_tracks=tot_tracks,
            total_track_length_mm=round(tot_len_mm, 2),
            copper_track_length_mm=round(cu_len_mm, 2),
            total_pads=tot_pads,
            smd_pads=smd_pads,
            tht_pads=tht_pads,
            total_vias=tot_vias,
            total_nets=tot_nets,
            routed_nets=routed_nets,
            total_rules=len(rules),
            total_violations=len(violations),
            min_track_width_mm=round(min_w_mm, 4),
            min_clearance_rule_mm=round(min_clear_mm, 4),
            min_hole_size_mm=round(min_hole_mm, 4),
        )
