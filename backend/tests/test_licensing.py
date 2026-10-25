import pytest

from app.licensing import classify_license


@pytest.mark.parametrize(
    "value,expected",
    [
        ("mit", "commercial"),
        ("apache-2.0", "commercial"),
        ("Attribution 4.0 International (CC BY 4.0)", "commercial"),
        ("CC0: Public Domain", "commercial"),
        ("cc-by-nc-4.0", "non_commercial"),
        ("Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)", "non_commercial"),
        ("Data files © Original Authors", "unknown"),
        ("other", "unknown"),
        (None, "unknown"),
        ("For research only", "research_only"),
    ],
)
def test_classify(value, expected):
    assert classify_license(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("mit", {"commercial": True, "redistribution": True, "attribution": True, "share_alike": False}),
        ("cc0-1.0", {"commercial": True, "redistribution": True, "attribution": False, "share_alike": False}),
        ("cc-by-sa-4.0", {"commercial": True, "redistribution": True, "attribution": True, "share_alike": True}),
        ("cc-by-nc-4.0", {"commercial": False, "redistribution": True, "attribution": True, "share_alike": False}),
        ("YouTube Standard License", {"commercial": False, "redistribution": False, "attribution": True, "share_alike": False}),
        (None, {"commercial": None, "redistribution": None, "attribution": None, "share_alike": None}),
        ("gpl-3.0", {"commercial": True, "redistribution": True, "attribution": True, "share_alike": True}),
    ],
)
def test_rights(value, expected):
    from app.licensing import license_rights

    assert license_rights(value) == expected
