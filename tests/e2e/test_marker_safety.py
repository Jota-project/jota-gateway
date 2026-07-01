def test_this_file_is_deselected_by_default():
    """If this test ever runs under a bare `pytest`, the marker safety net is broken."""
    assert False, "tests/e2e ran without -m e2e_real — addopts safety net is broken"
