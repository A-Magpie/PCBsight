# CODEX.md - Instructions for OpenAI Codex & Copilot CLI

## Overview
PCBsight is a standalone Altium Designer `.PcbDoc` file parser and analysis toolkit written in Python.

## Quick CLI Reference
```bash
# Install requirements
pip install altium-monkey olefile

# Execute automated tests
python tests/test_pcbsight.py

# Inspect PCB summary
python pcbsight.py inspect <file.PcbDoc>

# Audit rules, clearance, and DRC errors
python pcbsight.py rules <file.PcbDoc>

# View physical layer stackup
python pcbsight.py layers <file.PcbDoc>

# Generate full report (Markdown, JSON, CSVs, SVGs)
python pcbsight.py report <file.PcbDoc> --out ./output/ --export-all
```

## Key Modules
- `pcbsight.py`: CLI dispatcher.
- `scripts/pcb_model.py`: Data models (`BoardDimensions`, `LayerInfo`, `TrackEntity`, `SilkscreenEntity`, `ClearanceRuleEntity`, `DrcViolationEntity`, `ComponentEntity`).
- `scripts/pcbdoc_parser.py`: Low-level OLE and `altium-monkey` composite parser.
- `scripts/pcb_analyzer.py`: Aggregation algorithms (trace lengths, clearance checks, BOM grouping).
- `scripts/pcb_reporter.py`: Multi-format exporters (Markdown, JSON, CSV, SVG).
