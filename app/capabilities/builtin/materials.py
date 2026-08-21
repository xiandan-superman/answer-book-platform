from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from ..contracts import CapabilityManifest, ExpressionRule, KeywordRule

FIRST_BATCH_SCHEMA_KINDS = (
    "generic_axis_curve",
    "multi_curve_axis_plot",
    "binary_phase_diagram",
    "crystal_unit_cell",
    "crystal_plane_direction",
    "zone_axis_diffraction",
    "xrd_pattern",
    "microstructure_schematic",
    "fe_c_phase_diagram",
    "ttt_diagram",
    "stress_strain_curve",
    "dsc_curve",
    "polymer_chain_structure",
    "ceramic_crystal_structure",
    "sintering_microstructure_evolution",
)


ADDITIONAL_SCHEMA_KINDS = (
    "ternary_phase_diagram",
    "defect_structure_schematic",
    "process_flow_diagram",
    "cct_diagram",
    "heat_treatment_curve",
    "creep_curve",
    "fatigue_sn_curve",
    "dislocation_schematic",
    "slip_system_schematic",
    "precipitation_aging_curve",
    "recrystallization_grain_growth",
    "corrosion_polarization_curve",
    "welding_thermal_cycle",
    "polymer_configuration_conformation",
    "polymer_crystalline_morphology",
    "spherulite_schematic",
    "tga_curve",
    "dma_curve",
    "viscoelastic_creep_curve",
    "stress_relaxation_curve",
    "time_temperature_superposition",
    "polymer_stress_strain_curve",
    "molecular_weight_distribution",
    "polymer_blend_phase_diagram",
    "rheology_flow_curve",
    "silicate_structure_schematic",
    "glass_network_structure",
    "ceramic_phase_diagram",
    "sintering_densification_curve",
    "porous_ceramic_microstructure",
    "defect_chemistry_diagram",
    "ionic_conductivity_arrhenius",
    "dielectric_temperature_curve",
    "ferroelectric_hysteresis_loop",
    "magnetic_hysteresis_loop",
    "fracture_toughness_schematic",
)


MATERIAL_SCHEMA_KINDS = FIRST_BATCH_SCHEMA_KINDS + ADDITIONAL_SCHEMA_KINDS

# Historical name kept for stored plans and callers. These generally useful
# schemas are owned by the core figure pack rather than the materials pack.
GENERIC_SCHEMA_KINDS = ("generic_axis_curve", "multi_curve_axis_plot", "process_flow_diagram")


_REGISTRY: dict[str, dict[str, Any]] = {
    "generic_axis_curve": {
        "schema_id": "generic_axis_curve.v1",
        "kind": "generic_axis_curve",
        "name": "通用坐标曲线",
        "disciplines": ["materials_general"],
        "description": "用于性能-温度、强度-时间、转化率-时间等单曲线坐标图。",
        "required_fields": ["kind", "caption", "x_label", "y_label", "points"],
        "optional_fields": ["title", "annotations"],
        "renderer": "draw_generic_axis_curve",
    },
    "multi_curve_axis_plot": {
        "schema_id": "multi_curve_axis_plot.v1",
        "kind": "multi_curve_axis_plot",
        "name": "多曲线坐标对比图",
        "disciplines": ["materials_general"],
        "description": "用于不同温度、成分、热处理或材料条件下的多曲线对比。",
        "required_fields": ["kind", "caption", "x_label", "y_label", "series"],
        "optional_fields": ["title", "annotations", "legend_title"],
        "renderer": "draw_multi_curve_axis_plot",
    },
    "binary_phase_diagram": {
        "schema_id": "binary_phase_diagram.v1",
        "kind": "binary_phase_diagram",
        "name": "二元相图",
        "disciplines": ["materials_general", "metallic", "ceramic"],
        "description": "用于二元匀晶、共晶、包晶、偏晶等相图示意。",
        "required_fields": ["kind", "caption", "components", "x_label", "y_label", "curves", "phase_regions"],
        "optional_fields": ["invariant_points", "temperature_range", "composition_range"],
        "renderer": "draw_binary_phase_diagram",
    },
    "crystal_unit_cell": {
        "schema_id": "crystal_unit_cell.v1",
        "kind": "crystal_unit_cell",
        "name": "晶胞结构",
        "disciplines": ["materials_general", "metallic", "ceramic"],
        "description": "用于 BCC、FCC、HCP、NaCl、CsCl、金刚石、钙钛矿等晶胞示意。",
        "required_fields": ["kind", "caption", "structure"],
        "optional_fields": ["atom_labels", "highlight_sites", "lattice_parameters"],
        "renderer": "draw_crystal_unit_cell",
    },
    "crystal_plane_direction": {
        "schema_id": "crystal_plane_direction.v1",
        "kind": "crystal_plane_direction",
        "name": "晶面晶向标注",
        "disciplines": ["materials_general", "metallic", "ceramic"],
        "description": "用于 Miller 指数、晶面族、晶向族、滑移面与滑移方向标注。",
        "required_fields": ["kind", "caption", "cell", "planes", "directions"],
        "optional_fields": ["labels"],
        "renderer": "draw_crystal_plane_direction",
    },
    "zone_axis_diffraction": {
        "schema_id": "zone_axis_diffraction.v1",
        "kind": "zone_axis_diffraction",
        "name": "带轴电子衍射花样",
        "disciplines": ["materials_general", "metallic", "ceramic"],
        "description": "用于按带轴定律 hu+kv+lw=0 生成电子衍射斑点与指数标注。",
        "required_fields": ["kind", "caption", "zone_axis", "lattice", "max_index", "label_indices"],
        "optional_fields": ["apply_extinction", "spot_size"],
        "renderer": "draw_zone_axis_diffraction",
    },
    "xrd_pattern": {
        "schema_id": "xrd_pattern.v1",
        "kind": "xrd_pattern",
        "name": "XRD 衍射峰图",
        "disciplines": ["materials_general", "metallic", "polymer", "ceramic"],
        "description": "用于 2θ-强度衍射峰、峰位标注和物相对比。",
        "required_fields": ["kind", "caption", "peaks"],
        "optional_fields": ["x_label", "y_label", "phase_labels"],
        "renderer": "draw_xrd_pattern",
    },
    "microstructure_schematic": {
        "schema_id": "microstructure_schematic.v1",
        "kind": "microstructure_schematic",
        "name": "显微组织示意图",
        "disciplines": ["materials_general", "metallic", "polymer", "ceramic"],
        "description": "用于晶粒、第二相、孔洞、析出物、晶界、层片组织、非晶区等组织示意；features 应给出 label、morphology、distribution，并用 spatial_role 声明 matrix、intragranular、intergranular、boundary_network、dispersed 或 isolated 等空间角色；xy 仅用于没有空间角色时的局部定位。",
        "required_fields": ["kind", "caption", "features"],
        "optional_fields": ["matrix_label", "scale_note", "required_labels"],
        "renderer": "draw_microstructure_schematic",
    },
    "fe_c_phase_diagram": {
        "schema_id": "fe_c_phase_diagram.v1",
        "kind": "fe_c_phase_diagram",
        "name": "铁碳相图",
        "disciplines": ["metallic"],
        "description": "用于 Fe-C 相图关键相区、共析点、共晶点和典型温度线。",
        "required_fields": ["kind", "caption"],
        "optional_fields": ["highlight_points", "highlight_composition"],
        "renderer": "draw_fe_c_phase_diagram",
    },
    "ttt_diagram": {
        "schema_id": "ttt_diagram.v1",
        "kind": "ttt_diagram",
        "name": "等温转变 TTT 曲线",
        "disciplines": ["metallic"],
        "description": "用于珠光体、贝氏体、马氏体等温转变开始/终了曲线。",
        "required_fields": ["kind", "caption"],
        "optional_fields": ["start_curve", "finish_curve", "ms_temperature", "regions"],
        "renderer": "draw_ttt_diagram",
    },
    "stress_strain_curve": {
        "schema_id": "stress_strain_curve.v1",
        "kind": "stress_strain_curve",
        "name": "应力-应变曲线",
        "disciplines": ["metallic", "polymer", "ceramic"],
        "description": "用于弹性段、屈服、加工硬化、颈缩、断裂等力学行为示意。",
        "required_fields": ["kind", "caption"],
        "optional_fields": ["points", "material_type", "labels"],
        "renderer": "draw_stress_strain_curve",
    },
    "dsc_curve": {
        "schema_id": "dsc_curve.v1",
        "kind": "dsc_curve",
        "name": "DSC 曲线",
        "disciplines": ["polymer"],
        "description": "用于 Tg、Tc、Tm、结晶峰、熔融峰和热流方向标注。",
        "required_fields": ["kind", "caption"],
        "optional_fields": ["tg", "tc", "tm", "exo_direction"],
        "renderer": "draw_dsc_curve",
    },
    "polymer_chain_structure": {
        "schema_id": "polymer_chain_structure.v1",
        "kind": "polymer_chain_structure",
        "name": "高分子链结构",
        "disciplines": ["polymer"],
        "description": "用于线型、支化、交联、网状结构以及链段构象示意。",
        "required_fields": ["kind", "caption", "chain_type"],
        "optional_fields": ["repeat_unit_label", "crosslink_density"],
        "renderer": "draw_polymer_chain_structure",
    },
    "ceramic_crystal_structure": {
        "schema_id": "ceramic_crystal_structure.v1",
        "kind": "ceramic_crystal_structure",
        "name": "陶瓷晶体结构",
        "disciplines": ["ceramic"],
        "description": "用于 NaCl、CsCl、萤石、钙钛矿、尖晶石等无机非金属晶体结构。",
        "required_fields": ["kind", "caption", "structure"],
        "optional_fields": ["cation_label", "anion_label"],
        "renderer": "draw_ceramic_crystal_structure",
    },
    "sintering_microstructure_evolution": {
        "schema_id": "sintering_microstructure_evolution.v1",
        "kind": "sintering_microstructure_evolution",
        "name": "烧结组织演化",
        "disciplines": ["ceramic"],
        "description": "用于粉末接触、烧结颈形成、孔隙收缩、晶粒长大过程示意。",
        "required_fields": ["kind", "caption"],
        "optional_fields": ["stages", "pore_labels", "grain_labels"],
        "renderer": "draw_sintering_microstructure_evolution",
    },
}


