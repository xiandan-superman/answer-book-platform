from PIL import Image

from app.figures import draw_crystal_unit_cell


def test_two_dimensional_crystal_spec_uses_binary_lattice_renderer(tmp_path) -> None:
    output = tmp_path / "lattice.png"
    draw_crystal_unit_cell(
        {
            "kind": "crystal_unit_cell",
            "caption": "二维点阵",
            "structure": {
                "dimension": 2,
                "basis": [{"species": "A"}, {"species": "B"}],
                "unit_cell": {"stoichiometric_formula": "AB"},
            },
        },
        output,
    )

    assert output.exists()
    with Image.open(output) as image:
        assert image.width >= 800
        assert image.height >= 700


def test_two_dimensional_string_dimension_from_model_is_accepted(tmp_path) -> None:
    output = tmp_path / "lattice-model-shape.png"

    draw_crystal_unit_cell(
        {
            "kind": "crystal_unit_cell",
            "caption": "A、B交替排列的二维正方点阵及其代表性菱形原胞",
            "structure": {
                "dimension": "2D",
                "bravais_lattice": "二维正方点阵",
                "basis": "每个阵点对应A原子；菱形原胞内部的B原子与顶点A原子共同构成AB结构",
                "unit_cell": "以四个相邻A原子为顶点的菱形原胞，菱形中心含一个B原子",
                "stoichiometry": "AB",
            },
        },
        output,
    )

    assert output.exists()
    with Image.open(output) as image:
        assert image.width >= 800
        assert image.height >= 700
