from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ImageArtifactStoreTests(unittest.TestCase):
    def test_store_is_content_addressed_and_manifest_is_reloadable(self):
        from app.image_artifacts import ImageArtifactStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "assets"
            source = Path(tmp) / "source.png"
            Image.new("RGB", (160, 120), "white").save(source)
            first = ImageArtifactStore(root).register(
                source,
                provider="p",
                model="m",
                source_call_id="c1",
            )
            second = ImageArtifactStore(root).get(first.asset_id)

            self.assertTrue(first.asset_id.startswith("img_sha256_"))
            self.assertEqual(160, first.width)
            self.assertEqual(120, first.height)
            self.assertIsNotNone(second)
            self.assertEqual(first.sha256, second.sha256)
            self.assertTrue(Path(first.path).exists())

    def test_store_rejects_non_image_bytes(self):
        from app.image_artifacts import ImageArtifactStore

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bad.png"
            source.write_bytes(b"not an image")
            with self.assertRaisesRegex(ValueError, "unreadable"):
                ImageArtifactStore(Path(tmp) / "assets").register(
                    source,
                    provider="p",
                    model="m",
                    source_call_id="c1",
                )


if __name__ == "__main__":
    unittest.main()
