from __future__ import annotations

from copy import deepcopy
from typing import Any


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
        "description": "用于晶粒、第二相、孔洞、析出物、晶界、非晶区等组织示意。",
        "required_fields": ["kind", "caption", "features"],
        "optional_fields": ["matrix_label", "scale_note"],
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
