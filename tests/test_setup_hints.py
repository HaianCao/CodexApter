from codex_adapter.web import _HTML


def test_setup_shows_persistent_url_example_and_bearer_recommendation():
    assert '<code>https://provider.example/v1</code>' in _HTML
    assert 'Bearer token (recommended)' in _HTML
    assert 'without the Bearer prefix' in _HTML
