from gettext import gettext as _
from unittest.mock import patch

# Must import test base before the Controller
from base import FlowTest, FlowStep

from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON, ButtonOption
from seedsigner.models.settings_definition import SettingsConstants, SettingsDefinition
from seedsigner.views import settings_views
from seedsigner.views.view import MainMenuView



class TestL10nFlows(FlowTest):
    def test_change_locale(self):
        settings_entry = SettingsDefinition.get_settings_entry(SettingsConstants.SETTING__LOCALE)

        # Initially we get English
        assert _(MainMenuView.SCAN.button_label) == "Scan"

        # Seed Max (0033): on the Max the language is chosen on the splash and Settings has
        # no Language entry, so SeedSigner's own flow is checked on the standard profile
        from seedsigner.hardware import hw_profile
        with patch.object(hw_profile, "is_max_profile", lambda: False):
            self.run_sequence([
                FlowStep(MainMenuView, button_data_selection=MainMenuView.SETTINGS),
                FlowStep(settings_views.SettingsMenuView, button_data_selection=ButtonOption(settings_entry.display_name)),
                FlowStep(settings_views.LocaleSelectionView, screen_return_value=1),  # Any index > 0 (English)
            ])

        # Now we don't get English
        assert _(MainMenuView.SCAN.button_label) != "Scan"