_ADDITIONAL_SCHEMA_METADATA: dict[str, tuple[str, list[str], str, list[str], list[str], str]] = {
    "ternary_phase_diagram": ("三元相图", ["materials_general", "metallic", "ceramic", "polymer"], "用于三元组分三角坐标、相区、连线和关键点示意。", ["kind", "caption", "components", "phase_regions"], ["tie_lines", "critical_points"], "draw_ternary_phase_diagram"),
    "defect_structure_schematic": ("晶体缺陷结构示意", ["materials_general", "metallic", "ceramic"], "用于点缺陷、位错、层错、晶界、空位、间隙原子等结构缺陷。", ["kind", "caption", "defects"], ["matrix_label"], "draw_defect_structure_schematic"),
    "process_flow_diagram": ("材料工艺流程图", ["materials_general", "metallic", "polymer", "ceramic"], "用于制备、热处理、成形、测试等步骤流程示意。", ["kind", "caption", "steps"], ["arrows", "conditions"], "draw_process_flow_diagram"),
    "cct_diagram": ("连续冷却转变 CCT 曲线", ["metallic"], "用于连续冷却条件下奥氏体分解、转变区和冷却曲线。", ["kind", "caption"], ["cooling_curves", "regions", "ms_temperature"], "draw_cct_diagram"),
    "heat_treatment_curve": ("热处理温度-时间曲线", ["metallic"], "用于退火、正火、淬火、回火、固溶和时效温度制度。", ["kind", "caption"], ["segments", "temperatures", "holding_times"], "draw_heat_treatment_curve"),
    "creep_curve": ("蠕变曲线", ["metallic", "polymer", "ceramic"], "用于初始、稳态、加速蠕变三阶段应变-时间曲线。", ["kind", "caption"], ["points", "stage_labels"], "draw_creep_curve"),
    "fatigue_sn_curve": ("S-N 疲劳曲线", ["metallic", "polymer", "ceramic"], "用于应力幅-循环次数、疲劳极限和寿命区间示意。", ["kind", "caption"], ["points", "fatigue_limit"], "draw_fatigue_sn_curve"),
    "dislocation_schematic": ("位错结构与运动示意", ["metallic", "materials_general"], "用于刃型位错、螺型位错、柏氏矢量和位错滑移。", ["kind", "caption", "dislocation_type"], ["burgers_vector", "slip_direction"], "draw_dislocation_schematic"),
    "slip_system_schematic": ("滑移系示意", ["metallic", "materials_general"], "用于滑移面、滑移方向、外力方向和 Schmid 因子示意。", ["kind", "caption"], ["plane", "direction", "force_direction"], "draw_slip_system_schematic"),
    "precipitation_aging_curve": ("时效强化曲线", ["metallic"], "用于欠时效、峰时效、过时效的硬度/强度-时间曲线。", ["kind", "caption"], ["points", "peak_time"], "draw_precipitation_aging_curve"),
    "recrystallization_grain_growth": ("回复再结晶晶粒长大", ["metallic"], "用于冷变形组织、再结晶形核、晶粒长大组织演化。", ["kind", "caption"], ["stages"], "draw_recrystallization_grain_growth"),
    "corrosion_polarization_curve": ("腐蚀极化曲线", ["metallic"], "用于腐蚀电位、腐蚀电流、钝化区和击穿电位标注。", ["kind", "caption"], ["ecorr", "icorr", "passive_region"], "draw_corrosion_polarization_curve"),
    "welding_thermal_cycle": ("焊接热循环曲线", ["metallic"], "用于焊接热循环、峰值温度、冷却时间和热影响区示意。", ["kind", "caption"], ["peak_temperature", "cooling_time"], "draw_welding_thermal_cycle"),
    "polymer_configuration_conformation": ("高分子构型/构象示意", ["polymer"], "用于等规、间规、无规构型和链段构象差异示意。", ["kind", "caption"], ["configuration", "side_groups"], "draw_polymer_configuration_conformation"),
    "polymer_crystalline_morphology": ("高分子晶态形貌", ["polymer"], "用于晶区、非晶区、片晶、折叠链和球晶结构。", ["kind", "caption"], ["crystalline_regions"], "draw_polymer_crystalline_morphology"),
    "spherulite_schematic": ("高分子球晶示意", ["polymer"], "用于球晶中心、径向片晶、晶核和消光十字示意。", ["kind", "caption"], ["nucleus", "lamellae"], "draw_spherulite_schematic"),
    "tga_curve": ("TGA 热重曲线", ["polymer", "ceramic"], "用于质量保留率-温度、分解阶段和失重率标注。", ["kind", "caption"], ["steps", "residual_mass"], "draw_tga_curve"),
    "dma_curve": ("DMA 动态力学曲线", ["polymer"], "用于储能模量、损耗模量和 tanδ-温度曲线。", ["kind", "caption"], ["series", "tg"], "draw_dma_curve"),
    "viscoelastic_creep_curve": ("黏弹性蠕变-回复曲线", ["polymer"], "用于加载蠕变、卸载回复和残余形变示意。", ["kind", "caption"], ["loading_time", "recovery_time"], "draw_viscoelastic_creep_curve"),
    "stress_relaxation_curve": ("应力松弛曲线", ["polymer"], "用于恒定应变下应力随时间衰减曲线。", ["kind", "caption"], ["points"], "draw_stress_relaxation_curve"),
    "time_temperature_superposition": ("时温等效主曲线", ["polymer"], "用于不同温度曲线平移和主曲线构建示意。", ["kind", "caption"], ["series", "shift_factors"], "draw_time_temperature_superposition"),
    "polymer_stress_strain_curve": ("高分子应力-应变曲线", ["polymer"], "用于塑料、橡胶、纤维的典型拉伸行为对比。", ["kind", "caption"], ["material_type", "points"], "draw_polymer_stress_strain_curve"),
    "molecular_weight_distribution": ("分子量分布曲线", ["polymer"], "用于数均、重均分子量和分布宽度示意。", ["kind", "caption"], ["mn", "mw", "distribution_width"], "draw_molecular_weight_distribution"),
    "polymer_blend_phase_diagram": ("高分子共混相图", ["polymer"], "用于 UCST、LCST、单相区和两相区示意。", ["kind", "caption"], ["phase_behavior"], "draw_polymer_blend_phase_diagram"),
    "rheology_flow_curve": ("流变流动曲线", ["polymer"], "用于黏度-剪切速率、剪切变稀/增稠和屈服流体示意。", ["kind", "caption"], ["flow_type"], "draw_rheology_flow_curve"),
    "silicate_structure_schematic": ("硅酸盐结构示意", ["ceramic"], "用于硅氧四面体、岛状、链状、层状、架状结构。", ["kind", "caption", "structure_type"], ["tetrahedra"], "draw_silicate_structure_schematic"),
    "glass_network_structure": ("玻璃网络结构", ["ceramic"], "用于网络形成体、网络修饰体、桥氧和非桥氧结构示意。", ["kind", "caption"], ["network_formers", "modifiers"], "draw_glass_network_structure"),
    "ceramic_phase_diagram": ("陶瓷相图", ["ceramic"], "用于氧化物、硅酸盐二元或三元相图示意。", ["kind", "caption"], ["components", "phase_regions"], "draw_ceramic_phase_diagram"),
    "sintering_densification_curve": ("烧结致密化曲线", ["ceramic"], "用于密度/收缩率-时间或温度、烧结阶段标注。", ["kind", "caption"], ["points", "stage_labels"], "draw_sintering_densification_curve"),
    "porous_ceramic_microstructure": ("多孔陶瓷组织示意", ["ceramic"], "用于孔隙、晶粒、晶界、连通孔和闭口孔示意。", ["kind", "caption"], ["pore_labels"], "draw_porous_ceramic_microstructure"),
    "defect_chemistry_diagram": ("陶瓷缺陷化学示意", ["ceramic"], "用于氧空位、阳离子空位、间隙离子和缺陷反应示意。", ["kind", "caption"], ["defects"], "draw_defect_chemistry_diagram"),
    "ionic_conductivity_arrhenius": ("离子电导 Arrhenius 曲线", ["ceramic"], "用于 lnσ 或 logσT 对 1/T 的激活能关系。", ["kind", "caption"], ["activation_energy"], "draw_ionic_conductivity_arrhenius"),
    "dielectric_temperature_curve": ("介电常数-温度曲线", ["ceramic"], "用于铁电相变、居里温度和介电峰示意。", ["kind", "caption"], ["curie_temperature"], "draw_dielectric_temperature_curve"),
    "ferroelectric_hysteresis_loop": ("铁电 P-E 电滞回线", ["ceramic"], "用于剩余极化、矫顽场和饱和极化标注。", ["kind", "caption"], ["pr", "ec", "ps"], "draw_ferroelectric_hysteresis_loop"),
    "magnetic_hysteresis_loop": ("磁滞回线", ["ceramic", "metallic"], "用于剩磁、矫顽力、饱和磁化强度标注。", ["kind", "caption"], ["mr", "hc", "ms"], "draw_magnetic_hysteresis_loop"),
    "fracture_toughness_schematic": ("断裂韧性与增韧机制示意", ["ceramic"], "用于裂纹扩展、裂纹偏转、桥联、相变增韧等机制。", ["kind", "caption"], ["crack_path", "toughening_mechanisms"], "draw_fracture_toughness_schematic"),
}


