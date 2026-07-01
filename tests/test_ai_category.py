from models.ai_category import AiCategoryEnum


def test_enum_has_three_russian_values():
    assert {e.value for e in AiCategoryEnum} == {
        "Изменения",
        "Инциденты",
        "Качество работы",
    }


def test_enum_codes_map_to_russian_labels():
    assert AiCategoryEnum.CHANGE.value == "Изменения"
    assert AiCategoryEnum.INCIDENT.value == "Инциденты"
    assert AiCategoryEnum.QUALITY.value == "Качество работы"


def test_gpt_schema_includes_category_enum():
    from dependencies.gpt import build_analysis_schema

    schema = build_analysis_schema(include_summary=False)
    category = schema["properties"]["category"]
    assert category["enum"] == ["Изменения", "Инциденты", "Качество работы"]
    assert "category" in schema["required"]


def test_gpt_schema_keeps_summary_optional_toggle():
    from dependencies.gpt import build_analysis_schema

    assert "ai_summary" not in build_analysis_schema(include_summary=False)["properties"]
    with_summary = build_analysis_schema(include_summary=True)
    assert "ai_summary" in with_summary["properties"]
    assert "category" in with_summary["required"]
