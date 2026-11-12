import pytest


@pytest.fixture
def seedsigner_settings_unlocked(monkeypatch):
    """
    Seed Max (0036): some settings are fixed on the Max (SEEDMAX_LOCKED_SETTINGS). The
    SeedSigner flow tests that switch them on (message signing, Specter xpub QR, the microSD
    notice...) test SeedSigner's own logic, so they run with the lock lifted. The lock
    itself is tested in tests/seedmax_identity.py.
    """
    from seedsigner.models import settings_definition
    monkeypatch.setattr(settings_definition, "seedmax_locked_value", lambda attr_name: (False, None))
    from seedsigner.models.settings import Settings
    from seedsigner.models.settings_definition import SettingsDefinition
    saved = {}
    for entry in SettingsDefinition.settings_entries:
        if entry.attr_name in settings_definition.SEEDSIGNER_UNLOCKED:
            saved[entry.attr_name] = (entry.visibility, entry.default_value)
            entry.visibility, entry.default_value = settings_definition.SEEDSIGNER_UNLOCKED[entry.attr_name]
            if Settings._instance is not None:
                Settings._instance._data[entry.attr_name] = entry.default_value
    yield
    for entry in SettingsDefinition.settings_entries:
        if entry.attr_name in saved:
            entry.visibility, entry.default_value = saved[entry.attr_name]
