from __future__ import annotations

from ..contracts import CapabilityManifest, ExpressionRule

FORMULA_CHARACTERS = r"A-Za-zΑ-ω∂δΔ∆ΘΓΛΣΠΩ0-9_{}^()[\],±≈≠≤≥∝→⇌°ºᵒθ₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻+\-*/|·×\\'外内常数\s"
TEXT_ELECTRODE_PATTERN = r"（[^（），。；：!?！？、\n]{1,180}\|[^（），。；：!?！？、\n]{1,180}）"
TEXT_EQUATION_PATTERN = (
    rf"(?<![A-Za-z0-9_$])(?:[（(]?[∂δΔ∆A-Za-zΑ-ωΘΓΛΣΠΩ])[{FORMULA_CHARACTERS}]{{0,180}}"
    rf"(?:=|≈|≠|≤|≥|∝|→|⇌)[{FORMULA_CHARACTERS}.]{{1,180}}"
)
TEXT_THERMODYNAMIC_PATTERN = (
    r"(?<![A-Za-z])(?:"
    r"(?:[∂](?:[Δ∆])?r?[GHUSAF](?:p|v|m|,m)*[θ°ºᵒ]?(?:\s*/\s*∂[A-Za-z])?)|"
    r"(?:[Δ∆](?:_?[rR])?\s*(?:[GHUSAF](?:p|v|m|,m)*(?:_(?:iso|sys|amb|总|隔离|系统|环境))?|C(?:_[{]?[pPvV](?:,m)?[}]?|[pPvV],?m?))(?:\s*(?:\^\s*\{?\s*(?:o|O|\\circ|θ|\\theta)\s*\}?|[°ºᵒθ]))?)|"
    r"(?:C(?:_[{]?[pPvV](?:,m)?[}]?|[pPvV],?m?))(?:\s*(?:\^\s*\{?\s*(?:o|O|\\circ|θ|\\theta)\s*\}?|[°ºᵒθ]))?|"
    r"(?:[GHUSAFKE])(?:\s*(?:\^\s*\{?\s*(?:o|O|\\circ|θ|\\theta)\s*\}?|[°ºᵒθ]))|"
    r"(?:pV|RT|nRT|nF|zF|Kp|Ksp|Ka|Kb)"
    r")"
)
TEXT_SYMBOLIC_TOKEN_PATTERN = (
    r"(?<![A-Za-z0-9])(?:"
    r"(?:d?[Δ∆δ∂](?:[A-Za-zΑ-ω]+(?:,[A-Za-z]+)?|[（(][A-Za-zΑ-ω]+[）)])?)|"
    r"(?:[A-Za-zΑ-ω]_(?:[{]?[A-Za-zΑ-ω]+[}]?|外|内|总|系统|环境))|"
    r"(?:(?:ln|log|exp|sin|cos|tan)\s*(?:[A-Za-zΑ-ω][A-Za-zΑ-ω0-9θ]*|[（(][^()（）\n]{1,48}[）)]))|"
    r"(?:[-+]?\d+(?:\.\d+)?\s*(?:×|·|\\times|\\cdot)\s*10\s*\^\s*\{?[-+]?\d+\}?)|"
    r"(?:[A-Za-z0-9]\s*/\s*[A-Za-z0-9])|"
    r"(?:(?:pV|RT|nRT|nF|zF|Kp|Ksp|Ka|Kb)(?:\^[A-Za-zΑ-ω]+)?)"
    r")(?![A-Za-z0-9])"
)
TEXT_CHEMICAL_PATTERN = (
    r"(?<![A-Za-z])(?=[A-Za-z0-9_{}^₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻+\-]{1,36}"
    r"(?:₀|₁|₂|₃|₄|₅|₆|₇|₈|₉|⁰|¹|²|³|⁴|⁵|⁶|⁷|⁸|⁹|⁺|⁻|_\{?\d|\^\{?[+\-\d]|\((?:aq|s|l|g)\)))"
    r"(?:[A-Z][a-z]?(?:\d+|_\{?\d+\}?|[₀-₉]+)?){1,8}"
    r"(?:\^\{?[+\-\d]+\}?|[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)?(?:\((?:aq|s|l|g)\))?"
)
REACTION_TOKEN_PATTERN = (
    r"(?:(?:\d+(?:\.\d+)?\s*)?[A-Za-zΑ-Ωα-ω]"
    r"[A-Za-z0-9₀-₉_Α-Ωα-ω一-鿿']*)"
)
REACTION_GROUP_PATTERN = (
    rf"(?:{REACTION_TOKEN_PATTERN}|\((?:{REACTION_TOKEN_PATTERN})(?:\s*\+\s*{REACTION_TOKEN_PATTERN})+\)"
    rf"(?:_[A-Za-z0-9Α-Ωα-ω一-鿿]+)?)"
)
TEXT_REACTION_PATTERN = (
    rf"(?<![A-Za-z0-9_]){REACTION_GROUP_PATTERN}(?:\s*\+\s*{REACTION_GROUP_PATTERN})*"
    rf"\s*(?:→|⇌|↔)\s*{REACTION_GROUP_PATTERN}(?:\s*\+\s*{REACTION_GROUP_PATTERN})*"
)
TEXT_QUANTITY_PATTERN = (
    r"(?<![A-Za-z0-9_.])[+\-−]?\d+(?:\.\d+)?(?:\s*[×x]\s*10\s*[\^]?[+\-−]?\d+)?\s*"
    r"(?:%|℃|°[CFK]|kg|mg|μg|ng|g|km|cm|mm|μm|nm|m|kJ|J|MJ|GJ|MPa|kPa|Pa|mol|mmol|μmol|"
    r"kmol|L|mL|μL|s|ms|μs|min|h|Hz|kHz|MHz|GHz|V|mV|A|mA|W|kW|K|M|μM)"
    r"(?:\s*[·⋅/]\s*(?:kg|g|m|cm|mm|mol|L|s|K|A)(?:[⁻−-]?[⁰¹²³⁴⁵⁶⁷⁸⁹0-9]+)?)?"
    r"(?:[⁻−-]?[⁰¹²³⁴⁵⁶⁷⁸⁹0-9]+)?(?![A-Za-z])"
)

