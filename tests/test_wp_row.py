"""Unit tests for the work-package → DB row mapping. No database required."""
import db


def _wp(links=None):
    return {
        "id": 1,
        "subject": "Bug 1",
        "lockVersion": 1,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "_links": links or {},
    }


def test_wp_to_row_extracts_module_from_custom_field():
    row = db._wp_to_row(_wp({
        "customField14": {
            "href": "/api/v3/custom_options/64",
            "title": "Терра - Пользователи",
        }
    }))
    assert row["module_id"] == 64
    assert row["module_name"] == "Терра - Пользователи"


def test_wp_to_row_module_is_none_when_custom_field_absent():
    row = db._wp_to_row(_wp())
    assert row["module_id"] is None
    assert row["module_name"] is None


def test_wp_to_row_module_is_none_when_custom_field_is_empty():
    row = db._wp_to_row(_wp({"customField14": {"href": None, "title": None}}))
    assert row["module_id"] is None
    assert row["module_name"] is None
