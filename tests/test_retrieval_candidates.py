from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class RetrievalCandidateTests(unittest.TestCase):
    def test_corpus_scorer_uses_bm25s_and_rewards_rare_terms(self) -> None:
        from app.retrieval import CorpusTextScorer
        from app.text_utils import tokenize_zh_en

        scorer = CorpusTextScorer(
            [
                "材料 性能 材料 性能",
                "材料 性能 材料 组织",
                "马氏体相变 形核机制",
            ]
        )

        scores = scorer.scores(tokenize_zh_en("马氏体相变"))
        self.assertEqual("bm25s", scorer.backend)
        self.assertGreater(scores[2], scores[0])

    def test_candidate_evidence_text_uses_the_selected_row_not_last_scanned_row(self) -> None:
        from app.retrieval import build_candidates

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            blocks_csv = tmp / "blocks.csv"
            page_map_csv = tmp / "page_map.csv"
            output_csv = tmp / "retrieval_candidates.csv"
            write_csv(
                blocks_csv,
                [
                    {
                        "textbook": "示例教材",
                        "source_file": "demo.json",
                        "page_idx": "1",
                        "block_index": "1",
                        "block_type": "text",
                        "source_type": "text_block",
                        "chapter_section": "1",
                        "text": "相律 正文 A，系统自由度由相数和组分数决定。",
                        "retrieval_text": "相律 正文 A，系统自由度由相数和组分数决定。",
                    },
                    {
                        "textbook": "示例教材",
                        "source_file": "demo.json",
                        "page_idx": "2",
                        "block_index": "1",
                        "block_type": "text",
                        "source_type": "text_block",
                        "chapter_section": "1",
                        "text": "相律 正文 B，自由度计算用于相图分析。",
                        "retrieval_text": "相律 正文 B，自由度计算用于相图分析。",
                    },
                    {
                        "textbook": "示例教材",
                        "source_file": "demo.json",
                        "page_idx": "3",
                        "block_index": "1",
                        "block_type": "text",
                        "source_type": "text_block",
                        "chapter_section": "9",
                        "text": "如有账号问题，请发邮件至: abook@hep.com.cn。",
                        "retrieval_text": "如有账号问题，请发邮件至: abook@hep.com.cn。",
                    },
                ],
            )
            write_csv(
                page_map_csv,
                [
                    {"textbook": "示例教材", "citation_textbook": "示例教材", "source_file": "demo.json", "pdf_page_idx": "1", "printed_page": "101", "verified": "true"},
                    {"textbook": "示例教材", "citation_textbook": "示例教材", "source_file": "demo.json", "pdf_page_idx": "2", "printed_page": "102", "verified": "true"},
                    {"textbook": "示例教材", "citation_textbook": "示例教材", "source_file": "demo.json", "pdf_page_idx": "3", "printed_page": "103", "verified": "true"},
                ],
            )

            candidates = build_candidates(
                {"items": [{"question_id": "q1", "section": "一、识图题", "stem": "相律"}]},
                blocks_csv,
                page_map_csv,
                output_csv,
                top_k=1,
            )

        self.assertEqual(1, len(candidates))
        self.assertEqual("示例教材", candidates[0].textbook)
        self.assertIn("相律 正文", candidates[0].evidence_text)
        self.assertNotIn("如有账号问题", candidates[0].evidence_text)

    def test_selected_text_block_includes_same_page_continuation(self) -> None:
        from app.retrieval import _adjacent_text_context

        rows = [
            {"source_type": "text_block", "retrieval_text": "亚共晶合金在共晶温度以下，"},
            {"source_type": "text_block", "retrieval_text": "初生固相继续析出第二相，最终得到室温组织。"},
        ]

        evidence, preview = _adjacent_text_context(rows[0], rows)

        self.assertIn("同页后续说明", evidence)
        self.assertIn("初生固相继续析出第二相", evidence)
        self.assertIn("最终得到室温组织", preview)

    def test_continuation_can_cross_figure_and_table_blocks(self) -> None:
        from app.retrieval import _adjacent_text_context

        rows = [
            {"source_type": "text_block", "retrieval_text": "在2点以下,"},
            {"source_type": "figure_block", "retrieval_text": "图7.59 室温组织"},
            {"source_type": "table_block", "retrieval_text": "表格"},
            {"source_type": "text_block", "retrieval_text": "初生相与共晶相都会继续变化，最终得到珠光体和变态莱氏体。"},
        ]

        evidence, preview = _adjacent_text_context(rows[0], rows)

        self.assertIn("珠光体和变态莱氏体", evidence)
        self.assertIn("最终得到", preview)

    def test_truncated_figure_ocr_resolves_to_complete_same_page_text(self) -> None:
        from app.retrieval import _canonical_semantic_row

        full = (
            "亚共晶合金在液相线以下析出初生固相，液相成分随温度变化。"
            "到达共晶温度后剩余液相发生共晶反应，初生固相和共晶中的固相继续转变。"
            "冷却到室温后，显微组织中可见树枝状初生组成体，其余为共晶转变组成体。"
            "两类组成体的计算必须使用同一共晶反应基准。"
        )
        figure = {
            "source_type": "figure_block",
            "retrieval_text": "图7.59 " + full[:300],
        }
        text = {"source_type": "text_block", "block_type": "text", "retrieval_text": full}

        assert _canonical_semantic_row(figure, [text, figure]) is text

    def test_zone_law_formula_is_normalized_and_reserved_as_candidate(self) -> None:
        from app.retrieval import build_candidates
        from app.text_utils import formulas_equivalent

        self.assertTrue(
            formulas_equivalent(
                "hu + kv + lw = 0",
                r"$$ u h + v k + w l = 0\tag{3-71} $$",
                context="晶带定律与带轴衍射",
            )
        )
        self.assertFalse(formulas_equivalent("uv=0", "vu=0"))

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            blocks_csv = tmp / "blocks.csv"
            page_map_csv = tmp / "page_map.csv"
            output_csv = tmp / "retrieval_candidates.csv"
            write_csv(
                blocks_csv,
                [
                    {
                        "textbook": "示例教材",
                        "source_file": "demo.json",
                        "page_idx": "151",
                        "block_index": "8",
                        "block_id": "示例教材:p151:b8",
                        "block_type": "text",
                        "source_type": "text_block",
                        "text": "晶带内的晶面指数需要满足相应的带轴关系。",
                        "retrieval_text": "晶带内的晶面指数需要满足相应的带轴关系。",
                    },
                    {
                        "textbook": "示例教材",
                        "source_file": "demo.json",
                        "page_idx": "151",
                        "block_index": "9",
                        "block_id": "示例教材:p151:b9",
                        "block_type": "equation",
                        "source_type": "equation_block",
                        "text": r"$$ u h + v k + w l = 0\tag{3-71} $$",
                        "retrieval_text": r"$$ u h + v k + w l = 0\tag{3-71} $$",
                    },
                    {
                        "textbook": "示例教材",
                        "source_file": "demo.json",
                        "page_idx": "152",
                        "block_index": "1",
                        "block_id": "示例教材:p152:b1",
                        "block_type": "text",
                        "source_type": "text_block",
                        "text": "带轴定律的一般背景说明。",
                        "retrieval_text": "带轴定律的一般背景说明。",
                    },
                ],
            )
            write_csv(
                page_map_csv,
                [
                    {"textbook": "示例教材", "citation_textbook": "示例教材", "source_file": "demo.json", "pdf_page_idx": "151", "printed_page": "139", "verified": "true"},
                    {"textbook": "示例教材", "citation_textbook": "示例教材", "source_file": "demo.json", "pdf_page_idx": "152", "printed_page": "140", "verified": "true"},
                ],
            )
            candidates = build_candidates(
                {"items": [{"question_id": "q1", "section": "作图题", "stem": "画出[110]带轴电子衍射花样。"}]},
                blocks_csv,
                page_map_csv,
                output_csv,
                top_k=1,
                knowledge_plans={
                    "q1": {
                        "knowledge_points": ["带轴定律（晶带定律）"],
                        "formulas": ["hu + kv + lw = 0"],
                        "search_queries": ["带轴定律 hu+kv+lw=0"],
                    }
                },
            )

        formula = next(candidate for candidate in candidates if candidate.block_id.endswith(":b9"))
        self.assertEqual("139", formula.printed_page)
        self.assertEqual(100.0, formula.score)
        self.assertIn("相邻教材说明", formula.evidence_text)

    def test_formula_equivalence_guard_keeps_exact_textbook_formula(self) -> None:
        from app.evidence_selection import _apply_formula_evidence_guard
        from app.retrieval import EvidenceCandidate

        candidate = EvidenceCandidate(
            evidence_id="ev_zone",
            question_id="q1",
            textbook="示例教材",
            citation_textbook="示例教材",
            chapter_section="",
            source_file="demo.json",
            pdf_page_idx="151",
            printed_page="139",
            score=100.0,
            evidence_text=r"$$ u h + v k + w l = 0\tag{3-71} $$" + "\n相邻教材说明：晶带内的晶面指数需要满足相应的带轴关系。",
            verified_page=True,
            source_type="equation_block",
        )
        selection, repaired = _apply_formula_evidence_guard(
            {
                "question_id": "q1",
                "knowledge_points": [
                    {
                        "knowledge_point": "带轴定律（晶带定律）",
                        "selected_evidence_ids": [],
                        "rejected_evidence_ids": ["ev_zone"],
                        "needs_expansion": True,
                    }
                ],
            },
            {
                "knowledge_points": ["带轴定律（晶带定律）"],
                "formulas": ["hu + kv + lw = 0"],
                "search_queries": ["带轴定律 hu+kv+lw=0"],
            },
            [candidate],
        )

        self.assertEqual(["带轴定律（晶带定律）"], repaired)
        point = selection["knowledge_points"][0]
        self.assertEqual(["ev_zone"], point["selected_evidence_ids"])
        self.assertEqual("direct_support", point["support_type"])


if __name__ == "__main__":
    unittest.main()
