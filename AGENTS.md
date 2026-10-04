# AGENTS.md - Cross-Agent Operational Guide

Universal instructions for AI coding assistants (Claude Code, OpenAI Codex, Google Antigravity, and Cursor).

## About PCBsight
PCBsight parses and extracts 100% of PCB design data from Altium Designer `.PcbDoc` binary files:
- Board outline, dimensions, cutouts & area
- Full layer stackup (copper, technical, mechanical 1..32, keepout)
- Tracks, trace lengths, widths & routing completeness
- Silkscreen markings & texts (مارکاژ)
- Design rules & clearance constraints
- DRC violations audit (clearance, silk-to-silk, via style)
- Component placement, parameters, footprints & BOM
- Drill schedule & hole bins

## Verification & Commands
```bash
# 1. Run tests
python tests/test_pcbsight.py

# 2. Inspect PCB
python pcbsight.py inspect <file.PcbDoc>

# 3. Audit rules & clearance
python pcbsight.py rules <file.PcbDoc>

# 4. Export all artifacts
python pcbsight.py report <file.PcbDoc> --out ./output/ --export-all
```
