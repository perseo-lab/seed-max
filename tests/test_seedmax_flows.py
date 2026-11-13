"""
Seed Max (0037): the Max's own menus, through SeedSigner's FlowTest (the Controller's
real routing, screens mocked).
"""
from base import FlowTest, FlowStep

from seedsigner.models.seed import Seed
from seedsigner.views.view import MainMenuView
from seedsigner.views import seed_views, seedmax_tools, settings_views


class TestSeedMaxFlows(FlowTest):

    def test_tools_bip85_from_a_loaded_seed(self):
        seed = Seed(["abandon"] * 11 + ["about"])
        self.controller.storage.set_pending_seed(seed)
        self.controller.storage.finalize_pending_seed()
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.TOOLS),
            FlowStep(seedmax_tools.SeedMaxToolsMenuView, button_data_selection=seedmax_tools.SeedMaxToolsMenuView.BIP85),
            FlowStep(seedmax_tools.SeedMaxBIP85SelectSeedView, screen_return_value=0),
            FlowStep(seed_views.SeedBIP85SelectNumWordsView, button_data_selection=seed_views.SeedBIP85SelectNumWordsView.WORDS_12),
            FlowStep(seed_views.SeedBIP85SelectChildIndexView, screen_return_value=0),
            FlowStep(seed_views.SeedWordsWarningView),
        ])

    def test_tools_bip85_without_seed_loads_one(self):
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.TOOLS),
            FlowStep(seedmax_tools.SeedMaxToolsMenuView, button_data_selection=seedmax_tools.SeedMaxToolsMenuView.BIP85),
            FlowStep(seedmax_tools.SeedMaxBIP85SelectSeedView, button_data_selection=seedmax_tools.SeedMaxBIP85SelectSeedView.LOAD),
            FlowStep(seed_views.LoadSeedView),
        ])

    def test_seed_options_offer_bip85(self):
        """ BIP-85 fixed on: the parent's seed options also offer it """
        seed = Seed(["abandon"] * 11 + ["about"])
        self.controller.storage.set_pending_seed(seed)
        self.controller.storage.finalize_pending_seed()
        self.run_sequence([
            FlowStep(seed_views.SeedOptionsView, button_data_selection=seed_views.SeedOptionsView.BIP85_CHILD_SEED),
            FlowStep(seed_views.SeedBIP85SelectNumWordsView),
        ], initial_destination_view_args=dict(seed_num=0))

    def test_tools_back_to_home(self):
        from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.TOOLS),
            FlowStep(seedmax_tools.SeedMaxToolsMenuView, screen_return_value=RET_CODE__BACK_BUTTON),
            FlowStep(MainMenuView),
        ])

    def test_tools_clears_a_half_done_flow(self):
        """ review of 0037: Address explorer > enter a seed > Back to Tools > BIP-85 > Load a seed """
        from seedsigner.controller import Controller
        from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON
        from seedsigner.views import tools_views
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.TOOLS),
            FlowStep(seedmax_tools.SeedMaxToolsMenuView, button_data_selection=seedmax_tools.SeedMaxToolsMenuView.ADDRESS_EXPLORER),
            FlowStep(tools_views.ToolsAddressExplorerSelectSourceView, button_data_selection=tools_views.ToolsAddressExplorerSelectSourceView.TYPE_12WORD),
            FlowStep(seed_views.SeedMnemonicEntryView, screen_return_value=RET_CODE__BACK_BUTTON),
            FlowStep(tools_views.ToolsAddressExplorerSelectSourceView, screen_return_value=RET_CODE__BACK_BUTTON),
            FlowStep(seedmax_tools.SeedMaxToolsMenuView, button_data_selection=seedmax_tools.SeedMaxToolsMenuView.BIP85),
            FlowStep(seedmax_tools.SeedMaxBIP85SelectSeedView, button_data_selection=seedmax_tools.SeedMaxBIP85SelectSeedView.LOAD),
            FlowStep(seed_views.LoadSeedView),
        ])
        assert self.controller.resume_main_flow is None and self.controller.address_explorer_data is None
