---
description: Inspect and analyze an Altium Designer .PcbDoc file
argument-hint: [path-to-pcbdoc]
---

Inspect the specified Altium Designer `.PcbDoc` file using PCBsight:

```bash
python pcbsight.py inspect "$1"
```

If the user wants detailed design rules or clearance audit:
```bash
python pcbsight.py rules "$1"
```

If the user wants full report and exports (Markdown, JSON, CSV, SVG):
```bash
python pcbsight.py report "$1" --export-all
```
