from __future__ import annotations

import pytest

from data_agent.grounding import check_answer, date_values, extract_numbers, observed_numbers


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
    ],
)
def test_grounded_answers_pass(answer: str, observed: set[float]) -> None:
    report = check_answer(answer, observed)
    assert report.ok, report.ungrounded


def test_date_found_in_a_result_cell_passes() -> None:
    cells = ["2015-06-23"]
    report = check_answer(
        "The first visit was on 2015-06-23.", observed_numbers(cells), "", date_values(cells)
    )
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


def test_date_restated_in_another_format_is_grounded() -> None:
    # Held-out q176: the question says "19 September 2014", the answer writes "2014-09-19".
    question = "average risk in force on 19 September 2014, rounded to one decimal place"
    answer = "Their average chance creation passing risk on 2014-09-19 is 69.5."
    report = check_answer(answer, {69.5}, question)
    assert report.ok, report.ungrounded
    assert "2014-09-19" in report.checked


def test_date_from_a_result_cell_is_grounded_and_invented_date_is_caught() -> None:
    seen = {"2015-06-23"}
    assert check_answer("The first visit was on June 23, 2015.", set(), "", seen).ok
    assert check_answer("Activity peaked in June 2015.", set(), "", seen).ok  # month level
    bad = check_answer("The first visit was on 2015-07-01.", set(), "", seen)
    assert bad.ungrounded == ["2015-07-01"]


def test_ranges_are_not_dates() -> None:
    report = check_answer("Enrolment for 2014-2015 was 372,954.", {372954.0}, "in 2014-2015")
    assert report.ok, report.ungrounded
