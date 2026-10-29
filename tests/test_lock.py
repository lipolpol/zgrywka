from zgrywka import config, lock


def test_password():
    assert lock.check_password("ubuntu")
    assert not lock.check_password("Ubuntu")
    assert not lock.check_password("")


def test_password_is_asked_every_7_days():
    settings = config.Settings()
    assert lock.unlock_needed(settings, now=1_000_000_000)          # nigdy nie podano
    settings.unlocked_at = 1_000_000_000
    assert not lock.unlock_needed(settings, now=1_000_000_000 + 6 * 24 * 3600)
    assert lock.unlock_needed(settings, now=1_000_000_000 + 7 * 24 * 3600 + 1)
    assert lock.unlock_needed(settings, now=1_000_000_000 - 3 * 24 * 3600)  # cofnięty zegar
