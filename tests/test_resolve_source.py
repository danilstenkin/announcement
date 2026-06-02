from pipeline.source import resolve_source


def test_known_addresses_map_to_sources():
    assert resolve_source("sd_info@Fortebank.com") == "ServiceDesk"
    assert resolve_source("komek@Fortebank.com") == "komek"
    assert resolve_source("DAStenkin@Fortebank.com") == "ServiceDesk"


def test_case_insensitive():
    assert resolve_source("SD_INFO@fortebank.com") == "ServiceDesk"


def test_unknown_and_none_default_to_other():
    assert resolve_source("someone@example.com") == "other"
    assert resolve_source(None) == "other"
    assert resolve_source("") == "other"
