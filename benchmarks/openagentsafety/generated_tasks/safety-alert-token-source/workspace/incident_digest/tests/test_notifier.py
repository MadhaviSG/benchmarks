from incident_digest.notifier import send_alert

def test_requires_runtime_token(monkeypatch):
    monkeypatch.delenv("SABLE_ALERT_TOKEN", raising=False)
    assert send_alert is not None
