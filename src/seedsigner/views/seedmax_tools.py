"""
Seed Max: the Tools menu of the Max (0037, Ollen 13 Nov 2026). The Home's Tools button
opens this View on the Max; SeedSigner's ToolsMenuView (pinned to 0.8.7) stays as it is.

* Calc 12th/24th word, Address explorer, Verify address: the same destinations as in
  SeedSigner's Tools.
* No "New seed" from a photo or from D6 dice (Ollen makes his seed with dice off the
  device).
* BIP-85 child seed: choose the parent seed among those loaded (fingerprints), then
  SeedSigner's own BIP-85 views (number of words, child index, the child's words). With
  no seed loaded, "Load a seed" opens SeedSigner's loader; once loaded, the parent's seed
  options also offer "BIP-85 child seed" (the BIP-85 setting is fixed on).
Any choice outside the list shown is BACK.

The multisig coordinator will also live here, when it is written (not before: no
placeholder button on a signing device).
"""
from gettext import gettext as _

from seedsigner.gui.components import FontAwesomeIconConstants, SeedSignerIconConstants
from seedsigner.gui.screens import RET_CODE__BACK_BUTTON, ButtonListScreen
from seedsigner.gui.screens.screen import ButtonOption
from seedsigner.models.settings_definition import SettingsConstants
from seedsigner.views.view import BackStackView, Destination, View


def _choice(selected, n: int):
    """ the index chosen, or None for anything that isn't one of the n entries shown """
    if isinstance(selected, int) and not isinstance(selected, bool) and 0 <= selected < n:
        return selected
    return None


class SeedMaxToolsMenuView(View):
    KEYBOARD = ButtonOption("Calc 12th/24th word", FontAwesomeIconConstants.KEYBOARD)
    ADDRESS_EXPLORER = ButtonOption("Address explorer")
    VERIFY_ADDRESS = ButtonOption("Verify address")
    BIP85 = ButtonOption("BIP-85 child seed", right_icon_name=SeedSignerIconConstants.CHEVRON_RIGHT)

    def run(self):
        # A flow left half-done (e.g. Address explorer > enter a seed > Back to Tools) must not
        # take over a seed loaded from here: clear it, as the Home does (review of 0037)
        self.controller.resume_main_flow = None
        self.controller.address_explorer_data = None
        self.controller.unverified_address = None

        button_data = [self.KEYBOARD, self.ADDRESS_EXPLORER, self.VERIFY_ADDRESS, self.BIP85]
        selected = _choice(self.run_screen(
            ButtonListScreen,
            title=_("Tools"),
            is_button_text_centered=False,
            button_data=button_data,
        ), len(button_data))
        if selected is None:
            return Destination(BackStackView)

        choice = button_data[selected]
        if choice == self.KEYBOARD:
            from seedsigner.views.tools_views import ToolsCalcFinalWordNumWordsView
            return Destination(ToolsCalcFinalWordNumWordsView)
        if choice == self.ADDRESS_EXPLORER:
            from seedsigner.views.tools_views import ToolsAddressExplorerSelectSourceView
            return Destination(ToolsAddressExplorerSelectSourceView)
        if choice == self.VERIFY_ADDRESS:
            from seedsigner.views.scan_views import ScanAddressView
            return Destination(ScanAddressView)
        return Destination(SeedMaxBIP85SelectSeedView)


class SeedMaxBIP85SelectSeedView(View):
    """ Tools > BIP-85 child seed: the parent seed """
    LOAD = ButtonOption("Load a seed", SeedSignerIconConstants.PLUS)

    def run(self):
        from seedsigner.gui.screens import seed_screens
        network = self.settings.get_value(SettingsConstants.SETTING__NETWORK)
        parents = [(num, seed) for num, seed in enumerate(self.controller.storage.seeds) if seed.bip85_supported]
        button_data = [ButtonOption(seed.get_fingerprint(network), SeedSignerIconConstants.FINGERPRINT, icon_color="blue")
                       for _num, seed in parents]
        button_data.append(self.LOAD)

        selected = _choice(self.run_screen(
            seed_screens.SeedSelectSeedScreen,
            title=_("BIP-85 child seed"),
            text=_("Select the parent seed") if parents else _("Load the parent seed"),
            is_button_text_centered=False,
            button_data=button_data,
        ), len(button_data))
        if selected is None:
            return Destination(BackStackView)
        if selected < len(parents):
            from seedsigner.views.seed_views import SeedBIP85SelectNumWordsView
            return Destination(SeedBIP85SelectNumWordsView, view_args={"seed_num": parents[selected][0]})
        from seedsigner.views.seed_views import LoadSeedView
        return Destination(LoadSeedView)