for _kind, (_name, _disciplines, _description, _required, _optional, _renderer) in _ADDITIONAL_SCHEMA_METADATA.items():
    _REGISTRY[_kind] = {
        "schema_id": f"{_kind}.v1",
        "kind": _kind,
        "name": _name,
        "disciplines": _disciplines,
        "description": _description,
        "required_fields": _required,
        "optional_fields": _optional,
        "renderer": _renderer,
    }


# Numeric professional figures are data-driven capabilities.  Their semantic
# payload is required even when an older stored task or an internal caller
# reaches the renderer; renderer-side example numbers are never a valid
# substitute for question/evidence data.
_DATA_DRIVEN_REQUIRED_FIELDS: dict[str, list[str]] = {
    "ttt_diagram": ["kind", "caption", "start_curve", "finish_curve"],
    "stress_strain_curve": ["kind", "caption", "points"],
    "dsc_curve": ["kind", "caption", "points"],
    "sintering_microstructure_evolution": ["kind", "caption", "stages"],
    "cct_diagram": ["kind", "caption", "start_curve", "finish_curve", "cooling_curves"],
    "heat_treatment_curve": ["kind", "caption", "points"],
    "creep_curve": ["kind", "caption", "points"],
    "fatigue_sn_curve": ["kind", "caption", "points"],
    "precipitation_aging_curve": ["kind", "caption", "points"],
    "corrosion_polarization_curve": ["kind", "caption", "points"],
    "welding_thermal_cycle": ["kind", "caption", "points"],
    "tga_curve": ["kind", "caption", "points"],
    "dma_curve": ["kind", "caption", "series"],
    "viscoelastic_creep_curve": ["kind", "caption", "points"],
    "stress_relaxation_curve": ["kind", "caption", "points"],
    "time_temperature_superposition": ["kind", "caption", "series"],
    "polymer_stress_strain_curve": ["kind", "caption", "points"],
    "molecular_weight_distribution": ["kind", "caption", "points"],
    "polymer_blend_phase_diagram": ["kind", "caption", "points"],
    "rheology_flow_curve": ["kind", "caption", "points"],
    "sintering_densification_curve": ["kind", "caption", "points"],
    "ionic_conductivity_arrhenius": ["kind", "caption", "points"],
    "dielectric_temperature_curve": ["kind", "caption", "points"],
}
for _kind, _required_fields in _DATA_DRIVEN_REQUIRED_FIELDS.items():
    _REGISTRY[_kind]["required_fields"] = _required_fields


