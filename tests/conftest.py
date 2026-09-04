from __future__ import annotations

import os

# The established regression corpus locks A's exact OOXML formatting. B has
# dedicated integration tests; keeping this explicit avoids downloading an
# external runtime in hermetic unit-test jobs while production still defaults B.
os.environ.setdefault("ANSWER_BOOK_WORD_TOOL_VARIANT", "A")
os.environ.setdefault("ANSWER_BOOK_LITELLM_SHADOW", "0")
