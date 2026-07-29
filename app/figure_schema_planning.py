from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .figure_schema_registry import get_schema, registry_snapshot, schema_prompt_catalog
from .llm_client import LLMError, OpenAICompatibleClient
from .question_types import question_has_type
from .settings import DEFAULT_MODEL_MAX_TOKENS


SCHEMA_VERSION = "answer_book.figure_schema_plan.v1"


_KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("zone_axis_diffraction", ("带轴", "电子衍射", "衍射花样", "倒易斑点", "选区电子衍射", "saed")),
    ("xrd_pattern", ("xrd", "x射线衍射", "x-射线衍射", "衍射峰", "2θ", "2theta")),
    ("fe_c_phase_diagram", ("铁碳", "fe-c", "fec", "奥氏体", "珠光体", "渗碳体")),
    ("ttt_diagram", ("ttt", "等温转变", "c曲线", "珠光体转变", "贝氏体转变")),
    ("cct_diagram", ("cct", "连续冷却", "冷却转变")),
    ("heat_treatment_curve", ("热处理曲线", "淬火", "回火", "退火", "正火", "固溶", "保温")),
    ("creep_curve", ("蠕变", "稳态蠕变", "加速蠕变")),
    ("fatigue_sn_curve", ("s-n", "sn曲线", "疲劳曲线", "疲劳极限")),
    ("corrosion_polarization_curve", ("极化曲线", "腐蚀电位", "腐蚀电流", "钝化")),
    ("welding_thermal_cycle", ("焊接热循环", "热影响区", "焊接峰值温度")),
    ("dsc_curve", ("dsc", "差示扫描", "tg", "玻璃化转变", "熔融峰", "结晶峰")),
    ("tga_curve", ("tga", "热重", "失重", "质量保留率")),
    ("dma_curve", ("dma", "动态力学", "储能模量", "损耗模量", "tanδ")),
    ("viscoelastic_creep_curve", ("蠕变回复", "黏弹性蠕变", "回复曲线")),
    ("stress_relaxation_curve", ("应力松弛", "松弛曲线")),
    ("time_temperature_superposition", ("时温等效", "主曲线", "移位因子")),
    ("molecular_weight_distribution", ("分子量分布", "数均分子量", "重均分子量")),
    ("rheology_flow_curve", ("流变", "剪切速率", "剪切变稀", "剪切增稠", "黏度")),
    ("ionic_conductivity_arrhenius", ("arrhenius", "离子电导", "电导率", "激活能")),
    ("dielectric_temperature_curve", ("介电常数", "居里温度", "铁电相变")),
    ("ferroelectric_hysteresis_loop", ("电滞回线", "剩余极化", "矫顽场")),
    ("magnetic_hysteresis_loop", ("磁滞回线", "剩磁", "矫顽力", "饱和磁化")),
    ("polymer_chain_structure", ("高分子链", "线型", "支化", "交联", "网状", "等规", "间规", "无规")),
    ("polymer_configuration_conformation", ("构型", "构象", "等规", "间规", "无规")),
    ("polymer_crystalline_morphology", ("片晶", "折叠链", "晶区", "非晶区")),
    ("spherulite_schematic", ("球晶", "径向片晶", "消光十字")),
    ("polymer_blend_phase_diagram", ("高分子共混", "ucst", "lcst", "共混相图")),
    ("sintering_microstructure_evolution", ("烧结", "烧结颈", "致密化", "孔隙收缩", "晶粒长大")),
    ("sintering_densification_curve", ("致密化曲线", "收缩率", "烧结密度")),
    ("ceramic_crystal_structure", ("陶瓷晶体", "钙钛矿", "尖晶石", "萤石", "nacl", "cscl")),
    ("silicate_structure_schematic", ("硅酸盐", "硅氧四面体", "层状硅酸盐", "链状硅酸盐")),
    ("glass_network_structure", ("玻璃网络", "网络形成体", "网络修饰体", "桥氧", "非桥氧")),
    ("porous_ceramic_microstructure", ("多孔陶瓷", "连通孔", "闭口孔")),
    ("defect_chemistry_diagram", ("缺陷化学", "氧空位", "阳离子空位")),
    ("fracture_toughness_schematic", ("断裂韧性", "裂纹扩展", "裂纹偏转", "桥联增韧", "相变增韧")),
    ("dislocation_schematic", ("位错", "柏氏矢量", "刃型位错", "螺型位错")),
    ("slip_system_schematic", ("滑移系", "schmid", "滑移方向")),
    ("precipitation_aging_curve", ("时效强化", "峰时效", "过时效", "欠时效")),
    ("recrystallization_grain_growth", ("再结晶", "回复", "冷变形组织")),
    ("crystal_plane_direction", ("晶面", "晶向", "miller", "密勒", "滑移面", "滑移方向")),
    ("crystal_unit_cell", ("晶胞", "面心立方", "体心立方", "fcc", "bcc", "hcp", "金刚石")),
    ("ternary_phase_diagram", ("三元相图", "三角相图", "三角坐标")),
    ("ceramic_phase_diagram", ("陶瓷相图", "氧化物相图", "硅酸盐相图")),
    ("binary_phase_diagram", ("二元相图", "共晶", "包晶", "匀晶", "偏晶", "液相线", "固相线")),
    ("process_flow_diagram", ("流程图", "工艺流程", "制备流程")),
    ("defect_structure_schematic", ("缺陷结构", "空位", "间隙原子", "层错", "晶界")),
    ("stress_strain_curve", ("应力", "应变", "屈服", "抗拉强度", "弹性模量", "颈缩")),
    ("polymer_stress_strain_curve", ("橡胶应力", "塑料应力", "高分子应力")),
    ("microstructure_schematic", ("显微组织", "组织示意", "晶粒", "析出物", "第二相", "孔洞", "晶界")),
    ("multi_curve_axis_plot", ("多曲线", "对比曲线", "不同温度", "不同成分", "不同时间")),
    ("generic_axis_curve", ("曲线", "坐标图", "关系图", "变化图")),
)


