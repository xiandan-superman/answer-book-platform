from copy import deepcopy

import pytest

from app.calculation_consistency import calculation_contract_issues, formula_numeric_consistency_issues
from app.final_acceptance import audit_ok
from app.formula_audit import audit_text_segments_no_formula
from app.machine_gate_policy import current_content_report


@pytest.mark.parametrize('word', ['剩余', '析出', '转移', '反应', '损失', ''])
def test_content_words_do_not_require_transition_ledger(word):
    draft = {'answer': f'{word}液相为九分之一，固相为九分之八。',
             'formulas': [{'latex': '1/9'}, {'latex': '8/9'}],
             'calculation_contract': {'result_quantities': [
                 {'quantity_id': 'liquid', 'formula_index': 1, 'value': 1/9, 'basis': 'whole'},
                 {'quantity_id': 'solid', 'formula_index': 2, 'value': 8/9, 'basis': 'whole'}],
                 'partitions': [{'component_quantity_ids': ['liquid', 'solid'], 'expected_total': 1}]}}
    original = deepcopy(draft)
    assert calculation_contract_issues(draft) == []
    assert draft == original
    draft['calculation_contract']['result_quantities'][1]['value'] = 0.5
    assert any('partition_sum_mismatch' in issue for issue in calculation_contract_issues(draft))
    draft['calculation_contract']['partitions'][0]['component_quantity_ids'][1] = 'unknown'
    assert any('unknown_partition_component' in issue for issue in calculation_contract_issues(draft))


def test_literal_arithmetic_remains_checked_without_symbol_inference():
    assert formula_numeric_consistency_issues([{'latex': '1+2=4'}])
    assert not formula_numeric_consistency_issues([{'latex': '1+2=3'}])
    assert not formula_numeric_consistency_issues([{'latex': 'x=1'}, {'latex': 'x=2'}])


def test_history_retired_finding_does_not_block_but_unknown_errors_remain():
    old = {'ok': False, 'issues': [{'code': 'answer_analysis_comparative_contradiction'}], 'warnings': []}
    original = deepcopy(old)
    assert audit_ok('content_quality', old, False) == (True, [], [])
    assert old == original
    old['issues'].append({'code': 'missing_answer'})
    assert not current_content_report(old)['ok']
    assert not audit_ok('content_quality', {'ok': False, 'issues': ['unknown failure']}, False)[0]


def test_plain_relationship_preserved_and_raw_latex_still_rejected():
    text = [{'label': '解析', 'segments': [{'type': 'text', 'text': '自由能小于零，比例等于父项乘以局部分数。'}]}]
    assert audit_text_segments_no_formula(text) == []
    text[0]['segments'][0]['text'] = r'\unknown{x}'
    assert audit_text_segments_no_formula(text)
