from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import omml

MINIMAL_MATHML_TO_OMML_XSL = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
  <xsl:output method="xml" omit-xml-declaration="yes"/>
  <xsl:template match="/">
    <m:oMath><m:r><m:t><xsl:value-of select="string(.)"/></m:t></m:r></m:oMath>
  </xsl:template>
</xsl:stylesheet>
"""


class OmmlPerformanceTests(unittest.TestCase):
    def tearDown(self) -> None:
        omml.clear_omml_caches()

    def test_windows_xsl_search_is_scoped_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            xsl = root / "Microsoft Office" / "root" / "Office16" / "MML2OMML.XSL"
            xsl.parent.mkdir(parents=True)
            xsl.write_text(MINIMAL_MATHML_TO_OMML_XSL, encoding="utf-8")
            omml.clear_omml_caches()

            with patch("app.omml.platform.system", return_value="Windows"), patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(root),
                    "ProgramFiles(x86)": "",
                    "LOCALAPPDATA": str(root / "user-profile"),
                    "MATHML2OMML_XSL": "",
                },
                clear=False,
            ):
                first = omml.find_mathml2omml_xsl()
                second = omml.find_mathml2omml_xsl()

            self.assertEqual(xsl.resolve(), first)
            self.assertEqual(first, second)
            self.assertEqual(1, omml._find_mathml2omml_xsl_cached.cache_info().misses)
            self.assertEqual(1, omml._find_mathml2omml_xsl_cached.cache_info().hits)

    def test_repeated_formula_reuses_compiled_xslt_and_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            xsl = Path(raw_tmp) / "MML2OMML.XSL"
            xsl.write_text(MINIMAL_MATHML_TO_OMML_XSL, encoding="utf-8")
            omml.clear_omml_caches()

            with patch.dict(os.environ, {"MATHML2OMML_XSL": str(xsl)}, clear=False):
                first = omml.omml_from_latex_via_mathml(r"W = pV")
                second = omml.omml_from_latex_via_mathml(r"W = pV")

            self.assertEqual(first.tag, second.tag)
            self.assertIsNot(first, second)
            self.assertEqual(1, omml._compiled_mathml2omml_transform.cache_info().misses)
            self.assertEqual(1, omml._mathml2omml_xml.cache_info().misses)
            self.assertEqual(1, omml._mathml2omml_xml.cache_info().hits)


if __name__ == "__main__":
    unittest.main()
