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
