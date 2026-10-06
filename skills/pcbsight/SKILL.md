---
name: pcbsight
description: Comprehensive inspection, parsing, and analysis of Altium Designer PCB documents (.PcbDoc). Extracts complete board data including layer stackup, copper tracks, silkscreen markings (مارکاژ), board outline and dimensions, design rules, clearance constraints, DRC violations, components/BOM, padstacks, vias, and drill schedules. Generates Markdown reports, full structured JSON, CSV tables, and SVG layer renders.
---

# PCBsight - Altium Designer .PcbDoc Inspection & Analysis Skill

**PCBsight** is a dedicated skill and toolkit for deep parsing, analyzing, and extracting 100% of engineering data from Altium Designer `.PcbDoc` binary files without requiring Altium Designer or Windows GUI automation.

All dependencies (`olefile`, `altium_monkey`) are bundled in `scripts/vendor/`—zero external pip installation is required.

---

## Execution Modes (How to Run PCBsight)

PCBsight can be executed in any AI agent environment (OpenAI Codex, ChatGPT Plugins, Claude Code, Antigravity, or Python Code Interpreter):

### Mode 1: MCP Tools (Recommended when MCP tools are exposed)
If you have access to the PCBsight MCP tools in your session, call them directly:
- `pcbsight_inspect(file_path="...")` -> Returns board outline, dimensions, layers, component counts, routing length, rule counts.
- `pcbsight_rules(file_path="...")` -> Audits clearance rules, design rules, and recorded DRC violations.
- `pcbsight_layers(file_path="...")` -> Returns full physical layer stackup and dielectric properties.
- `pcbsight_bom(file_path="...")` -> Returns placed components, footprints, MPN, and BOM breakdown.
- `pcbsight_report(file_path="...", output_dir="./output", export_all=True)` -> Generates Markdown report, JSON dump, CSV tables, and SVG renders.

### Mode 2: Command-Line Interface (CLI / Terminal)
When a shell or terminal is available:
```bash
# 1. Quick Board Inspection
python pcbsight.py inspect "<path_to_file.PcbDoc>"

# 2. Design Rules & Clearance Audit
python pcbsight.py rules "<path_to_file.PcbDoc>"

# 3. Layer Stackup Table
python pcbsight.py layers "<path_to_file.PcbDoc>"

# 4. Generate Full Report & Export All Artifacts
python pcbsight.py report "<path_to_file.PcbDoc>" --out ./output/ --export-all
```
*(If running from outside the repo, replace `pcbsight.py` with `<skill_dir>/pcbsight.py` or `<skill_dir>/scripts/cli.py`)*

### Mode 3: Python Code Interpreter / Sandboxed Python (ChatGPT Web / Jupyter)
When running inside a Python Code Interpreter sandbox:
```python
import sys
import os

# Automatically locate and add pcbsight scripts to sys.path
candidate_dirs = [
    os.getcwd(),
    os.path.join(os.getcwd(), "skills", "pcbsight"),
    os.path.join(os.getcwd(), "skills", "pcbsight", "scripts"),
    os.path.join(os.getcwd(), "scripts"),
]
for d in candidate_dirs:
    if os.path.exists(d) and d not in sys.path:
        sys.path.insert(0, os.path.abspath(d))

from pcbdoc_parser import PCBDocParser
from pcb_analyzer import PCBAnalyzer
from pcb_reporter import PcbReporter

# 1. Parse board file
parser = PCBDocParser("path/to/board.PcbDoc")
result = parser.parse()

# 2. Get comprehensive analysis summary
analyzer = PCBAnalyzer(result)
summary = analyzer.get_summary()
print("Board Dimensions:", summary["dimensions"])
print("Copper Layers:", summary["copper_layers"])
print("Total Components:", summary["components"]["total_count"])
print("Design Rules:", summary["rules"]["rule_count"])
print("DRC Violations:", summary["rules"]["recorded_violations_count"])

# 3. Export reports
reporter = PcbReporter(result)
md_report = reporter.to_markdown()
reporter.to_json("board_data.json")
```

