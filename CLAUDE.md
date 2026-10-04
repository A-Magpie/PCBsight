# CLAUDE.md - Instructions for Claude Code

## Project Overview
**PCBsight** is a standalone Python tool and agent skill for parsing, inspecting, and analyzing Altium Designer `.PcbDoc` binary files without requiring Altium Designer or Windows GUI automation.

## Core Commands
- **Install dependencies:** `pip install altium-monkey olefile`
- **Run test suite:** `python tests/test_pcbsight.py`
- **Inspect a board:** `python pcbsight.py inspect <path_to_file.PcbDoc>`
- **Audit design rules & clearance:** `python pcbsight.py rules <path_to_file.PcbDoc>`
- **View layer stackup:** `python pcbsight.py layers <path_to_file.PcbDoc>`
- **Full report & exports (Markdown, JSON, CSV, SVG):** `python pcbsight.py report <path_to_file.PcbDoc> --out <dir> --export-all`

## Architecture
- `pcbsight.py`: Root CLI entry point.
- `scripts/pcb_model.py`: Strongly-typed dataclasses (dual Metric/Imperial unit normalization).
- `scripts/pcbdoc_parser.py`: Core parser using `altium-monkey` + direct OLE stream extraction for clearance violations and stackup.
- `scripts/pcb_analyzer.py`: High-level metrics, silkscreen texts, BOM grouping, and DRC audit.
- `scripts/pcb_reporter.py`: Markdown, JSON, CSV (BOM, drills, nets), and SVG layer exporters.
- `tests/test_pcbsight.py`: Automated test suite covering dimensions, layers, tracks, silkscreen, rules, clearance, violations, and BOM.

## Agent Workflow Guidelines
- When a user asks to inspect a `.PcbDoc` file, run `python pcbsight.py inspect <file>` first for an executive summary.
- For clearance or DRC questions, run `python pcbsight.py rules <file>`.
- For deep reporting, run `python pcbsight.py report <file> --export-all` and reference the generated Markdown report.
