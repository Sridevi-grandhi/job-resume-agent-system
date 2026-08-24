"""Tests for app/services/keyword_bold_formatter.py."""

from __future__ import annotations

from app.services.keyword_bold_formatter import bold_important_keywords, boldImportantKeywords


def test_one_keyword_is_bolded():
    result = bold_important_keywords("Experienced with Java development.", ["Java"])
    assert "**Java**" in result


def test_multiple_keywords_are_bolded():
    result = bold_important_keywords("Used Java and Docker in production.", ["Java", "Docker"])
    assert "**Java**" in result
    assert "**Docker**" in result


def test_case_insensitive_matching():
    result = bold_important_keywords("Skilled in java programming.", ["Java"])
    assert "**java**" in result


def test_word_boundary_behavior():
    result = bold_important_keywords("Javascript is different from Java.", ["Java"])
    assert "**Javascript**" not in result
    assert "**Java**" in result


def test_java_does_not_match_javascript():
    result = bold_important_keywords("Built apps with JavaScript only.", ["Java"])
    assert "Java**" not in result.replace("JavaScript", "")
    assert "JavaScript" in result
    assert "**JavaScript**" not in result


def test_cplusplus_handled_safely():
    result = bold_important_keywords("Wrote C++ services.", ["C++"])
    assert "**C++**" in result


def test_csharp_handled_safely():
    result = bold_important_keywords("Wrote C# services.", ["C#"])
    assert "**C#**" in result


def test_dotnet_handled_safely():
    result = bold_important_keywords("Built apps with .NET.", [".NET"])
    assert "**.NET**" in result


def test_ci_cd_handled_safely():
    result = bold_important_keywords("Maintained CI/CD pipelines.", ["CI/CD"])
    assert "**CI/CD**" in result


def test_existing_bold_markers_not_duplicated():
    result = bold_important_keywords("Already **Java** bolded.", ["Java"])
    assert result.count("**Java**") == 1
    assert "****Java****" not in result


def test_empty_text_returns_empty_string():
    assert bold_important_keywords("", ["Java"]) == ""


def test_empty_keyword_list_returns_original_text():
    text = "No keywords here."
    assert bold_important_keywords(text, []) == text


def test_duplicate_keywords_do_not_double_bold():
    result = bold_important_keywords("Java developer using Java daily.", ["Java", "Java"])
    assert result.count("**Java**") == 2


def test_overlapping_keywords_longest_first():
    result = bold_important_keywords("Spring Boot developer.", ["Spring", "Spring Boot"])
    assert "**Spring Boot**" in result
    assert "Spring** **Boot" not in result


def test_punctuation_around_keywords():
    result = bold_important_keywords("Skills: Java, Docker, AWS.", ["Java", "Docker", "AWS"])
    assert "**Java**" in result
    assert "**Docker**" in result
    assert "**AWS**" in result


def test_contact_lines_are_not_bolded():
    text = "jane@example.com | linkedin.com/in/jane | github.com/jane"
    result = bold_important_keywords(text, ["Jane"])
    assert result == text


def test_date_only_lines_are_not_bolded():
    text = "Jan 2018 - Present"
    result = bold_important_keywords(text, ["2018"])
    assert result == text


def test_formatting_does_not_alter_unrelated_text():
    text = "Professional Summary\nExperienced engineer."
    result = bold_important_keywords(text, ["Python"])
    assert result == text


def test_repeated_execution_is_idempotent_on_output():
    once = bold_important_keywords("Java developer.", ["Java"])
    twice = bold_important_keywords(once, ["Java"])
    assert once == twice


def test_camel_case_alias_boldImportantKeywords_matches_snake_case():
    text = "Built services with Java."
    assert boldImportantKeywords(text, ["Java"]) == bold_important_keywords(text, ["Java"])


def test_falsy_keywords_are_ignored():
    result = bold_important_keywords("Java developer.", ["Java", "", None])
    assert "**Java**" in result
