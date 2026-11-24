from __future__ import annotations

import pytest

from data_agent.grounding import check_answer, extract_numbers, observed_numbers


def test_extract_numbers() -> None:
    ms = extract_numbers("Total 1,234.5 across 57.14 % of 3 groups, change -48; Q1 and COVID-19.")
    assert [(m.raw, m.value, m.decimals, m.percent) for m in ms] == [
        ("1,234.5", 1234.5, 1, False),
        ("57.14 %", 57.14, 2, True),
        ("3", 3.0, 0, False),
        ("-48", -48.0, 0, False),
    ]


def test_observed_numbers_from_cells() -> None:
    seen = observed_numbers([1616.0, 3, None, True, "2015-06-23", "abc", float("nan")])
    assert {1616.0, 3.0, 2015.0} <= seen
    assert all(v == v for v in seen)  # no NaN


@pytest.mark.parametrize(
    ("answer", "observed"),
    [
        ("The average delay is 1616 days.", {1616.0}),
        ("The rate is 0.9049.", {0.9049079754601227}),  # answer rounded to 4 decimals
        ("57.14% of patients had SLE.", {0.5714285714}),  # fraction shown as a percentage
        ("57.14 percent of patients.", {57.142857}),
        ("The share is 0.57.", {57.14}),  # percentage shown as a fraction
        ("Enrollment totals 372,954 students.", {372954.0}),
        ("The difference is -48.", {-48.0}),
        ("The first visit was on 2015-06-23.", observed_numbers(["2015-06-23"])),
    ],
)
def test_grounded_answers_pass(answer: str, observed: set[float]) -> None:
    report = check_answer(answer, observed)
    assert report.ok, report.ungrounded


def test_invented_number_is_caught() -> None:
    report = check_answer("Revenue grew 12% to 4,500 units.", {4500.0})
    assert not report.ok
    assert report.ungrounded == ["12%"]


def test_wrong_rounding_is_caught() -> None:
    # 0.905 would round 0.90490..., but 0.91 is outside half a unit of its last digit
    assert check_answer("The rate is 0.905.", {0.9049079754601227}).ok
    assert not check_answer("The rate is 0.91.", {0.9049079754601227}).ok


def test_numbers_from_the_question_need_no_grounding() -> None:
    question = "Among schools with an SAT rate above 0.3 in 2017, what is the highest rate?"
    report = check_answer("Above 0.3 in 2017, the highest rate is 0.9049.", {0.9049}, question)
    assert report.ok
    assert report.checked == ["0.9049"]


def test_answer_without_numbers_passes() -> None:
    report = check_answer("Borussia Dortmund has the most home wins.", set())
    assert report.ok and report.checked == []
