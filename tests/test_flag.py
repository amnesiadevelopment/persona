from src.utils.proxy_checker import flag_from_country_code


def test_flag_canada():
    assert flag_from_country_code("CA") == "\U0001F1E8\U0001F1E6"


def test_flag_lowercase():
    assert flag_from_country_code("us") == "\U0001F1FA\U0001F1F8"


def test_flag_empty():
    assert flag_from_country_code("") == ""


def test_flag_invalid():
    assert flag_from_country_code("XYZ") == ""
    assert flag_from_country_code("1") == ""
