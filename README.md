# PCBsight

A lightweight, standalone tool and AI agent skill for deep extraction, inspection, and analysis of Altium Designer `.PcbDoc` binary files — without requiring Altium Designer or Windows GUI automation.

> **Note:** This project was **vibe-coded**. If you run into edge cases, unsupported record types, or see room for improvement, please open an **[Issue](https://github.com/A-Magpie/PCBsight/issues)**! Feedback and PRs are very welcome.

---

## Features

- **Board Dimensions & Outline:** Width, height, bounding box (mm / mils), cutouts, and surface area.
- **Full Layer Stackup:** Copper (Top, Bottom, Mid), dielectric, solder masks, overlays, keepout, and mechanical layers (1..32).
- **Tracks & Routing:** Segment coordinates, trace widths, lengths per net/layer, and routing completion metrics.
- **Silkscreen & Markings:** Component designators, values, and standalone text annotations (font, height, rotation, layer).
- **Design Rules & Clearance:** Clearance rules (gap, generic, matrix), routing width constraints, via styles, and mask rules.
- **DRC Violations Audit:** Extracts recorded clearance violations, silk-to-silk collisions, and via style errors directly from OLE streams.
- **BOM & Components:** SMD vs THT breakdown, placement coordinates, footprint names, and manufacturer part numbers (MPN).
- **Drill Schedule:** Hole diameter bins, plating status (PTH / NPTH), and pad/via counts.
- **Multi-Format Exports:** Structured JSON, comprehensive Markdown report, CSV tables (BOM, drills, nets), and SVG layer renders.

---

## Quick Start

### Installation

```bash
pip install altium-monkey olefile
```

### Usage

```bash
# 1. Quick inspection in terminal
python pcbsight.py inspect board.PcbDoc

# 2. Audit design rules, clearance & DRC violations
python pcbsight.py rules board.PcbDoc

# 3. View layer stackup
python pcbsight.py layers board.PcbDoc

# 4. Generate full Markdown report, JSON dump, CSVs & SVGs
python pcbsight.py report board.PcbDoc --out ./report/ --export-all
```

---

## Python API

```python
from scripts.pcbdoc_parser import PCBDocParser
from scripts.pcb_reporter import PcbReporter

# Parse board
board = PCBDocParser("board.PcbDoc").parse()
print(f"Size: {board.dimensions.width_mm} x {board.dimensions.height_mm} mm")
print(f"Components: {board.statistics.total_components}")

# Export report
reporter = PcbReporter(board)
print(reporter.to_markdown())
reporter.to_json("board_data.json")
```

---

## License

MIT
