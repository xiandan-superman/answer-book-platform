from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class WindowsWordRuntimeTests(unittest.TestCase):
    def test_environment_probe_uses_isolated_word_and_quits_it(self) -> None:
        from app.environment import _check_word_windows

        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "ok\n", "stderr": ""},
        )()
        with patch("app.environment.platform.system", return_value="Windows"), patch(
            "app.environment.subprocess.run", return_value=completed
        ) as run, patch("app.environment.find_spec", return_value=object()):
            result = _check_word_windows()

        code = run.call_args.args[0][2]
        self.assertTrue(result["word_com_available"])
        self.assertIn("DispatchEx", code)
        self.assertIn("word.Quit()", code)
        self.assertNotIn("Dispatch(\"Word.Application\")", code)

    def test_pdf_export_passes_windows_paths_as_arguments(self) -> None:
        from app.render_word import export_docx_to_pdf

        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "teacher's questions.docx"
            target = root / "teacher's questions.pdf"
            with patch("app.render_word.platform.system", return_value="Windows"), patch(
                "app.render_word.subprocess.run"
            ) as run:
                result = export_docx_to_pdf(source, target)

        command = run.call_args.args[0]
        code = command[2]
        self.assertEqual(target, result)
        self.assertIn("DispatchEx", code)
        self.assertIn("document.Close(False)", code)
        self.assertIn("word.Quit()", code)
        self.assertEqual(str(source.resolve()), command[3])
        self.assertEqual(str(target.resolve()), command[4])
        self.assertNotIn(str(source), code)


if __name__ == "__main__":
    unittest.main()