def get_schema(kind: str) -> dict[str, Any] | None:
    entry = _REGISTRY.get(str(kind or "").strip())
    return deepcopy(entry) if entry else None


def registry_snapshot() -> list[dict[str, Any]]:
    return [deepcopy(_REGISTRY[kind]) for kind in MATERIAL_SCHEMA_KINDS]


def schema_prompt_catalog() -> list[dict[str, Any]]:
    return [
        {
            "schema_id": entry["schema_id"],
            "kind": entry["kind"],
            "name": entry["name"],
            "description": entry["description"],
            "required_fields": entry["required_fields"],
            "optional_fields": entry.get("optional_fields", []),
        }
        for entry in registry_snapshot()
    ]


MATERIAL_KEYWORD_RULES = (
    KeywordRule("zone_axis_diffraction", ("带轴", "电子衍射", "衍射花样", "倒易斑点", "选区电子衍射", "saed")),
    # Measurement/output intent outranks a lattice word such as “体心立方”.
    # Otherwise an XRD-peak task is incorrectly routed to a unit-cell sketch.
    KeywordRule(
        "xrd_pattern",
        ("xrd", "x射线衍射", "x-射线衍射", "x射线粉末衍射", "粉末衍射峰", "衍射峰", "2θ", "2theta"),
        confidence=0.98,
    ),
    KeywordRule("fe_c_phase_diagram", ("铁碳", "fe-c", "fec", "奥氏体", "珠光体", "渗碳体")),
    KeywordRule("ttt_diagram", ("ttt", "等温转变", "c曲线", "珠光体转变", "贝氏体转变")),
    KeywordRule("cct_diagram", ("cct", "连续冷却", "冷却转变")),
    KeywordRule("heat_treatment_curve", ("热处理曲线", "淬火", "回火", "退火", "正火", "固溶", "保温")),
    KeywordRule("creep_curve", ("蠕变", "稳态蠕变", "加速蠕变")),
    KeywordRule("fatigue_sn_curve", ("s-n", "sn曲线", "疲劳曲线", "疲劳极限")),
    KeywordRule("corrosion_polarization_curve", ("极化曲线", "腐蚀电位", "腐蚀电流", "钝化")),
    KeywordRule("welding_thermal_cycle", ("焊接热循环", "热影响区", "焊接峰值温度")),
    KeywordRule("dsc_curve", ("dsc", "差示扫描", "tg", "玻璃化转变", "熔融峰", "结晶峰")),
    KeywordRule("tga_curve", ("tga", "热重", "失重", "质量保留率")),
    KeywordRule("dma_curve", ("dma", "动态力学", "储能模量", "损耗模量", "tanδ")),
    KeywordRule("viscoelastic_creep_curve", ("蠕变回复", "黏弹性蠕变", "回复曲线")),
    KeywordRule("stress_relaxation_curve", ("应力松弛", "松弛曲线")),
    KeywordRule("time_temperature_superposition", ("时温等效", "主曲线", "移位因子")),
    KeywordRule("molecular_weight_distribution", ("分子量分布", "数均分子量", "重均分子量")),
    KeywordRule("rheology_flow_curve", ("流变", "剪切速率", "剪切变稀", "剪切增稠", "黏度")),
    KeywordRule("ionic_conductivity_arrhenius", ("arrhenius", "离子电导", "电导率", "激活能")),
    KeywordRule("dielectric_temperature_curve", ("介电常数", "居里温度", "铁电相变")),
    KeywordRule("ferroelectric_hysteresis_loop", ("电滞回线", "剩余极化", "矫顽场")),
    KeywordRule("magnetic_hysteresis_loop", ("磁滞回线", "剩磁", "矫顽力", "饱和磁化")),
    KeywordRule("polymer_chain_structure", ("高分子链", "线型", "支化", "交联", "网状", "等规", "间规", "无规")),
    KeywordRule("polymer_configuration_conformation", ("构型", "构象", "等规", "间规", "无规")),
    KeywordRule("polymer_crystalline_morphology", ("片晶", "折叠链", "晶区", "非晶区")),
    KeywordRule("spherulite_schematic", ("球晶", "径向片晶", "消光十字")),
    KeywordRule("polymer_blend_phase_diagram", ("高分子共混", "ucst", "lcst", "共混相图")),
    KeywordRule("sintering_microstructure_evolution", ("烧结", "烧结颈", "致密化", "孔隙收缩", "晶粒长大")),
    KeywordRule("sintering_densification_curve", ("致密化曲线", "收缩率", "烧结密度")),
    KeywordRule("ceramic_crystal_structure", ("陶瓷晶体", "钙钛矿", "尖晶石", "萤石", "nacl", "cscl")),
    KeywordRule("silicate_structure_schematic", ("硅酸盐", "硅氧四面体", "层状硅酸盐", "链状硅酸盐")),
    KeywordRule("glass_network_structure", ("玻璃网络", "网络形成体", "网络修饰体", "桥氧", "非桥氧")),
    KeywordRule("porous_ceramic_microstructure", ("多孔陶瓷", "连通孔", "闭口孔")),
    KeywordRule("defect_chemistry_diagram", ("缺陷化学", "氧空位", "阳离子空位")),
    KeywordRule("fracture_toughness_schematic", ("断裂韧性", "裂纹扩展", "裂纹偏转", "桥联增韧", "相变增韧")),
    KeywordRule("dislocation_schematic", ("位错", "柏氏矢量", "刃型位错", "螺型位错")),
    KeywordRule("slip_system_schematic", ("滑移系", "schmid", "滑移方向")),
    KeywordRule("precipitation_aging_curve", ("时效强化", "峰时效", "过时效", "欠时效")),
    KeywordRule("recrystallization_grain_growth", ("再结晶", "回复", "冷变形组织")),
    KeywordRule("crystal_plane_direction", ("晶面", "晶向", "miller", "密勒", "滑移面", "滑移方向")),
    KeywordRule("crystal_unit_cell", ("晶胞", "面心立方", "体心立方", "fcc", "bcc", "hcp", "金刚石")),
    KeywordRule("ternary_phase_diagram", ("三元相图", "三角相图", "三角坐标")),
    KeywordRule("ceramic_phase_diagram", ("陶瓷相图", "氧化物相图", "硅酸盐相图")),
    KeywordRule("binary_phase_diagram", ("二元相图", "共晶", "包晶", "匀晶", "偏晶", "液相线", "固相线")),
    KeywordRule("process_flow_diagram", ("流程图", "工艺流程", "制备流程")),
    KeywordRule("defect_structure_schematic", ("缺陷结构", "空位", "间隙原子", "层错", "晶界")),
    KeywordRule("stress_strain_curve", ("应力", "应变", "屈服", "抗拉强度", "弹性模量", "颈缩")),
    KeywordRule("polymer_stress_strain_curve", ("橡胶应力", "塑料应力", "高分子应力")),
    KeywordRule("microstructure_schematic", ("显微组织", "组织示意", "组织图", "晶粒", "析出物", "第二相", "孔洞", "晶界")),
    KeywordRule("multi_curve_axis_plot", ("多曲线", "对比曲线", "不同温度", "不同成分", "不同时间")),
    KeywordRule("generic_axis_curve", ("曲线", "坐标图", "关系图", "变化图"), confidence=0.75),
)