CORE_EXPRESSIONS_CAPABILITY = CapabilityManifest(
    capability_id="core.academic_expressions",
    version="1.2",
    name="跨学科学术表达",
    description="只识别显式的公式、向量、矩阵、反应式和数值单位，不判断学科结论正误。",
    expression_rules=(
        ExpressionRule(
            "core.latex_matrix",
            "matrix",
            r"\\begin\s*\{(?:matrix|pmatrix|bmatrix|vmatrix|Vmatrix|smallmatrix)\}",
            source_format="latex",
            confidence=1.0,
            priority=100,
        ),
        ExpressionRule(
            "core.latex_vector",
            "vector",
            r"\\(?:vec|overrightarrow|mathbf|boldsymbol)\s*(?:\{|[A-Za-z])",
            source_format="latex",
            confidence=0.99,
            priority=90,
        ),
        ExpressionRule(
            "core.latex_chemical_notation",
            "chemical_notation",
            r"\\ce\s*\{",
            source_format="latex",
            confidence=1.0,
            priority=95,
        ),
        ExpressionRule(
            "core.reaction_arrow",
            "reaction",
            r"(?:\\(?:rightleftharpoons|leftrightarrow|rightarrow|longrightarrow|to)\b|[⇌↔→])",
            source_format="any",
            confidence=0.95,
            priority=80,
        ),
        ExpressionRule(
            "core.latex_relation",
            "equation",
            r"(?:=|<|>|≤|≥|≈|∝|\\(?:leq?|geq?|approx|propto)\b)",
            source_format="latex",
            confidence=0.95,
            priority=20,
        ),
        ExpressionRule(
            "core.text_reaction",
            "reaction",
            TEXT_REACTION_PATTERN,
            source_format="text",
            confidence=0.98,
            priority=95,
        ),
        ExpressionRule(
            "core.text_electrode_notation",
            "chemical_notation",
            TEXT_ELECTRODE_PATTERN,
            source_format="text",
            confidence=0.99,
            priority=100,
        ),
        ExpressionRule(
            "core.text_equation",
            "equation",
            TEXT_EQUATION_PATTERN,
            source_format="text",
            confidence=0.96,
            priority=85,
        ),
        ExpressionRule(
            "core.text_thermodynamic_quantity",
            "formula",
            TEXT_THERMODYNAMIC_PATTERN,
            source_format="text",
            confidence=0.95,
            priority=75,
        ),
        ExpressionRule(
            "core.text_symbolic_token",
            "formula",
            TEXT_SYMBOLIC_TOKEN_PATTERN,
            source_format="text",
            confidence=0.94,
            priority=65,
        ),
        ExpressionRule(
            "core.text_chemical_species",
            "chemical_notation",
            TEXT_CHEMICAL_PATTERN,
            source_format="text",
            confidence=0.96,
            priority=70,
        ),
        ExpressionRule(
            "core.numeric_quantity",
            "quantity",
            TEXT_QUANTITY_PATTERN,
            source_format="text",
            confidence=0.98,
            priority=30,
        ),
    ),
)
