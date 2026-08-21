import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class PlanDraftUITests(unittest.TestCase):
    """回归：蓝图页可按计划项生成本题草案（场景裁剪 + 意见输入）。"""

    def setUp(self):
        self.html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.js = (ROOT / "web/app.js").read_text(encoding="utf-8")

    def test_js_renders_draft_button_per_plan_item(self):
        self.assertIn('data-plan-draft="${index}"', self.js)
        self.assertIn("生成本题草案", self.js)

    def test_js_renders_draft_view_slot(self):
        self.assertIn('data-plan-draft-view="', self.js)

    def test_js_has_generate_plan_item_draft_handler(self):
        self.assertIn("async function generatePlanItemDraft(", self.js)
        self.assertIn('api("/api/practice/plan-draft"', self.js)

    def test_js_delegates_draft_button_click(self):
        self.assertIn('event.target.closest("[data-plan-draft]")', self.js)

    def test_js_prompts_for_feedback_before_draft(self):
        self.assertIn("platformPrompt(", self.js)
        self.assertIn("作为模型的针对性反馈", self.js)

    def test_js_has_draft_render(self):
        self.assertIn("function renderPlanItemDraft(", self.js)
        self.assertNotIn("draft.solution_steps", self.js)

    def test_js_persists_draft_by_plan_item_id(self):
        self.assertIn("const practicePlanDrafts = {}", self.js)
        self.assertIn("practicePlanDrafts[planItem.plan_item_id]", self.js)

    def test_js_has_adopt_and_clear_actions(self):
        self.assertIn("采用此草案", self.js)
        self.assertIn("清除草案", self.js)
        self.assertIn("togglePlanItemDraftAdopt", self.js)
        self.assertIn("clearPlanItemDraft", self.js)

    def test_js_injects_adopted_drafts_into_generation(self):
        self.assertIn("plan_drafts", self.js)
        self.assertIn("e.adopted", self.js)

    def test_js_cleans_old_drafts_per_blueprint_identity(self):
        self.assertIn("function practiceBlueprintKey(", self.js)
        self.assertIn("currentPlanDraftBlueprintKey", self.js)
        self.assertIn("syncPracticePlanDraftsToBlueprint", self.js)

    def test_js_restores_drafts_after_render_plan(self):
        self.assertIn(" syncPracticePlanDraftsToBlueprint(plan, planItems);", self.js)
        self.assertIn("renderPlanItemDraft(pid)", self.js)

    def test_editing_plan_item_cancels_its_adopted_draft(self):
        self.assertIn("practicePlanDrafts[planItemId].adopted = false", self.js)
        self.assertIn("renderPlanDraftAdoptSummary()", self.js)

    def test_full_plan_regeneration_uses_a_reversible_candidate(self):
        self.assertIn("pendingPracticePlanCandidate", self.js)
        self.assertIn("function adoptPracticePlanCandidate()", self.js)
        self.assertIn("function keepOriginalPracticePlan()", self.js)
        self.assertIn('$("practicePlanConfirmBtn").disabled = false;', self.js)
        self.assertIn('id="practicePlanCandidateActions"', self.html)


if __name__ == "__main__":
    unittest.main()