---

## Capabilities & Extracted Data

PCBsight extracts and validates 100% of the PCB design database:

1. **Board Outline & Dimensions (ابعاد و خط دور برد)**:
   - Overall board width and height in Metric (mm) and Imperial (mils).
   - Exact outline geometry (vertices, arcs, lines, origin).
   - Board cutouts and internal routing slots.
   - Net board area (mm² and sq inches).
   - Coordinate bounding box (Min/Max X, Y).

2. **Layer Stackup (لایه ها)**:
   - Full physical layer stack (Top Layer, Bottom Layer, Mid 1..N, Internal Planes).
   - Technical layers (Top/Bottom Solder Mask, Top/Bottom Paste, Top/Bottom Overlay / Silkscreen).
   - Dielectric layers (Core, Prepreg, thicknesses, materials).
   - Mechanical layers (1..32, Route Guide, Keepout).
   - Layer usage indicators and layer ID mappings.

3. **Tracks & Copper Routing (ترک ها و سیم‌کشی)**:
   - All track primitives with start/end coordinates, widths, and lengths.
   - Layer assignment and associated Net name / Net index.
   - Total routed trace length per net and per layer.
   - Trace width distribution (min, max, preferred trace widths).

4. **Silkscreen & Markings (مارکاژ و چاپ راهنما)**:
   - All text entities on `TOP_OVERLAY`, `BOTTOM_OVERLAY`, and mechanical layers.
   - Component designators vs standalone board markings (board name, revision, serial numbers, labels, logos, warnings).
   - Font types (TrueType vs Stroke fonts vs Barcode), heights, stroke widths, positions, and rotations.

5. **Design Rules & Clearance (قوانین و کلیرنس)**:
   - Full rule database (35+ standard Altium rule types).
   - Clearance rules (Different Nets, Same Net, Matrix clearances, Scope expressions, Priority).
   - Routing width rules (Min, Preferred, Max).
   - Routing via style rules (Diameter, Hole size).
   - Solder mask and paste mask expansion rules.
   - Silk-to-silk, silk-to-solder, and hole-to-hole constraints.

6. **DRC Violations Audit (خطاها و تداخلات کلیرنس)**:
   - Extraction of recorded design violations (`TClearanceViolation`, `TSilkToSilkClearanceViolation`, `TRoutingViaStyleViolation`).
   - Pinpointing exact colliding primitives (e.g. Pad #230 <-> Pad #50).
   - Violation coordinates (X, Y) and violation clearance gap measurements.

7. **Components & Bill of Materials (قطعات و لیست قطعات)**:
   - Placed components with Designator, Footprint pattern, Layer (Top/Bottom), Coordinates (X, Y), and Rotation.
   - Parameter extraction: MPN, Manufacturer Name, Value, Description, Package.
   - Automated SMD vs Through-Hole (THT) component classification.
   - Aggregated Bill of Materials (BOM) grouping.

8. **Pads, Vias & Drill Schedule (پدها، ویاها و جدول سوراخ‌کاری)**:
   - Padstacks (SMD vs THT, shapes: Round, Rectangular, Octagonal, Rounded Rectangle, hole sizes, plating).
   - Vias (Diameter, Hole size, start/end layer spans, tenting, IPC-4761 protection).
   - Drill tool bins schedule sorted by tool diameter with counts of pads and vias.

9. **Exports & Visualization (خروجی‌ها و نقشه‌های برداری)**:
   - Comprehensive GitHub-flavored Markdown report.
   - Full structured JSON document.
   - Export CSVs: `bom.csv`, `drills.csv`, `nets.csv`.
   - SVG vector graphics for board outline, copper layers, silkscreen layers, and drills.

---

## Verification & Testing

To run the automated test suite verifying all core features against real Altium boards:
```bash
python tests/test_pcbsight.py
```
