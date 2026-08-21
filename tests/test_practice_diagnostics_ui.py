import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class PracticeDiagnosticsUITests(unittest.TestCase):
    def test_scope_view_has_diagnostics_container_and_renderer(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.assertIn('id="practiceSourceDiagnostics"', html)
        self.assertIn('id="practiceSourceDiagnosticsList"', html)
        self.assertIn("renderPracticeSourceDiagnostics(data.source_file_diagnostics || [])", js)
        self.assertIn("item.omml_formula_count", js)
        self.assertIn("item.table_count", js)
        self.assertIn("item.image_count_included", js)
        self.assertIn("item.embedded_image_count", js)
        self.assertIn("item.warnings", js)
        self.assertIn("knowledge_overall", js)
        self.assertIn("practice-knowledge-strategy", js)
        self.assertIn("source_difficulty", js)


if __name__ == "__main__":
    unittest.main()
