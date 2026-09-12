from copy import deepcopy

import pytest

from app.answer_generation import _answer_from_answer_units, _question_subquestion_rows, fragment_from_analysis_draft


@pytest.mark.parametrize('parent', ['1', '6'])
@pytest.mark.parametrize('legacy', [False, True])
def test_synthetic_parent_ids_do_not_reintroduce_question_or_empty_number(parent, legacy):
    stem = '为什么需要可逆电池？直接测量什么？'
    container = {'number': parent, 'stem': stem, 'raw': stem, 'requirements': [
        {'number': f'{parent}.1', 'stem': '为什么需要可逆电池？', 'question_type': '简答题'},
        {'number': f'{parent}.2', 'stem': '直接测量什么？', 'question_type': '简答题'},
    ]}
    if not legacy:
        container['synthetic_parent'] = True
    question = {'question_id': 'summary_q', 'number': parent, 'section': '简答题',
                'question_type': '简答题', 'stem': stem, 'subquestions': [container]}
    draft = {'question_id': 'summary_q', 'answer': '见解析', 'formulas': [], 'answer_units': [
        {'number': f'{parent}.1', 'answer': '需要可逆条件。'},
        {'number': f'{parent}.2', 'answer': '测量电动势与温度。'},
    ]}
    original = deepcopy(draft)
    fragment = fragment_from_analysis_draft(deepcopy(draft), question, [])
    assert fragment['answer_summary'] == '(1)需要可逆条件。；(2)测量电动势与温度。'
    assert fragment['answer'] == fragment['answer_summary']
    assert [unit['number'] for unit in fragment['answer_units']] == [f'{parent}.1', f'{parent}.2']
    assert draft == original
    analysis = ''.join(seg.get('text', '') for block in fragment['blocks'] if block['label'] == '解析' for seg in block['segments'])
    assert '(1)为什么需要可逆电池？' in analysis
    assert '(2)直接测量什么？' in analysis


def test_real_nested_parent_keeps_numbers_but_not_question_text():
    question = {'subquestions': [{'number': '2', 'stem': '原题的真实父问题', 'raw': '(2)原题的真实父问题',
        'requirements': [{'number': '2.1', 'stem': '要求甲'}, {'number': '2.2', 'stem': '要求乙'}]}]}
    units = [{'number': '2.1', 'answer': '甲答案'}, {'number': '2.2', 'answer': '乙答案'}, {'number': '3', 'answer': '另一答案'}]
    assert _answer_from_answer_units(units, _question_subquestion_rows(question)) == '(2)：①、甲答案；②、乙答案；(3)另一答案'


def test_model_summary_is_not_replaced_by_generated_unit_summary():
    draft = {'question_id': 'q', 'answer': '模型保留的明确答案。', 'answer_units': [
        {'number': '1', 'answer': '分项甲'}, {'number': '2', 'answer': '分项乙'}], 'formulas': []}
    question = {'question_id': 'q', 'question_type': '简答题', 'subquestions': [
        {'number': '1', 'stem': '题目甲'}, {'number': '2', 'stem': '题目乙'}]}
    assert fragment_from_analysis_draft(draft, question, [])['answer_summary'] == '模型保留的明确答案。'


def test_word_export_uses_flat_answer_summary(tmp_path):
    import json
    from zipfile import ZipFile

    from lxml import etree

    from app.docx_v4 import build_docx_from_fragments

    stem = '说明原因并指出测量量。'
    question = {'question_id': 'q', 'number': '1', 'section': '一、简答题', 'question_type': '简答题',
                'stem': stem, 'subquestions': [{'number': '1', 'stem': stem, 'raw': stem,
                    'synthetic_parent': True, 'requirements': [
                        {'number': '1.1', 'stem': '说明原因'}, {'number': '1.2', 'stem': '指出测量量'}]}]}
    fragment = fragment_from_analysis_draft({'question_id': 'q', 'answer': '见解析', 'formulas': [],
        'answer_units': [{'number': '1.1', 'answer': '原因甲'}, {'number': '1.2', 'answer': '测量量乙'}]}, question, [])
    source = tmp_path / 'fragments.json'
    source.write_text(json.dumps({'fragments': [fragment]}, ensure_ascii=False))
    output = tmp_path / 'answer.docx'
    build_docx_from_fragments(source, output)
    with ZipFile(output) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    text = '\n'.join(''.join(p.xpath('.//w:t/text()', namespaces=ns)) for p in root.xpath('//w:body/w:p', namespaces=ns))
    assert '(1)原因甲；(2)测量量乙' in text
    assert '()' not in text
    assert f'{stem}：' not in text