CRYSTALLOGRAPHIC_INDEX_CONTEXT_MARKERS = (
    "晶面指数",
    "晶向",
    "电子衍射",
    "衍射花样",
    "带轴",
    "xrd",
    "x射线衍射",
    "粉末衍射",
    "diffraction",
    "zone axis",
)

CRYSTALLOGRAPHIC_INDEX_JUDGMENT_MARKERS = (
    "非法",
    "消光",
    "晶带",
    "h+k",
    "h + k",
    "h+k+l",
    "h + k + l",
    "反射条件",
    "衍射条件",
    "不应出现",
    "允许反射",
    "指数错误",
    "标签错误",
    "标准指数",
    "上划线",
    "overbar",
)


def _policy_text(context: Mapping[str, Any]) -> str:
    question_value = context.get("question")
    spec_value = context.get("spec")
    question: dict[str, Any] = question_value if isinstance(question_value, dict) else {}
    spec: dict[str, Any] = spec_value if isinstance(spec_value, dict) else {}
    return " ".join(
        [
            str(context.get("text") or ""),
            str(question.get("stem") or ""),
            " ".join(
                str(item.get("stem") or "")
                for item in question.get("subquestions") or []
                if isinstance(item, dict)
            ),
            str(context.get("caption") or ""),
            str(spec.get("caption") or ""),
            str(spec.get("notes") or ""),
        ]
    ).lower()


def _uses_crystallographic_index_policy(context: Mapping[str, Any]) -> bool:
    return any(marker in _policy_text(context) for marker in CRYSTALLOGRAPHIC_INDEX_CONTEXT_MARKERS)


def _zone_axis_for_context(context: Mapping[str, Any]) -> tuple[int, int, int] | None:
    spec_value = context.get("spec")
    spec: dict[str, Any] = spec_value if isinstance(spec_value, dict) else {}
    raw_axis = spec.get("zone_axis")
    if isinstance(raw_axis, (list, tuple)) and len(raw_axis) >= 3:
        try:
            return int(raw_axis[0]), int(raw_axis[1]), int(raw_axis[2])
        except (TypeError, ValueError):
            pass
    text = _policy_text(context)
    spaced = re.search(r"\[\s*([+-]?\d+)\s*[, ]+\s*([+-]?\d+)\s*[, ]+\s*([+-]?\d+)\s*\]", text)
    compact = re.search(r"\[\s*([+-]?\d)([+-]?\d)([+-]?\d)\s*\]", text)
    match = spaced or compact
    if match:
        return tuple(int(match.group(index)) for index in range(1, 4))  # type: ignore[return-value]
    return None


def _zone_axis_quality_rules(context: Mapping[str, Any]) -> list[str]:
    text = _policy_text(context)
    if not any(token in text for token in ("衍射", "diffraction", "带轴", "zone")):
        return []
    axis = _zone_axis_for_context(context)
    equation = "hu+kv+lw=0"
    if axis is not None:
        u, v, w = axis
        equation = f"h*({u})+k*({v})+l*({w})=0"
    rules = [
        "Draw a periodic reciprocal-lattice spot array derived from the declared zone axis, not a hand-picked list of example spots.",
        f"Validate every labelled (hkl) reflection with the zone-axis law {equation}.",
        "Use a symmetric bounded index range, show the transmitted (000) spot, and label at least two non-origin reflections that pass deterministic validation.",
        "Do not draw reciprocal-vector arrows or coordinate axes unless the question explicitly requests them.",
    ]
    if "体心" in text or "bcc" in text:
        rules.append("For a BCC lattice, retain only reflections with h+k+l even.")
    if "面心" in text or "fcc" in text:
        rules.append("For an FCC lattice, retain only reflections whose h, k, and l indices are all even or all odd.")
    return rules


def materials_drawing_quality_policy(context: Mapping[str, Any]) -> dict[str, Any]:
    text = _policy_text(context)
    rules: list[str] = []
    if any(token in text for token in ("晶面", "晶向", "miller", "米勒", "hkl", "uvw", "衍射", "带轴", "zone")):
        rules.append(
            "For crystallographic plane indices, direction indices, plane families, and direction families, every negative index must use LaTeX overbar notation, for example {10\\bar{1}0} and <11\\bar{2}0>; never use hyphen forms such as {10-10}, <11-20>, (1-10), or [11-2 0]."
        )
    rules.extend(_zone_axis_quality_rules(context))
    if any(token in text for token in ("x射线", "xrd", "粉末衍射")) and any(
        token in text for token in ("体心", "bcc", "有序", "cscl")
    ):
        rules.extend(
            [
                "For BCC powder XRD relative peak positions, use relative N=h^2+k^2+l^2 or sin^2θ proportional to N unless the question explicitly gives lattice constant and wavelength.",
                "Do not invent lattice constants, wavelengths, or absolute 2θ values when the question asks only for relative positions.",
                "For disordered BCC, derive fundamental peaks by enumerating hkl and retaining h+k+l even; do not copy a fixed example list.",
                "For CsCl-type ordering, derive superlattice peaks from odd h+k+l and distinguish them with a black-and-white-safe style.",
                "If the question has two parts, use two panels or clearly separated rows: before ordering and after ordering. Both panels must contain multiple labeled peaks.",
                "Do not use color as the key distinction; use solid versus dashed lines, direct labels, vertical offsets, or subplots.",
            ]
        )
    return {"rules": rules}


