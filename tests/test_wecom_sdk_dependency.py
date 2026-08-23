def test_official_wecom_sdk_is_importable():
    from wecom_aibot_sdk import WSClient

    assert WSClient is not None
