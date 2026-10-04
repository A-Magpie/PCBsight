---
name: pcbsight
description: Comprehensive inspection, parsing, and analysis of Altium Designer PCB documents (.PcbDoc). Extracts complete board data including layer stackup, copper tracks, silkscreen markings (مارکاژ), board outline and dimensions, design rules, clearance constraints, DRC violations, components/BOM, padstacks, vias, and drill schedules. Generates Markdown reports, full structured JSON, CSV tables, and SVG layer renders.
---

# PCBsight - Altium Designer .PcbDoc Inspection & Analysis Skill

**PCBsight** is a dedicated skill and toolkit for deep parsing, analyzing, and extracting all engineering data from Altium Designer `.PcbDoc` binary files without requiring Altium Designer or Windows GUI automation.

---

## Capabilities & Extracted Data

PCBsight parses and analyzes 100% of the PCB design database:

1. **Board Outline & Dimensions (ابعاد و خط دور برد)**:
   - Overall board width and height in both Metric (mm) and Imperial (mils).
   - Exact outline geometry (vertices, arcs, lines, origin).
   - Board cutouts and internal routing slots.
   - Net board area (mm² and sq inches).
   - Minimum and maximum coordinate bounding box.

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
   - Distinction between component designators/comments and standalone board markings (e.g. board name, serial numbers, labels, logos, warnings).
   - Font types (TrueType vs Stroke fonts vs Barcode), font names (e.g. Arial), heights, stroke widths, positions, and rotations.

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
   - All placed components with Designator, Footprint pattern, Layer (Top/Bottom), Coordinates (X, Y), and Rotation.
   - Parameter extraction: Manufacturer Part Number (MPN), Manufacturer Name, Value, Description, Package.
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

## Command-Line Usage (CLI)

The CLI can be invoked globally from anywhere using:
```powershell
python "C:\Users\Sam13\.gemini\config\plugins\pcbsight\skills\pcbsight\pcbsight.py" <subcommand> "<path_to_file.PcbDoc>"
```
Or locally from `s:\My Computer\Work\MySkills\PCBsight\pcbsight.py`.

### 1. Quick Board Inspection
Prints an executive summary of dimensions, layers, component counts, routing length, and rule counts in the terminal:
```powershell
python "C:\Users\Sam13\.gemini\config\plugins\pcbsight\skills\pcbsight\pcbsight.py" inspect "<path_to_file.PcbDoc>"
```

### 2. Design Rules & Clearance Audit
Displays all design rules, clearance gaps, and recorded DRC violations:
```powershell
python "C:\Users\Sam13\.gemini\config\plugins\pcbsight\skills\pcbsight\pcbsight.py" rules "<path_to_file.PcbDoc>"
```

### 3. Layer Stackup Table
Displays the layer stackup and dielectric properties:
```powershell
python "C:\Users\Sam13\.gemini\config\plugins\pcbsight\skills\pcbsight\pcbsight.py" layers "<path_to_file.PcbDoc>"
```

### 4. Comprehensive Report & Export
Generates a full Markdown report and JSON dump. Add `--export-all` to also export CSVs and SVG images:
```powershell
# Generate Markdown + JSON report
python "C:\Users\Sam13\.gemini\config\plugins\pcbsight\skills\pcbsight\pcbsight.py" report "<path_to_file.PcbDoc>" --out ./output_folder/

# Generate everything (Markdown, JSON, BOM CSV, Drills CSV, Nets CSV, SVGs)
python "C:\Users\Sam13\.gemini\config\plugins\pcbsight\skills\pcbsight\pcbsight.py" report "<path_to_file.PcbDoc>" --out ./output_folder/ --export-all
```

---

## Python API Usage

To use PCBsight inside custom Python scripts or automated pipelines:

```python
import sys
sys.path.append(r"s:\My Computer\Work\MySkills\PCBsight\scripts")

from pcbdoc_parser import PCBDocParser
from pcb_analyzer import PCBAnalyzer
from pcb_reporter import PcbReporter

# 1. Parse the board file
parser = PCBDocParser("board.PcbDoc")
res = parser.parse()

# 2. Access parsed data structures
print(f"Board size: {res.dimensions.width_mm} x {res.dimensions.height_mm} mm")
print(f"Copper layers: {res.copper_layer_count}")
print(f"Total components: {res.statistics.total_components}")
print(f"Clearance rules: {len(res.clearance_rules)}")
print(f"Violations: {len(res.violations)}")

# 3. Use the Analyzer for aggregated data
analyzer = PCBAnalyzer(res)
bom = analyzer.get_bom_summary()
summary = analyzer.get_summary()

# 4. Generate Reports and Exports
reporter = PcbReporter(res)
md_report = reporter.to_markdown()
reporter.to_json("board_data.json")
reporter.to_csv_bom("bom.csv")
reporter.to_csv_drills("drills.csv")
reporter.export_svgs("./svgs/")
```

---

## Verification & Testing

To run the automated test suite verifying all 9 core features against real Altium boards:
```powershell
python "s:\My Computer\Work\MySkills\PCBsight\tests\test_pcbsight.py"
```
All tests assert dimensions, layer stacks, tracks, silkscreen markings, clearance rules, violations, and BOM integrity.
