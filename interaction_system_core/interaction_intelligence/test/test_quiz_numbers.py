# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""A quiz answer whose expected value is a number is graded without the LLM when it says one
number (in digits or words, English or Spanish): a small model gave "forty" for 48 and "Mars has
one moon" for 2 as right. Several numbers, or none, are left to the LLM."""

import pytest

from interaction_intelligence.spoken_numbers import grade_number, spoken_numbers

EN, ES = "English", "Spanish"


@pytest.mark.parametrize("text,language,strong", [
    ("forty-eight", EN, [48]), ("two thousand and five", EN, [2005]),
    ("a hundred and twenty", EN, [120]), ("twenty one", EN, [21]), ("I think it's 48", EN, [48]),
    ("nineteen sixty-nine", EN, [19, 69]),
    ("cuarenta y ocho", ES, [48]), ("ciento veinte", ES, [120]), ("dos mil veinte", ES, [2020]),
    ("mil novecientos sesenta y nueve", ES, [1969]), ("treinta y un días", ES, [31]),
    ("veintiún", ES, [21]), ("un millón", ES, [1000000]), ("3,5", ES, [3.5]), ("1.000", ES, [1000]),
    ("dos tres", ES, [2, 3]),
])
def test_spoken_numbers(text, language, strong):
    assert spoken_numbers(text, language)[0] == [float(v) for v in strong]


def test_an_article_is_a_number_only_when_there_is_no_other():
    assert spoken_numbers("Marte tiene una luna.", ES) == ([], [1.0])
    assert spoken_numbers("Es una pregunta difícil, creo que dos", ES) == ([2.0], [1.0])
    assert spoken_numbers("Mars has one moon", EN) == ([], [1.0])


@pytest.mark.parametrize("question,expected,given,language,graded", [
    ("¿Cuántas horas hay en dos días?", "48", "Cuarenta.", ES, False),
    ("¿Cuántas horas hay en dos días?", "48", "No sé, creo que cuarenta.", ES, False),
    # the "two" of the question is not the answer
    ("¿Cuántas horas hay en dos días?", "48", "Dos días tienen cuarenta y ocho horas.", ES, True),
    ("¿Cuántas lunas tiene Marte?", "2", "Marte tiene una luna.", ES, False),
    ("¿Cuántas lunas tiene Marte?", "2", "Dos.", ES, True),
    ("How many moons does Mars have?", "2", "Mars has one moon.", EN, False),
    ("How many days are there in a leap year?", "366", "three hundred and sixty-six", EN, True),
    ("How many hours are there in two days?", "48", "48", EN, True),
    # left to the LLM: no number, several candidates, an expected answer that is not a number
    ("How many hours are there in two days?", "48", "I don't know", EN, None),
    ("How many hours are there in two days?", "48", "forty-eight or fifty", EN, None),
    ("¿Qué forma tiene la señal de ceda el paso?", "Un triángulo invertido", "Amarilla", ES, None),
    ("¿Qué mes tiene menos días?", "Febrero", "Febrero", ES, None),
])
def test_grade_number(question, expected, given, language, graded):
    assert grade_number(question, expected, given, language) is graded