def _question_text(question: dict[str, Any]) -> str:
    chunks = [str(question.get("stem") or ""), str(question.get("section") or ""), str(question.get("section_raw") or "")]
    for sub in question.get("subquestions") or []:
        if not isinstance(sub, dict):
            continue
        chunks.append(str(sub.get("stem") or ""))
        for req in sub.get("requirements") or []:
            if isinstance(req, dict):
                chunks.append(str(req.get("stem") or ""))
    return "\n".join(chunks)


def infer_schema_kind_locally(question: dict[str, Any]) -> tuple[str, str]:
    text = _question_text(question)
    lowered = text.lower()
    for kind, keywords in _KIND_KEYWORDS:
        for keyword in keywords:
            if keyword.lower() in lowered:
                return kind, f"题面包含“{keyword}”，匹配 {kind} schema。"
    return "generic_axis_curve", "未命中特定专业图关键词，使用通用坐标曲线 schema 作为可渲染兜底。"


def _model_plan_prompt(question: dict[str, Any]) -> list[dict[str, Any]]:
    payload = {
        "task": "plan_professional_figure_schema",
        "hard_rules": [
            "Only return one valid JSON object.",
            "Only choose a kind from available_schema_registry when it satisfies the drawing need.",
            "If no registry schema matches, return status schema_proposed and provide proposed_kind plus schema_proposal.",
            "Do not solve the question. Do not generate figure_specs parameters. Only plan the schema.",
        ],
        "question": {
            "question_id": question.get("question_id", ""),
            "question_type": question.get("question_type", ""),
            "stem": question.get("stem", ""),
            "subquestions": question.get("subquestions", []),
        },
        "available_schema_registry": schema_prompt_catalog(),
        "output_schema": {
            "professional_diagram_type": "kind from registry or proposed kind",
            "reason": "why this schema is needed",
            "schema_resolution": {
                "status": "schema_found | schema_proposed",
                "kind": "registry kind when found",
                "schema_id": "schema id when found",
                "schema_proposal": {},
            },
        },
    }
    return [
        {"role": "system", "content": "你是材料科学与工程真题专业作图 schema 规划器。只输出 JSON。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _resolve_with_model(question: dict[str, Any], provider: Any, model: str) -> dict[str, Any] | None:
    if provider is None or not getattr(provider, "api_key", ""):
        return None
    client = OpenAICompatibleClient(provider)
    try:
        result = client.chat_json_object(_model_plan_prompt(question), model=model or getattr(provider, "default_model", ""), max_tokens=DEFAULT_MODEL_MAX_TOKENS)
    except (LLMError, Exception):
        return None
    if not isinstance(result, dict):
        return None
    resolution = result.get("schema_resolution") if isinstance(result.get("schema_resolution"), dict) else {}
    kind = str(resolution.get("kind") or result.get("professional_diagram_type") or "").strip()
    entry = get_schema(kind)
    if entry:
        return {
            "kind": entry["kind"],
            "reason": str(result.get("reason") or f"模型选择 {entry['kind']} schema。"),
            "schema_resolution": {
                "status": "schema_found",
                "schema_id": entry["schema_id"],
                "kind": entry["kind"],
                "renderer": entry["renderer"],
                "schema_source": "registry",
                "selected_by": "model",
            },
        }
    proposal = resolution.get("schema_proposal") if isinstance(resolution.get("schema_proposal"), dict) else {}
    proposed_kind = str(resolution.get("proposed_kind") or result.get("professional_diagram_type") or "").strip()
    if proposed_kind or proposal:
        return {
            "kind": proposed_kind,
            "reason": str(result.get("reason") or "模型认为现有 registry 未覆盖该专业图。"),
            "schema_resolution": {
                "status": "schema_proposed",
                "proposed_kind": proposed_kind,
                "requires_renderer_creation": True,
                "schema_source": "model_proposal",
                "schema_proposal": proposal,
            },
        }
    return None


def _plan_one(question: dict[str, Any], provider: Any | None = None, model: str = "") -> dict[str, Any] | None:
    qid = str(question.get("question_id") or "").strip()
    if not qid or not question_has_type(question, "作图题"):
        return None
    resolved = _resolve_with_model(question, provider, model) if provider is not None else None
    selected_by = "model"
    if resolved is None:
        kind, reason = infer_schema_kind_locally(question)
        entry = get_schema(kind)
        selected_by = "local_keyword"
        if entry:
            resolved = {
                "kind": entry["kind"],
                "reason": reason,
                "schema_resolution": {
                    "status": "schema_found",
                    "schema_id": entry["schema_id"],
                    "kind": entry["kind"],
                    "renderer": entry["renderer"],
                    "schema_source": "registry",
                    "selected_by": selected_by,
                },
            }
        else:
            safe_kind = re.sub(r"[^a-z0-9_]+", "_", kind.lower()).strip("_") or "unregistered_diagram"
            resolved = {
                "kind": safe_kind,
                "reason": reason,
                "schema_resolution": {
                    "status": "schema_proposed",
                    "proposed_kind": safe_kind,
                    "requires_renderer_creation": True,
                    "schema_source": "local_proposal",
                    "schema_proposal": {},
                },
            }
    return {
        "question_id": qid,
        "confirmed_question_type": question.get("question_type") or question.get("confirmed_question_type") or "",
        "diagram_intent": {
            "needs_figure": True,
            "professional_diagram_type": resolved["kind"],
            "reason": resolved["reason"],
        },
        "schema_resolution": resolved["schema_resolution"],
    }


def plan_figure_schemas(
    structured_exam: dict[str, Any],
    output_json: Path,
    *,
    provider: Any | None = None,
    model: str = "",
) -> dict[str, Any]:
    items = []
    for question in structured_exam.get("items") or []:
        if not isinstance(question, dict):
            continue
        plan = _plan_one(question, provider=provider, model=model)
        if plan:
            items.append(plan)
    report = {
        "schema_version": SCHEMA_VERSION,
        "planned_count": len(items),
        "items": items,
        "registry": registry_snapshot(),
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def attach_figure_schema_plans(structured_exam: dict[str, Any], plan_report: dict[str, Any]) -> dict[str, Any]:
    plans_by_id = {
        str(item.get("question_id") or "").strip(): item
        for item in plan_report.get("items", []) or []
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    }
    for question in structured_exam.get("items") or []:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        if qid in plans_by_id:
            question["figure_schema_plan"] = plans_by_id[qid]
    return structured_exam
