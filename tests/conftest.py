from __future__ import annotations

import os

# All document regressions exercise the production Pandoc C converter.
if os.environ.get("PANDOC_TEST_BINARY"):
    os.environ.setdefault("ANSWER_BOOK_PANDOC_BINARY", os.environ["PANDOC_TEST_BINARY"])
os.environ.setdefault("ANSWER_BOOK_LITELLM_SHADOW", "0")
