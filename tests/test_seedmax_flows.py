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


from base import BaseTest


class TestSeedMaxChildWords(BaseTest):
    """
    0038: a BIP-85 child's words on one screen. Run with the real View.run_screen and the
    Controller's back-stack rules; only the screen's display() is scripted.
    """
    def setup_method(self):
        super().setup_method()
        from seedsigner.gui.renderer import Renderer
        self.renderer = Renderer.get_instance()
        self.saved_size = (self.renderer.canvas_width, self.renderer.canvas_height)
        self.renderer.canvas_width, self.renderer.canvas_height = 480, 320

    def teardown_method(self):
        self.renderer.canvas_width, self.renderer.canvas_height = self.saved_size
        super().teardown_method()

    def drive(self, start, answers, stop_at):
        """ the Controller's loop; `answers` feed the child-words screen, in order """
        from unittest.mock import patch
        from seedsigner.gui.screens.seedmax_words import SeedMaxChildWordsScreen
        from seedsigner.views.view import BackStackView
        shown, visited = [], []
        controller = self.controller

        def display(screen):
            shown.append(list(screen.words))
            return answers.pop(0)

        controller.back_stack = [start]
        destination = start
        with patch.object(SeedMaxChildWordsScreen, "display", display), \
                patch.object(SeedMaxChildWordsScreen, "_render", lambda self: None):
            for _ in range(50):
                visited.append((destination.View_cls.__name__, (destination.view_args or {}).get("page_index")))
                if destination.View_cls.__name__ == stop_at:
                    return shown, visited
                nxt = destination.run()
                if nxt.View_cls is BackStackView:
                    nxt = controller.pop_prev_from_back_stack()
                if not controller.back_stack or controller.back_stack[-1] != nxt:
                    controller.back_stack.append(nxt)
                destination = nxt
        raise AssertionError(visited)

    def child(self, num_words=12, index=0):
        from seedsigner.views.view import Destination
        seed = Seed(["abandon"] * 11 + ["about"])
        self.controller.storage.set_pending_seed(seed)
        self.controller.storage.finalize_pending_seed()
        return Destination(seed_views.SeedWordsView, view_args=dict(
            seed_num=0, page_index=0, bip85_data=dict(child_index=index, num_words=num_words)))

    def test_all_words_on_one_screen_then_on(self):
        for num_words, pages in ((12, 3), (24, 6)):
            shown, visited = self.drive(self.child(num_words), [0], "SeedWordsBackupTestPromptView")
            expected = Seed(["abandon"] * 11 + ["about"]).get_bip85_child_mnemonic(0, num_words).split()
            assert shown == [expected], "all the child's words, once"
            assert [p for v, p in visited if v == "SeedWordsView"] == list(range(pages))
            assert getattr(self.controller, "seedmax_child_pages", None) is None
        # the BIP-85 test vector: abandon x11 about, child #0, 12 words
        twelve = Seed(["abandon"] * 11 + ["about"]).get_bip85_child_mnemonic(0, 12).split()
        assert twelve == "prosper short ramp prepare exchange stove life snack client enough purpose fold".split()

    def test_back_from_after_the_words_shows_them_again(self):
        """ Back from the backup prompt: the silent pages step back, the words come again """
        from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON
        from unittest.mock import patch
        from seedsigner.views.view import BackStackView
        start = self.child(12)
        shown, visited = self.drive(start, [0], "SeedWordsBackupTestPromptView")
        # now Back from the prompt, as the Controller does
        controller = self.controller
        prev = controller.pop_prev_from_back_stack()
        answers = [RET_CODE__BACK_BUTTON]
        from seedsigner.gui.screens.seedmax_words import SeedMaxChildWordsScreen
        seen = []

        def display(screen):
            seen.append(len(screen.words))
            return answers.pop(0)

        destination = prev
        trail = []
        with patch.object(SeedMaxChildWordsScreen, "display", display), \
                patch.object(SeedMaxChildWordsScreen, "_render", lambda self: None):
            for _ in range(10):
                trail.append((destination.View_cls.__name__, (destination.view_args or {}).get("page_index")))
                nxt = destination.run()
                if nxt.View_cls is BackStackView:
                    nxt = controller.pop_prev_from_back_stack()
                destination = nxt
                if seen:
                    break
        assert seen == [12], f"the words shown again after Back: {trail}"
        assert trail[0] == ("SeedWordsView", 2) and trail[-1] == ("SeedWordsView", 0), trail

    def test_stale_page_marker_never_skips_a_seed_words_page(self):
        """ a marker for another child or page is ignored: the words are shown """
        for stale in [((0, 5, 12), 1), ((0, 0, 24), 1), ((0, 0, 12), 2), "x", None]:
            self.controller.seedmax_child_pages = stale
            shown, visited = self.drive(self.child(12), [0], "SeedWordsBackupTestPromptView")
            assert len(shown) == 1 and len(shown[0]) == 12, stale

    def test_normal_seed_words_keep_seedsigners_pages(self):
        """ the parent's own words (no BIP-85): SeedSigner's screen, 4 words a page """
        from seedsigner.views import seedmax_bip85
        from seedsigner.gui.screens import seed_screens
        self.child(12)
        view = seed_views.SeedWordsView(seed_num=0, page_index=0)
        assert not seedmax_bip85.applies(view, seed_screens.SeedWordsScreen)