def materials_deterministic_figure_spec(context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a materials-owned spec only when every plotted fact is derivable.

    The platform core merely asks capability packs for deterministic proposals;
    all crystallographic vocabulary and selection rules remain isolated here.
    """

    raw_question = context.get("question")
    question: Mapping[str, Any] = raw_question if isinstance(raw_question, Mapping) else {}
    planned_kinds = {str(value or "").strip() for value in context.get("planned_kinds", []) or []}
    text = _policy_text({"question": question})
    qid = str(question.get("question_id") or "question").strip()
    if (
        "xrd_pattern" in planned_kinds
        and any(token in text for token in ("体心立方", "bcc"))
        and any(token in text for token in ("有序", "超结构", "超点阵"))
    ):
        # Relative powder positions use N=h²+k²+l² because no wavelength
        # or lattice parameter is supplied. Enumeration rules are owned by
        # this optional discipline pack, never by the generic figure pipeline.
        # Use one complete, documented window rather than an arbitrary short
        # example.  N<=16 contains the first eight BCC fundamentals and every
        # representable CsCl superlattice position in the same window.
        fundamental = [
            (2, "110"),
            (4, "200"),
            (6, "211"),
            (8, "220"),
            (10, "310"),
            (12, "222"),
            (14, "321"),
            (16, "400"),
        ]
        superlattice = [
            (1, "100"),
            (3, "111"),
            (5, "210"),
            (9, "300/221"),
            (11, "311"),
            (13, "320"),
        ]
        fundamental_peaks = [
            {
                "two_theta": position,
                "intensity": 1.0,
                "label": label,
                "phase": "disordered",
                "style": "-",
                "phase_label": "基本峰",
                "pattern_label": "无序态",
            }
            for position, label in fundamental
        ]
        ordered_peaks = [
            *[
                {
                    "two_theta": position,
                    "intensity": 1.0,
                    "label": label,
                    "phase": "ordered",
                    "style": "-",
                    "phase_label": "基本峰",
                    "pattern_label": "有序态",
                }
                for position, label in fundamental
            ],
            *[
                {
                    "two_theta": position,
                    "intensity": 0.72,
                    "label": label,
                    "phase": "superlattice",
                    "style": "--",
                    "phase_label": "新增超结构峰",
                    "pattern_label": "有序态",
                }
                for position, label in superlattice
            ],
        ]
        return {
            "question_id": qid,
            "figure_id": f"{qid}_xrd_fig_01",
            "kind": "xrd_pattern",
            "caption": "体心立方固溶体无序态与有序化后的 XRD 相对峰位",
            "x_label": r"相对峰位 $N=h^2+k^2+l^2$（$\sin^2\theta\propto N$）",
            "y_label": "状态（峰高不表示强度）",
            "peaks": [*fundamental_peaks, *ordered_peaks],
            "generation_basis": "materials.bcc_cscl_extinction_contract",
            "capability_id": "materials.figures",
        }

    # A planned programmatic schema remains authoritative.  This capability
    # only supplies a missing required field on an existing model-authored
    # spec; it must not create a competing spec after schema planning.
    if str(context.get("purpose") or "") == "hydrate_explicit_spec" and "crystal_unit_cell" in planned_kinds:
        structure = ""
        structure_label = ""
        if "面心立方" in text or re.search(r"\bfcc\b", text):
            structure, structure_label = "fcc", "面心立方"
        elif "体心立方" in text or re.search(r"\bbcc\b", text):
            structure, structure_label = "bcc", "体心立方"
        if structure:
            return {
                "question_id": qid,
                "figure_id": f"{qid}_unit_cell_fig_01",
                "kind": "crystal_unit_cell",
                "caption": f"{structure_label}晶胞结构示意图",
                "structure": structure,
                "generation_basis": "materials.explicit_lattice_type_contract",
                "capability_id": "materials.figures",
            }
    return None


def _xrd_hkl_labels(value: Any) -> set[str]:
    labels: set[str] = set()
    for body in re.findall(r"\(([^()]*)\)", str(value or "")):
        labels.update(re.findall(r"(?<!\d)(\d{3})(?!\d)", body))
    return labels


def materials_content_quality_policy(context: Mapping[str, Any]) -> dict[str, Any]:
    """Cross-check user-visible XRD claims against the active final figure.

    The final programmatic spec may supersede a model-authored draft spec.  A
    raster-only visual reviewer cannot reliably compare every crystallographic
    label, so this capability performs the comparison on structured data.
    """

    question_value = context.get("question")
    fragment_value = context.get("fragment")
    question: Mapping[str, Any] = question_value if isinstance(question_value, Mapping) else {}
    fragment: Mapping[str, Any] = fragment_value if isinstance(fragment_value, Mapping) else {}
    text = _policy_text({"question": question})
    if not any(token in text for token in ("xrd", "x射线", "粉末衍射", "衍射峰")):
        return {"issues": []}

    visible_parts = [str(fragment.get("answer") or ""), str(fragment.get("answer_summary") or "")]
    for unit in fragment.get("answer_units", []) or []:
        if not isinstance(unit, Mapping):
            continue
        visible_parts.append(str(unit.get("answer") or ""))
        visible_parts.extend(
            str(segment.get("text") or "")
            for segment in unit.get("analysis_segments", []) or []
            if isinstance(segment, Mapping)
        )
    for block in fragment.get("blocks", []) or []:
        if not isinstance(block, Mapping) or str(block.get("label") or "").strip() == "教材依据":
            continue
        visible_parts.extend(
            str(segment.get("text") or "")
            for segment in block.get("segments", []) or []
            if isinstance(segment, Mapping) and segment.get("type") == "text"
        )
    visible_text = "\n".join(visible_parts)

    active_specs = [
        spec
        for spec in context.get("active_figure_specs", []) or []
        if isinstance(spec, Mapping) and str(spec.get("kind") or "") == "xrd_pattern"
    ]
    active_labels = {
        label
        for spec in active_specs
        for peak in spec.get("peaks", []) or []
        if isinstance(peak, Mapping)
        for label in _xrd_hkl_labels(peak.get("label"))
    }
    declared_labels = _xrd_hkl_labels(visible_text)
    missing_labels = sorted(declared_labels - active_labels) if active_specs else []
    issues: list[dict[str, str]] = []
    if missing_labels:
        issues.append(
            {
                "code": "xrd_figure_text_label_mismatch",
                "message": "解析正文声明的衍射峰未全部出现在最终有效图中："
                + "、".join(f"({label})" for label in missing_labels)
                + "。应统一最终图与文字的峰表范围。",
            }
        )
    if re.search(r"峰间距[^\u3002；;\n]{0,18}(?:逐渐|随)[^\u3002；;\n]{0,12}(?:增大|减小|变大|变小)", visible_text):
        issues.append(
            {
                "code": "xrd_unsupported_peak_spacing_trend",
                "message": "题目未给出波长与点阵常数时，只能稳健给出 N=h²+k²+l² 或 sin²θ 的相对次序；不应额外声明 2θ 峰间距必然单调变大或变小。",
            }
        )
    return {"issues": issues}


def materials_visual_understanding_policy(context: Mapping[str, Any]) -> dict[str, Any]:
    text = _policy_text(context)
    image_schema: dict[str, Any] = {}
    rules: list[str] = []
    if any(token in text for token in ("相图", "phase diagram", "液相线", "固相线", "共晶", "包晶")):
        image_schema.update(
            {
                "invariant_horizontal_lines": [
                    {
                        "y_value": "visible value or approximate range",
                        "x_start": "visible horizontal-line start",
                        "x_end": "visible horizontal-line end",
                        "regions_immediately_above": ["labels touching the line from above"],
                        "regions_immediately_below": ["labels touching the line from below"],
                        "visible_basis": "short description of the pixels/geometry used",
                        "confidence": 0.0,
                    }
                ],
                "fixed_condition_phase_paths": [
                    {
                        "condition": "visible fixed-condition label or value",
                        "direction": "x_min_to_x_max",
                        "terminal_regions": {
                            "x_min": {"side_label": "left endpoint label", "phase_or_region": "visible phase"},
                            "x_max": {"side_label": "right endpoint label", "phase_or_region": "visible phase"},
                        },
                        "ordered_regions": [{"x_range": "visible range", "phase_or_region": "visible phase/region"}],
                        "confidence": 0.0,
                    }
                ],
            }
        )
        rules.extend(
            [
                "For a phase diagram, extract every visibly horizontal invariant line into invariant_horizontal_lines. A curved liquidus maximum or two sloped curves meeting at a peak is not a horizontal invariant line.",
                "For a phase diagram, report only visible line geometry and the phase-region labels immediately touching each line. Do not name reaction types and do not write reaction equations in answer_relevant_observations.",
                "For a requested fixed temperature, state which labeled single-phase regions the horizontal isotherm visibly intersects. Do not add a phase whose field lies entirely above or below that temperature.",
                "For every requested fixed temperature/pressure/composition path, also return fixed_condition_phase_paths with explicit endpoint labels and terminal phases/regions. Keep single- and two-phase regions distinct.",
            ]
        )
    if any(token in text for token in ("晶胞", "unit cell", "面心立方", "体心立方", "fcc", "bcc")):
        image_schema["unit_cell_site_families"] = [
            {
                "site_family": "corner|face_center|edge_center|body_center|other",
                "species_or_color": "visible species/color",
                "visible_count": 0,
                "crystallographic_count_per_conventional_cell": 0.0,
                "representative_coordinates": ["(0,0,0)"],
                "visible_basis": "which spheres/marks establish this family",
                "confidence": 0.0,
            }
        ]
        rules.extend(
            [
                "For a crystal unit-cell image, enumerate corner, face-center, edge-center, and body-center sites separately in unit_cell_site_families. Count visible spheres before naming a lattice.",
                "Perspective overlap is not evidence of a face-center or edge-center site. A sphere at a projected face location may be a rear corner; use connecting cube edges, occlusion, symmetry, and the question's stated coordinates. Put ambiguity in uncertainties rather than inventing a site family.",
                "For a crystal unit-cell image, do not solve the Bravais-lattice question in answer_relevant_observations; report site families and coordinates only.",
            ]
        )
    return {"image_schema": image_schema, "hard_rules": rules}


def normalize_materials_visual_understanding(context: Mapping[str, Any]) -> dict[str, Any]:
    value = context.get("value")
    merged: dict[str, Any] = deepcopy(value) if isinstance(value, dict) else {}
    if "相图" not in _policy_text(context):
        return merged
    solution_markers = re.compile(r"(?:包晶|共晶|共析|偏晶|包析|(?:->|→|⇌|\\rightleftharpoons|\\to))")
    for image in merged.get("images", []) or []:
        if not isinstance(image, dict):
            continue
        observations = [str(item) for item in image.get("answer_relevant_observations", []) or []]
        removed = [item for item in observations if solution_markers.search(item)]
        image["answer_relevant_observations"] = [item for item in observations if item not in removed]
        if removed:
            image.setdefault("uncertainties", []).append(
                "题面视觉解析曾返回反应类型或方程；已按政策剔除，仅保留可见几何事实供正确性层判断。"
            )
    return merged


def materials_visual_qa_policy(context: Mapping[str, Any]) -> dict[str, Any]:
    text = _policy_text(context)
    spec_value = context.get("spec")
    spec: dict[str, Any] = spec_value if isinstance(spec_value, dict) else {}
    kind = str(spec.get("kind") or "").strip()
    whitelist = _uses_crystallographic_index_policy(context)
    rules: list[str] = []
    if whitelist:
        rules.extend(
            [
                "This figure contains crystallographic plane/direction indices or diffraction indexing.",
                "Do not judge any hkl, uvw, zone-axis, extinction, reflection, or indexing label as physically illegal, wrong, or missing based on the raster image.",
                "For crystallographic labels, report only directly visible readability defects such as blur, clipping, or overlap. If uncertain, do not report an index issue.",
            ]
        )
    elif kind == "zone_axis_diffraction":
        rules.extend(_zone_axis_quality_rules(context))
    elif kind == "xrd_pattern":
        rules.extend(
            [
                "For disordered BCC powder XRD, allowed fundamental peaks satisfy h+k+l even.",
                "For CsCl-type ordering, new superlattice peaks occur at odd h+k+l positions and should be visually distinguished from fundamental peaks.",
            ]
        )
    return {"hard_rules": rules, "crystallographic_index_whitelist": whitelist, "text": text}


def filter_materials_visual_qa(context: Mapping[str, Any]) -> dict[str, Any]:
    value = context.get("value")
    qa: dict[str, Any] = deepcopy(value) if isinstance(value, dict) else {}
    spec_value = context.get("spec")
    spec: dict[str, Any] = spec_value if isinstance(spec_value, dict) else {}
    deterministic_kind = str(spec.get("kind") or "") in {"zone_axis_diffraction", "xrd_pattern"}
    deterministic_passed = context.get("deterministic_validation_passed") is True
    if (
        qa.get("error")
        or not _uses_crystallographic_index_policy(context)
        or not deterministic_kind
        or not deterministic_passed
    ):
        return qa
    removed: list[dict[str, str]] = []
    remaining_issue_count = 0
    for field in ("missing_requirements", "label_issues", "visual_issues"):
        raw_values = qa.get(field)
        values: list[Any] = raw_values if isinstance(raw_values, list) else []
        kept: list[Any] = []
        for value in values:
            issue_text = str(value or "")
            if any(marker in issue_text.lower() for marker in CRYSTALLOGRAPHIC_INDEX_JUDGMENT_MARKERS):
                removed.append({"field": field, "issue": issue_text})
            else:
                kept.append(value)
        qa[field] = kept
        remaining_issue_count += len(kept)
    if not removed:
        return qa
    qa["crystallographic_index_whitelist"] = {
        "applied": True,
        "reason": "晶面/晶向/电子衍射指数的物理合法性不由视觉 OCR 判定。",
        "suppressed_issues": removed,
    }
    if qa.get("ok") is not True and remaining_issue_count == 0:
        qa["ok"] = True
        qa["summary"] = "未发现可由视觉审查直接确认的排版或图像问题；晶体学指数合法性由程序规则校验。"
    return qa


def materials_drawing_repair_policy(context: Mapping[str, Any]) -> dict[str, Any]:
    text = _policy_text(context)
    constraints: list[str] = []
    if any(token in text for token in ("衍射", "带轴", "diffraction", "zone")):
        constraints.extend(_zone_axis_quality_rules(context))
    if any(token in text for token in ("x射线", "xrd", "粉末衍射")) and any(
        token in text for token in ("体心", "bcc", "有序")
    ):
        constraints.extend(
            [
                "XRD 峰图应避免相邻峰标签重叠；对密集峰可交错上下标注、旋转标签或增加画布宽度。",
                "不能用颜色区分关键含义；用实线/虚线、圆点/菱形、上下分图或直接文字说明区分。",
            ]
        )
    return {"constraints": constraints}


def materials_answer_generation_policy(context: Mapping[str, Any]) -> dict[str, Any]:
    text = _policy_text(context)
    rules: list[str] = []
    if any(token in text for token in ("相图", "杠杆定律", "液相线", "固相线", "共晶", "包晶")):
        rules.extend(
            [
                "For phase-diagram lever-rule calculations, distinguish the requested stage explicitly: phase fractions at room temperature use the room-temperature tie-line endpoint compositions, while primary-structure versus eutectic/peritectic structure fractions use the invariant-reaction temperature endpoints immediately before that reaction. Never reuse room-temperature solubility endpoints to calculate invariant-reaction structure fractions.",
                "For phase-diagram calculations, preserve each composition read from the diagram in formulas/steps with its physical meaning (alloy composition, phase endpoint, eutectic/peritectic composition); do not silently substitute a different-stage endpoint.",
                "When a phase-diagram cooling path crosses a solubility boundary below an invariant reaction, explicitly decide whether secondary precipitation changes the requested phase/structure constituents. Do not state that a primary solid transforms wholly into one product unless the diagram or confirmed evidence justifies that simplification.",
            ]
        )
    if any(token in text for token in ("晶面", "晶向", "miller", "密勒", "hkl", "uvw", "带轴", "衍射")):
        rules.extend(
            [
                "Do not put crystallographic labels into formulas only to satisfy formula rules. Miller indices, zone-axis indices, and diffraction peak labels should stay in normal text, symbolic_notations, figure_specs.required_labels, or drawing-code labels unless they are part of an actual relation or criterion.",
                "For crystallographic plane indices, direction indices, plane families, and direction families, every negative index must use LaTeX overbar notation. Write {10\\bar{1}0} and <11\\bar{2}0>; never write hyphen forms such as {10-10}, <11-20>, (1-10), or [11-2 0].",
            ]
        )
    if any(token in text for token in ("x射线", "xrd", "粉末衍射")):
        rules.extend(
            [
                "When wavelength and lattice parameter are absent, state and draw peak positions only on a relative N=h^2+k^2+l^2 or sin^2(theta) scale. Do not invent absolute 2theta values or a monotonic 2theta-spacing trend.",
                "Every hkl peak explicitly enumerated in the answer or analysis must appear in the requested final figure within the same declared plotting window.",
            ]
        )
    if any(token in text for token in ("显微组织", "组织示意", "晶粒", "析出物", "第二相", "晶界")):
        rules.append(
            "For microstructure_schematic, every feature must include label, morphology, distribution, and spatial_role. Use morphology values matrix, grain, boundary_network, lamellar_colony, particles, island, or dendrite. Use spatial_role values matrix, intragranular, intergranular, boundary_network, dispersed, or isolated to declare field relationships; xy is only an optional local hint when no spatial relationship applies."
        )
    return {
        "hard_rules": rules,
        "symbolic_notations_guidance": "材料专业符号、图示标签、晶面/晶向指数或族、相区名、坐标轴标签或单位说明；负指数用 LaTeX 上横线，例如 {10\\bar{1}0}、<11\\bar{2}0>。",
    }


def normalize_materials_formula_retrieval(context: Mapping[str, Any]) -> str:
    """Canonicalize commutative zone-law products only in materials context."""

    value = str(context.get("value") or "")
    return re.sub(
        r"(?<![a-z])([hkluvw]{2})(?![a-z])",
        lambda match: "".join(sorted(match.group(1))),
        value,
    )


def materials_notation_formula_policy(context: Mapping[str, Any]) -> dict[str, bool]:
    meaning = str(context.get("meaning") or "")
    return {
        "is_notation": bool(re.search(r"(晶面|晶向|米勒|相区|相名|峰位|衍射指数)", meaning)),
    }


def materials_retrieval_source_policy(context: Mapping[str, Any]) -> dict[str, bool]:
    query = str(context.get("query") or context.get("text") or "").lower()
    return {
        "figure_query": bool(re.search(r"(相图|组织|衍射|晶胞|峰|斑点|形貌|晶粒)", query)),
    }


def materials_exam_image_hint_policy(context: Mapping[str, Any]) -> dict[str, bool]:
    text = str(context.get("text") or "").lower()
    return {
        "has_image_hint": bool(re.search(r"(衍射花样|标出.*斑点|组织图|晶胞图|相图)", text)),
    }


MATERIALS_CAPABILITY = CapabilityManifest(
    capability_id="materials.figures",
    version="1.0",
    name="材料科学图形",
    description="材料、冶金、高分子与陶瓷题目的内置结构化图形能力。",
    schemas=tuple(_REGISTRY[kind] for kind in MATERIAL_SCHEMA_KINDS if kind not in GENERIC_SCHEMA_KINDS),
    keyword_rules=tuple(rule for rule in MATERIAL_KEYWORD_RULES if rule.schema_kind not in GENERIC_SCHEMA_KINDS),
    expression_rules=(
        ExpressionRule(
            "materials.crystal_plane_index",
            "domain_notation",
            r"\(\s*[\-−]?\d+\s+[\-−]?\d+\s+[\-−]?\d+\s*\)",
            source_format="text",
            context_keywords=("晶面", "密勒", "miller", "衍射"),
            confidence=0.95,
            priority=70,
        ),
        ExpressionRule(
            "materials.crystal_direction_index",
            "domain_notation",
            r"\[\s*[\-−]?\d+\s+[\-−]?\d+\s+[\-−]?\d+\s*\]",
            source_format="text",
            context_keywords=("晶向", "晶带轴", "密勒", "miller", "衍射"),
            confidence=0.95,
            priority=70,
        ),
        ExpressionRule(
            "materials.fractional_crystal_coordinate",
            "domain_notation",
            r"(?<!\d)\d+\s*/\s*\d+(?!\d)",
            source_format="text",
            context_keywords=("坐标", "位置", "晶胞", "阵点", "原子"),
            confidence=0.95,
            priority=75,
        ),
        ExpressionRule(
            "materials.zone_or_extinction_relation",
            "domain_notation",
            r"[hkl](?:\s*[+＋]\s*[hkl])+(?:\s*=\s*(?:偶数|奇数|0|零))?",
            source_format="text",
            context_keywords=("晶带", "带轴", "衍射", "消光", "反射"),
            confidence=0.98,
            priority=80,
        ),
        ExpressionRule(
            "materials.phase_or_constituent_label",
            "domain_notation",
            r"(?:Fe-Fe3?C|Fe3?C|[Α-Ωα-ωL])(?:_?(?:I{1,3}|[一-鿿]+))?",
            source_format="text",
            context_keywords=("相图", "相区", "组织", "渗碳体", "相名", "初生", "二次相"),
            confidence=0.94,
            priority=90,
        ),
    ),
    prompt_context="根据题目本身选择适合的专业图形；材料科学只是可用能力之一，不是平台的学科边界。",
    policy_hooks={
        "deterministic_figure_spec": materials_deterministic_figure_spec,
        "drawing_quality": materials_drawing_quality_policy,
        "visual_understanding": materials_visual_understanding_policy,
        "normalize_visual_understanding": normalize_materials_visual_understanding,
        "visual_qa": materials_visual_qa_policy,
        "filter_visual_qa": filter_materials_visual_qa,
        "drawing_repair": materials_drawing_repair_policy,
        "answer_generation": materials_answer_generation_policy,
        "content_quality": materials_content_quality_policy,
        "formula_retrieval_normalization": normalize_materials_formula_retrieval,
        "notation_formula_classification": materials_notation_formula_policy,
        "retrieval_source_routing": materials_retrieval_source_policy,
        "exam_image_hint": materials_exam_image_hint_policy,
    },
)
