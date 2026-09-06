from copy import deepcopy
from pathlib import Path

import pytest

from scripts.replay_saved_delivery import ReplayAssets


def test_replay_localizes_same_asset_identity_without_mutating_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    asset = source / "image.png"
    asset.write_bytes(b"original asset")
    original = {"image_refs": [str(asset)], "blocks": [{"type": "image_ref", "path": "image.png"}],
                "figure_specs": [{"image_path": str(asset)}], "text": str(asset)}
    before = deepcopy(original)
    output = tmp_path / "replay"
    localized = ReplayAssets(source, output).localize(original)
    target = Path(localized["image_refs"][0])
    assert target.is_relative_to(output)
    assert localized["blocks"][0]["path"] == str(target)
    assert localized["figure_specs"][0]["image_path"] == str(target)
    assert localized["text"] == str(asset)
    assert original == before
    assert target.read_bytes() == asset.read_bytes()
    target.with_name("figure_word_fit.json").write_text("derived")
    assert sorted(p.name for p in source.iterdir()) == ["image.png"]
    assert asset.read_bytes() == b"original asset"


def test_missing_assets_remain_missing_and_same_named_assets_do_not_collide(tmp_path):
    source = tmp_path / "source"
    for name in ("a", "b"):
        folder = source / name
        folder.mkdir(parents=True)
        (folder / "image.png").write_text(name)
    mapper = ReplayAssets(source, tmp_path / "replay")
    a, b, missing = mapper.localize(["a/image.png", "b/image.png", "missing.png"], "image_refs")
    assert a != b
    assert Path(a).read_text() == "a"
    assert Path(b).read_text() == "b"
    assert not Path(missing).exists()
    assert Path(missing).is_relative_to(tmp_path / "replay")


@pytest.mark.parametrize("value", ["https://example.com/image.png", "data:image/png;base64,AA=="])
def test_offline_replay_rejects_remote_assets(tmp_path, value):
    with pytest.raises(ValueError, match="local assets"):
        ReplayAssets(tmp_path, tmp_path / "output").localize({"image_path": value})
