from codexapter.web import _HTML


def test_setup_shows_persistent_url_example_and_automatic_auth_recommendation():
    assert "<code>https://provider.example/v1</code>" in _HTML
    assert "Automatic: Bearer, or x-api-key if the provider needs it (recommended)" in _HTML
    assert '<option value="bearer">Bearer token</option>' in _HTML
    assert "without the Bearer prefix" in _HTML
