from __future__ import annotations

import io
import os
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile

import pytest
from lxml import etree

from app import pandoc_word
from app.officecli_word import selected_word_tool_variant


def test_missing_runtime_does_not_fallback(monkeypatch):
    monkeypatch.setattr(pandoc_word, '_binary', lambda: (_ for _ in ()).throw(RuntimeError('runtime missing')))
    with pytest.raises(RuntimeError, match='runtime missing'):
        pandoc_word.convert('x^2')


def test_unknown_binary_is_rejected_before_execution(tmp_path, monkeypatch):
    binary = tmp_path / 'pandoc.exe'
    binary.write_bytes(b'not-the-verified-release')
    monkeypatch.setenv('ANSWER_BOOK_PANDOC_BINARY', str(binary))
    with pytest.raises(RuntimeError, match='SHA256'):
        pandoc_word.convert('x')


@pytest.fixture
def candidate(monkeypatch):
    binary = os.environ.get('PANDOC_TEST_BINARY')
    if binary:
        monkeypatch.setenv('ANSWER_BOOK_PANDOC_BINARY', binary)
    pandoc_word.runtime_info()


def test_conditions_are_native_preserved_and_concurrent_results_independent(candidate):
    from app.omml import omml_from_latex
    formula = r'A\xrightarrow[k_1]{\text{催化剂}}B'
    with ThreadPoolExecutor(max_workers=2) as pool:
        nodes = list(pool.map(omml_from_latex, [formula, formula]))
    assert nodes[0] is not nodes[1]
    node = nodes[0]
    text = ''.join(node.xpath('.//*[local-name()="t"]/text()'))
    assert '催化剂' in text and 'k' in text and '1' in text
    assert node.xpath('.//*[local-name()="limUpp"]')
    assert node.xpath('.//*[local-name()="limLow"]')
    assert not any('\\' in t for t in node.xpath('.//*[local-name()="t"]/text()'))


@pytest.mark.parametrize('formula', [r'\frac{', r'\unknowncommand{x}'])
def test_malformed_and_unsupported_formulas_fail(candidate, formula):
    with pytest.raises(ValueError):
        pandoc_word.convert(formula)


@pytest.mark.parametrize('mode', ['question', 'knowledge'])
def test_production_practice_entry_preserves_conditions(candidate, mode):
    from app.practice_export import build_practice_question_docx, validate_docx_output
    from scripts.build_practice_contract_fixture import _fixture
    data = _fixture()
    data['source_mode'] = mode
    data['exercises'][1]['formulas'][0].update(latex=r'A\xrightarrow[k_1]{\text{催化剂}}B', role='given')
    content = build_practice_question_docx(data)
    assert validate_docx_output(content, data)['ok']
    with ZipFile(io.BytesIO(content)) as z:
        root = etree.fromstring(z.read('word/document.xml'))
    assert '催化剂' in ''.join(root.xpath('//*[local-name()="oMath"]//*[local-name()="t"]/text()'))


def test_legacy_task_and_environment_cannot_reactivate_retired_engines(monkeypatch):
    from app.officecli_word import word_tool_selection
    for legacy in ("A", "B", "", "C"):
        monkeypatch.setenv("ANSWER_BOOK_WORD_TOOL_VARIANT", legacy)
        with word_tool_selection(legacy):
            assert selected_word_tool_variant() == "C"
        assert selected_word_tool_variant(legacy) == "C"


def test_install_checks_archive_and_binary_and_preserves_archive(tmp_path, monkeypatch):
    import hashlib
    content = b'verified-pandoc-test-binary'
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as z:
        z.writestr('release/pandoc', content)
        z.writestr('release/COPYRIGHT', 'upstream license')
    archive = buffer.getvalue()
    digest = hashlib.sha256(archive).hexdigest()
    monkeypatch.setattr('app.paths.DATA_ROOT', tmp_path)
    monkeypatch.setattr(pandoc_word.platform, 'system', lambda: 'TestOS')
    monkeypatch.setattr(pandoc_word.platform, 'machine', lambda: 'testcpu')
    monkeypatch.setitem(pandoc_word._ARCHIVES, ('TestOS', 'testcpu'), ('release.zip', digest, 'release/pandoc'))
    monkeypatch.setattr(pandoc_word, '_BINARY_HASHES', {hashlib.sha256(content).hexdigest()})
    requests = []
    def download(request, **kwargs):
        requests.append(request.full_url)
        return io.BytesIO(archive)
    monkeypatch.setattr(pandoc_word.urllib.request, 'urlopen', download)
    binary = pandoc_word._install_runtime()
    assert binary.read_bytes() == content
    assert (binary.parent / 'release.zip').read_bytes() == archive
    assert pandoc_word._install_runtime() == binary
    assert len(requests) == 1
    assert requests[0].startswith('https://github.com/jgm/pandoc/releases/download/3.11/')


def test_install_rejects_corrupt_archive_without_publishing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr('app.paths.DATA_ROOT', tmp_path)
    monkeypatch.setattr(pandoc_word.platform, 'system', lambda: 'TestOS')
    monkeypatch.setattr(pandoc_word.platform, 'machine', lambda: 'testcpu')
    monkeypatch.setitem(pandoc_word._ARCHIVES, ('TestOS', 'testcpu'), ('release.zip', 'invalid', 'release/pandoc'))
    monkeypatch.setattr(pandoc_word.urllib.request, 'urlopen', lambda *_args, **_kwargs: io.BytesIO(b'corrupt'))
    with pytest.raises(RuntimeError, match='archive SHA256'):
        pandoc_word._install_runtime()
    assert not list(tmp_path.rglob('pandoc'))
    assert not list(tmp_path.rglob('*.zip'))
