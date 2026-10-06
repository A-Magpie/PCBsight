"""
pcb_model.py - Strongly-typed Data Models for Altium .PcbDoc Inspection and Analysis.
Part of the PCBsight skill suite.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple


# Conversion Constants:
# Altium internal coordinates use 10,000 units per mil (1 mil = 0.001 inch = 0.0254 mm)
UNITS_PER_MIL = 10000.0
MIL_TO_MM = 0.0254
MM_TO_MIL = 1.0 / 0.0254


def dxt_to_mil(val: float | int | None) -> float:
    """Convert Altium internal integer/float unit to mils."""
    if val is None:
        return 0.0
    return round(float(val) / UNITS_PER_MIL, 4)


def dxt_to_mm(val: float | int | None) -> float:
    """Convert Altium internal integer/float unit to millimeters."""
    if val is None:
        return 0.0
    return round((float(val) / UNITS_PER_MIL) * MIL_TO_MM, 4)


def mil_to_mm(val: float | int | None) -> float:
    """Convert mils to millimeters."""
    if val is None:
        return 0.0
    return round(float(val) * MIL_TO_MM, 4)


def parse_altium_dim_string(s: Optional[str]) -> Tuple[float, float]:
    """Parse string like '7.874mil' or '0.2mm' returning (mils, mm)."""
    if not s:
        return 0.0, 0.0
    clean = str(s).strip()
    if clean.endswith("mil"):
        try:
            mils = float(clean[:-3].strip())
            return mils, mil_to_mm(mils)
        except ValueError:
            return 0.0, 0.0
    elif clean.endswith("mm"):
        try:
            mm = float(clean[:-2].strip())
            return round(mm * MM_TO_MIL, 4), mm
        except ValueError:
            return 0.0, 0.0
    elif clean.endswith("in"):
        try:
            inches = float(clean[:-2].strip())
            mils = inches * 1000.0
            return mils, mil_to_mm(mils)
        except ValueError:
            return 0.0, 0.0
    else:
        try:
            v = float(clean)
            return v, mil_to_mm(v)
        except ValueError:
            return 0.0, 0.0


@dataclass
class Point2D:
    x_mm: float
    y_mm: float
    x_mils: float
    y_mils: float


@dataclass
class BoardOutlineVertex:
    x_mm: float
    y_mm: float
    x_mils: float
    y_mils: float
    is_arc: bool = False
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    radius_mm: float = 0.0
    start_angle_deg: Optional[float] = None
    end_angle_deg: Optional[float] = None


@dataclass
class BoardDimensions:
    width_mm: float = 0.0
    height_mm: float = 0.0
    width_mils: float = 0.0
    height_mils: float = 0.0
    area_sq_mm: float = 0.0
    area_sq_in: float = 0.0
    min_x_mm: float = 0.0
    min_y_mm: float = 0.0
    max_x_mm: float = 0.0
    max_y_mm: float = 0.0
    min_x_mils: float = 0.0
    min_y_mils: float = 0.0
    max_x_mils: float = 0.0
    max_y_mils: float = 0.0
    vertex_count: int = 0
    cutout_count: int = 0
    vertices: List[BoardOutlineVertex] = field(default_factory=list)
    cutouts: List[List[BoardOutlineVertex]] = field(default_factory=list)


@dataclass
class LayerInfo:
    index: int
    name: str
    layer_id: int
    kind: str  # 'Copper', 'Dielectric', 'SolderMask', 'Overlay', 'Paste', 'Mechanical', 'Other'
    is_copper: bool = False
    is_technical: bool = False
    is_mechanical: bool = False
    is_used: bool = False
    thickness_mm: Optional[float] = None
    thickness_mils: Optional[float] = None
    material: Optional[str] = None
    dielectric_constant: Optional[float] = None


@dataclass
class TrackEntity:
    index: int
    layer_num: int
    layer_name: str
    start_x_mm: float
    start_y_mm: float
    end_x_mm: float
    end_y_mm: float
    width_mm: float
    width_mils: float
    length_mm: float
    length_mils: float
    net_index: Optional[int] = None
    net_name: Optional[str] = None
    component_index: Optional[int] = None
    component_designator: Optional[str] = None
    is_locked: bool = False
    is_keepout: bool = False
    is_polygon_outline: bool = False


@dataclass
class ArcEntity:
    index: int
    layer_num: int
    layer_name: str
    center_x_mm: float
    center_y_mm: float
    radius_mm: float
    radius_mils: float
    start_angle_deg: float
    end_angle_deg: float
    width_mm: float
    width_mils: float
    net_index: Optional[int] = None
    net_name: Optional[str] = None
    component_index: Optional[int] = None
    component_designator: Optional[str] = None


@dataclass
class PadEntity:
    index: int
    designator: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    shape: str
    hole_size_mm: float
    hole_size_mils: float
    hole_shape: str
    is_plated: bool
    is_smd: bool
    layer_num: int
    layer_name: str
    net_index: Optional[int] = None
    net_name: Optional[str] = None
    component_index: Optional[int] = None
    component_designator: Optional[str] = None
    rotation: float = 0.0


@dataclass
class ViaEntity:
    index: int
    x_mm: float
    y_mm: float
    diameter_mm: float
    diameter_mils: float
    hole_size_mm: float
    hole_size_mils: float
    layer_start: int
    layer_end: int
    layer_start_name: str
    layer_end_name: str
    is_through_hole: bool
    net_index: Optional[int] = None
    net_name: Optional[str] = None
    ipc4761_type: str = "None"
    is_tent_top: bool = False
    is_tent_bottom: bool = False


@dataclass
class SilkscreenEntity:
    index: int
    text: str
    x_mm: float
    y_mm: float
    height_mm: float
    height_mils: float
    stroke_width_mm: float
    stroke_width_mils: float
    font_name: str
    font_type: str  # 'Stroke', 'TrueType', 'Barcode'
    rotation: float
    layer_num: int
    layer_name: str
    is_mirrored: bool = False
    is_inverted: bool = False
    is_designator: bool = False
    is_comment: bool = False
    component_index: Optional[int] = None
    component_designator: Optional[str] = None


@dataclass
class ComponentEntity:
    index: int
    designator: str
    footprint: str
    layer: str  # 'TOP' or 'BOTTOM'
    x_mm: float
    y_mm: float
    rotation: float
    description: Optional[str] = None
    comment: Optional[str] = None
    parameters: Dict[str, str] = field(default_factory=dict)
    is_smd: bool = True
    pad_count: int = 0
    unique_id: Optional[str] = None


@dataclass
class NetEntity:
    index: int
    name: str
    net_class: Optional[str] = None
    is_diff_pair: bool = False
    diff_pair_name: Optional[str] = None
    track_count: int = 0
    via_count: int = 0
    pad_count: int = 0
    total_track_length_mm: float = 0.0
    total_track_length_mils: float = 0.0


@dataclass
class DesignRuleEntity:
    index: int
    name: str
    rule_kind: str
    enabled: bool
    priority: int
    scope1: str
    scope2: Optional[str] = None
    net_scope: Optional[str] = None
    layer_scope: Optional[str] = None
    raw_constraints: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClearanceRuleEntity(DesignRuleEntity):
    gap_mm: float = 0.0
    gap_mils: float = 0.0
    generic_clearance_mm: float = 0.0
    generic_clearance_mils: float = 0.0
    matrix_clearances: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DrcViolationEntity:
    violation_kind: str
    rule_index: Optional[int] = None
    rule_name: Optional[str] = None
    prim1_type: Optional[str] = None
    prim1_index: Optional[int] = None
    prim2_type: Optional[str] = None
    prim2_index: Optional[int] = None
    description: str = ""
    location1_x_mm: Optional[float] = None
    location1_y_mm: Optional[float] = None
    location2_x_mm: Optional[float] = None
    location2_y_mm: Optional[float] = None


@dataclass
class DrillToolBin:
    hole_size_mm: float
    hole_size_mils: float
    is_plated: bool
    pad_count: int = 0
    via_count: int = 0
    total_count: int = 0


@dataclass
class PcbStatistics:
    total_components: int = 0
    smd_components: int = 0
    tht_components: int = 0
    top_components: int = 0
    bottom_components: int = 0
    total_tracks: int = 0
    total_track_length_mm: float = 0.0
    copper_track_length_mm: float = 0.0
    total_pads: int = 0
    smd_pads: int = 0
    tht_pads: int = 0
    total_vias: int = 0
    total_nets: int = 0
    routed_nets: int = 0
    total_rules: int = 0
    total_violations: int = 0
    min_track_width_mm: float = 0.0
    min_clearance_rule_mm: float = 0.0
    min_hole_size_mm: float = 0.0


@dataclass
class PcbAnalysisResult:
    filename: str
    filepath: str
    file_size_bytes: int
    dimensions: BoardDimensions
    layers: List[LayerInfo]
    copper_layer_count: int
    tracks: List[TrackEntity]
    arcs: List[ArcEntity]
    pads: List[PadEntity]
    vias: List[ViaEntity]
    silkscreen_items: List[SilkscreenEntity]
    components: List[ComponentEntity]
    nets: List[NetEntity]
    rules: List[DesignRuleEntity]
    clearance_rules: List[ClearanceRuleEntity]
    violations: List[DrcViolationEntity]
    drills: List[DrillToolBin]
    statistics: PcbStatistics

    def to_dict(self) -> Dict[str, Any]:
        """Convert analysis result to a JSON-serializable dictionary."""
        return asdict(self)
