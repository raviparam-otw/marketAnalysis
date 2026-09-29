from intraday_lab.validation import offline_checks


def test_offline_validation_checks_all_pass():
    checks=offline_checks()
    failed=[c for c in checks if not c.passed]
    assert not failed, [(c.name,c.detail) for c in failed]


def test_validation_never_places_orders(monkeypatch):
    import intraday_lab.broker as broker
    monkeypatch.setattr(broker.PaperBroker,"buy_qty",lambda *a,**k: (_ for _ in ()).throw(AssertionError("order submitted")))
    monkeypatch.setattr(broker.PaperBroker,"sell_qty",lambda *a,**k: (_ for _ in ()).throw(AssertionError("order submitted")))
    failed=[c for c in offline_checks() if not c.passed]
    assert not failed
