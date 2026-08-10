import pytest

from harness.evals import PortugueseDetector, TaskVerdict, evaluate_oracle
from harness.evals.oracles import EvalEvidence

PORTUGUESE = [
    "Criei o arquivo index.html com um título e um parágrafo em português.",
    "No projeto, foram encontrados 3 arquivos Markdown na raiz e em docs.",
    "A expressão TODO foi encontrada em um arquivo do projeto, na primeira linha.",
    "Glob procura caminhos por padrão; a busca de conteúdo procura texto dentro deles.",
]

ENGLISH = [
    "I created the file index.html with a title and a paragraph inside the body.",
    "The search found one result in the project files that you asked about.",
    "Here is the list of Markdown files that were found in this repository.",
]


@pytest.mark.parametrize("text", PORTUGUESE)
def test_portuguese_answers_are_recognized(text: str) -> None:
    assert PortugueseDetector().is_portuguese(text)


@pytest.mark.parametrize("text", ENGLISH)
def test_english_answers_are_rejected(text: str) -> None:
    assert not PortugueseDetector().is_portuguese(text)


def test_text_too_short_to_judge_is_not_counted_as_portuguese() -> None:
    detector = PortugueseDetector()

    assert detector.deterministic is True
    assert not detector.is_portuguese("ok")
    assert not detector.is_portuguese("")


def test_the_oracle_can_only_grade_the_language_when_a_detector_is_supplied() -> None:
    assertion = [{"operator": "response_language_pt"}]
    evidence = EvalEvidence(response=PORTUGUESE[0])

    # Without a detector the assertion abstains, which is what made it dead.
    assert evaluate_oracle(assertion, evidence).verdict is TaskVerdict.INCONCLUSIVE

    graded = evaluate_oracle(assertion, evidence, language_detector=PortugueseDetector())
    assert graded.verdict is TaskVerdict.PASS

    english = EvalEvidence(response=ENGLISH[0])
    failed = evaluate_oracle(assertion, english, language_detector=PortugueseDetector())
    assert failed.verdict is TaskVerdict.FAIL
