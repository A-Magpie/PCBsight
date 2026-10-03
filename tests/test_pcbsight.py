"""
test_pcbsight.py - Automated Unit & Integration Tests for PCBsight Skill.
Tests parsing, metrics, rules, clearance, violations, and reporting.
"""

import os
import sys
import unittest

# Add scripts directory to sys.path
scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from pcbdoc_parser import PCBDocParser
from pcb_analyzer import PCBAnalyzer
from pcb_reporter import PcbReporter

SAMPLE_PCB = r"S:\My Computer\Work\HUBUSB\HUBUSBISO.PcbDoc"


class TestPCBsight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(SAMPLE_PCB):
            raise unittest.SkipTest(f"Sample file not found: {SAMPLE_PCB}")
        cls.parser = PCBDocParser(SAMPLE_PCB)
        cls.res = cls.parser.parse()
        cls.analyzer = PCBAnalyzer(cls.res)
        cls.reporter = PcbReporter(cls.res)

    def test_dimensions(self):
        """Verify board outline, dimensions, and area."""
        dim = self.res.dimensions
        self.assertGreater(dim.width_mm, 100.0, "Board width should be ~114mm")
        self.assertLess(dim.width_mm, 130.0)
        self.assertGreater(dim.height_mm, 35.0, "Board height should be ~42mm")
        self.assertLess(dim.height_mm, 50.0)
        self.assertGreater(dim.area_sq_mm, 4000.0, "Area should be ~4418 mm²")
        self.assertGreater(dim.vertex_count, 4, "Board outline should have vertices")
        self.assertEqual(dim.cutout_count, 1, "Board should have 1 cutout slot")

    def test_layers(self):
        """Verify layer stackup and copper count."""
        self.assertEqual(self.res.copper_layer_count, 2, "Expected 2 copper layers (Top, Bottom)")
        layer_names = [l.name for l in self.res.layers]
        self.assertIn("Top Layer", layer_names)
        self.assertIn("Bottom Layer", layer_names)
        self.assertIn("Top Overlay", layer_names)
        self.assertIn("Bottom Overlay", layer_names)
        self.assertIn("Top Solder", layer_names)
        self.assertIn("Bottom Solder", layer_names)

    def test_tracks_and_routing(self):
        """Verify copper traces and routing statistics."""
        self.assertGreater(len(self.res.tracks), 1500, "Expected >1500 track segments")
        stats = self.res.statistics
        self.assertGreater(stats.copper_track_length_mm, 2000.0, "Expected >2m copper trace length")
        self.assertGreater(stats.min_track_width_mm, 0.0, "Min track width should be > 0")

    def test_silkscreen_markings(self):
        """Verify silkscreen texts and specific board markings."""
        texts = self.res.silkscreen_items
        self.assertGreater(len(texts), 100, "Expected >100 silkscreen items")
        text_strings = [t.text for t in texts]
        self.assertIn("MADE IN IRAN", text_strings, "Should detect 'MADE IN IRAN' silkscreen marking")
        self.assertTrue(any("HUB USB" in t for t in text_strings), "Should detect 'HUB USB' silkscreen marking")

    def test_design_rules_and_clearance(self):
        """Verify design rules and clearance rules extraction."""
        rules = self.res.rules
        self.assertGreaterEqual(len(rules), 30, "Expected >= 30 design rules")
        self.assertGreater(len(self.res.clearance_rules), 0, "Expected clearance rules")

        # Find the primary Clearance rule
        primary_cl = next((cr for cr in self.res.clearance_rules if cr.name == "Clearance"), None)
        self.assertIsNotNone(primary_cl, "Should find 'Clearance' rule")
        self.assertAlmostEqual(primary_cl.gap_mm, 0.200, places=2, msg="Clearance gap should be ~0.2mm (7.874 mil)")
        self.assertEqual(primary_cl.net_scope, "DifferentNets")

    def test_drc_violations(self):
        """Verify DRC violation extraction from internal OLE streams."""
        violations = self.res.violations
        self.assertGreater(len(violations), 50, "Expected > 50 recorded DRC violations")
        kinds = set(v.violation_kind for v in violations)
        self.assertIn("Clearance", kinds, "Should extract Clearance violations")
        self.assertIn("SilkToSilkClearance", kinds, "Should extract Silk-to-Silk violations")
        self.assertIn("RoutingViaStyle", kinds, "Should extract Routing Via Style violations")

    def test_components_and_bom(self):
        """Verify component parsing, parameters, and BOM generation."""
        comps = self.res.components
        self.assertEqual(len(comps), 88, "Expected 88 components")
        des_map = {c.designator: c for c in comps}
        self.assertIn("U1", des_map)
        self.assertIn("U2", des_map)

        # U1 is Mornsun isolated DC/DC
        u1 = des_map["U1"]
        self.assertEqual(u1.parameters.get("Manufacturer_Name"), "MORNSUN")
        self.assertEqual(u1.parameters.get("Manufacturer_Part_Number"), "B0505S-2WR3")

        # Test BOM aggregation
        bom = self.analyzer.get_bom_summary()
        self.assertGreater(len(bom), 15, "Expected >15 BOM items")

    def test_drills(self):
        """Verify drill schedule and tool bins."""
        drills = self.res.drills
        self.assertGreaterEqual(len(drills), 6, "Expected >= 6 drill tool bins")
        hole_sizes = [d.hole_size_mm for d in drills]
        self.assertIn(0.5, hole_sizes)
        self.assertIn(0.6, hole_sizes)

    def test_reports_and_serialization(self):
        """Verify JSON and Markdown report generation."""
        d = self.res.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["filename"], "HUBUSBISO.PcbDoc")

        md = self.reporter.to_markdown()
        self.assertIn("PCBsight Inspection Report", md)
        self.assertIn("MADE IN IRAN", md)
        self.assertIn("Clearance", md)


if __name__ == "__main__":
    unittest.main()
