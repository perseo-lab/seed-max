"""
Seed Max 0034: the seed words typed on the AKC keyboard and reviewed all together.

The seed path is security code: what SeedSigner builds the seed from must be exactly the
words the user typed and saw in the review. Checked end to end with the real
SeedMnemonicEntryView (identical to SeedSigner 0.8.7), the real word screen driven by taps
at screen coordinates, and the Controller's own back-stack rules:

* hundreds of random 12- and 24-word mnemonics typed correctly;
* the same with a wrong word typed somewhere, spotted in the review and rewritten by
  tapping it (checksum right or wrong);
* random Back taps along the way;
* whatever happens, the View only ever receives a wordlist word or BACK, and the
  mnemonic handed over equals the one in the review.
"""
import random
from unittest.mock import patch

from base import BaseTest

from embit import bip39
from embit.wordlists.bip39 import WORDLIST

from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON
from seedsigner.gui.screens import seedmax_words as screens
from seedsigner.gui.screens.seedmax_words import SeedMaxWordEntryScreen
from seedsigner.hardware.buttons import HardwareButtonsConstants
from seedsigner.hardware.touch import TouchEvent
from seedsigner.views import seedmax_words
from seedsigner.views.seed_views import SeedFinalizeView, SeedMnemonicEntryView, SeedMnemonicInvalidView
from seedsigner.views.view import BackStackView, Destination


def center(rect):
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def random_mnemonic(rng, n):
    return bip39.mnemonic_from_bytes(bytes(rng.randrange(256) for _ in range(16 if n == 12 else 32))).split()


class User:
    """
    Taps the screen like a person copying `target` from the backup sheet. `typos` maps
    word positions to a wrong word typed there the first time. In the review the user
    compares every word with the sheet and taps the first one that differs.
    `back_rate`: probability of tapping Back instead of the next tap (only before the
    review), to wander around.
    """
    def __init__(self, target, typos=None, back_rate=0.0, rng=None):
        self.target = target
        self.typos = dict(typos or {})
        self.back_rate = back_rate
        self.rng = rng or random.Random(0)
        self.screen = None
        self.taps = 0
        self.reviews_seen = []

    def word_for(self, index):
        return self.typos.get(index, self.target[index])

    def next_tap(self):
        s = self.screen
        self.taps += 1
        assert self.taps < 5000, "the user is stuck"
        if s.mode == "review":
            words = s.review_words()
            if not s.shown:
                return center(screens.BUTTON)
            self.reviews_seen.append(list(words))
            for i, w in enumerate(words):
                if w != self.target[i]:
                    self.typos.pop(i, None)         # this time it is typed right
                    return center(screens.cell_rect(i, s.word_count))
            assert s.checksum_ok()
            return center(screens.BUTTON)
        if self.back_rate and self.rng.random() < self.back_rate and s.word_index > 0:
            self.back_rate /= 2                     # wander less and less
            return (20, 20) if self.rng.random() < .5 else center(screens.control_rects()["back"])
        want = self.word_for(s.word_index)
        if not want.startswith(s.text):
            return center(screens.control_rects()["delete"])
        for slot, w in enumerate(s.candidates()):
            if w == want:
                return center(screens.candidate_rect(slot))
        return center(screens.key_rects()[want[len(s.text)]])


class TestSeedMaxWordEntry(BaseTest):

    def setup_method(self):
        super().setup_method()
        # the Renderer is a MagicMock in this suite: give it the Max's canvas size
        from seedsigner.gui.renderer import Renderer
        self.renderer = Renderer.get_instance()
        self.saved_size = (self.renderer.canvas_width, self.renderer.canvas_height)
        self.renderer.canvas_width, self.renderer.canvas_height = 480, 320

    def teardown_method(self):
        self.renderer.canvas_width, self.renderer.canvas_height = self.saved_size
        super().teardown_method()

    def drive(self, user, n, calc=False, electrum=False, max_views=2000):
        """ The Controller's loop, reduced to the word entry: returns the mnemonic handed over """
        controller = self.controller
        controller.storage.init_pending_mnemonic(n, is_electrum=electrum)
        returned = []
        real_run = seedmax_words.run

        def spy_run(view, kwargs):
            result = real_run(view, kwargs)
            returned.append((view.cur_word_index, result))
            assert result == RET_CODE__BACK_BUTTON or result in WORDLIST, result
            return result

        def wait_for(keys=[]):
            x, y = user.next_tap()
            code = user.screen.handle_touch(TouchEvent(TouchEvent.TYPE__UP, x, y, tap=True))
            return code if code is not None else HardwareButtonsConstants.KEY_RIGHT   # an ignored tap

        orig_post_init = SeedMaxWordEntryScreen.__post_init__

        def post_init(screen):
            orig_post_init(screen)
            user.screen = screen
            screen.hw_inputs.wait_for = wait_for

        handed_over = {}
        orig_convert = controller.storage.convert_pending_mnemonic_to_pending_seed

        def convert():
            handed_over["words"] = controller.storage.pending_mnemonic
            orig_convert()

        controller.back_stack = []
        destination = Destination(SeedMnemonicEntryView, view_args=dict(is_calc_final_word=calc) if calc else None)
        with patch.object(seedmax_words, "run", spy_run), \
                patch.object(SeedMaxWordEntryScreen, "__post_init__", post_init), \
                patch.object(SeedMaxWordEntryScreen, "_render", lambda self: None), \
                patch.object(controller.storage, "convert_pending_mnemonic_to_pending_seed", convert):
            controller.back_stack.append(destination)
            for _ in range(max_views):
                nxt = destination.run()
                if nxt.View_cls in (SeedFinalizeView, SeedMnemonicInvalidView) or nxt.View_cls.__name__.startswith("ToolsCalc"):
                    return nxt.View_cls, handed_over.get("words"), returned
                if nxt.View_cls is BackStackView:
                    nxt = controller.pop_prev_from_back_stack()
                    if nxt.View_cls is None:
                        return None, None, returned
                if not controller.back_stack or controller.back_stack[-1] != nxt:
                    controller.back_stack.append(nxt)
                destination = nxt
        raise AssertionError("too many views")

    # ---------------------------------------------------------------------------------
    def test_typed_right(self):
        rng = random.Random(1)
        for k in range(120):
            n = 12 if k % 2 else 24
            target = random_mnemonic(rng, n)
            user = User(target)
            view_cls, words, returned = self.drive(user, n)
            assert view_cls is SeedFinalizeView and words == target
            assert user.reviews_seen and user.reviews_seen[-1] == target
            # every word screen returned its word once, in order, then the last one after the review
            assert [r for _i, r in returned] == target

    def test_typo_found_in_review_and_rewritten(self):
        rng = random.Random(2)
        passed_checksum = 0
        for k in range(150):
            n = 12 if k % 3 else 24
            target = random_mnemonic(rng, n)
            pos = rng.randrange(n)
            wrong = rng.choice([w for w in WORDLIST if w != target[pos]])
            user = User(target, typos={pos: wrong})
            view_cls, words, returned = self.drive(user, n)
            assert view_cls is SeedFinalizeView and words == target, (pos, wrong)
            first_review = user.reviews_seen[0]
            assert first_review[pos] == wrong and first_review[:pos] + first_review[pos + 1:] == target[:pos] + target[pos + 1:]
            passed_checksum += bip39.mnemonic_is_valid(" ".join(first_review))
            # an earlier word is reached by BACK answers only, never by an extra word
            assert all(r == RET_CODE__BACK_BUTTON or r == target[i] or (i == pos and r == wrong) for i, r in returned)
        assert passed_checksum > 0, "some typos keep a valid checksum: the review is still how they're caught"

    def test_two_typos_and_wandering_back(self):
        rng = random.Random(3)
        for k in range(80):
            n = 24 if k % 2 else 12
            target = random_mnemonic(rng, n)
            a, b = rng.sample(range(n), 2)
            typos = {a: rng.choice(WORDLIST), b: rng.choice(WORDLIST)}
            typos = {i: w for i, w in typos.items() if w != target[i]}
            user = User(target, typos=typos, back_rate=0.15, rng=rng)
            view_cls, words, _ = self.drive(user, n)
            assert view_cls is SeedFinalizeView and words == target

    def test_wrong_checksum_cannot_be_confirmed(self):
        target = ["abandon"] * 11 + ["about"]
        s = SeedMaxWordEntryScreen(wordlist=WORDLIST, word_index=11, word_count=12, current_word="zoo",
                                   earlier_words=target[:11], start_in_review=True)
        assert s.mode == "review" and not s.checksum_ok()
        assert s.action_at(*center(screens.cell_rect(3, 12))) is None, "words hidden: no edit before Show"
        assert s.action_at(*center(screens.BUTTON)) == ("show",)
        s.apply(("show",))
        assert s.action_at(*center(screens.BUTTON)) is None, "wrong checksum: the button does nothing"
        assert s.apply(("confirm",)) is None, "and a forged confirm returns nothing"
        s.text = "about"
        assert s.checksum_ok() and s.action_at(*center(screens.BUTTON)) == ("confirm",)
        assert s.apply(("confirm",)) == "about"

    def test_keyboard_rules(self):
        s = SeedMaxWordEntryScreen(wordlist=WORDLIST, word_index=0, word_count=12)
        assert s.text == "" and "x" not in s.live_letters(), "no BIP-39 word starts with x"
        assert s.action_at(*center(screens.key_rects()["x"])) is None, "a dark key ignores taps"
        assert s.action_at(*center(screens.control_rects()["delete"])) is None, "nothing to delete"
        s.apply(("letter", "z"))
        assert s.text == "z" and s.candidates() == ["zebra", "zero", "zone", "zoo"]
        assert s.apply(("letter", "q")) is None and s.text == "z", "a letter that leads nowhere is not typed"
        assert s.apply(("word", "abandon")) is None, "a word that doesn't match what's typed is refused"
        assert s.apply(("word", "zzzz")) is None, "a word not in the list is refused"
        assert s.apply(("word", "zoo")) == "zoo"
        s = SeedMaxWordEntryScreen(wordlist=WORDLIST, word_index=4, word_count=12, current_word="actor")
        assert s.text == "actor" and s.candidates() == ["actor"]
        assert s.apply(("delete",)) is None and s.text == "acto"
        assert s.apply(("back",)) == RET_CODE__BACK_BUTTON
        # 4 slots are enough: every word shows among the candidates within its first 4 letters
        matches = lambda p: [w for w in WORDLIST if w.startswith(p)]
        assert all(any(w in matches(w[:k])[:4] for k in range(1, 5)) for w in WORDLIST)
        # key and candidate zones never overlap
        rects = list(screens.key_rects().values()) + [screens.candidate_rect(i) for i in range(4)] + list(screens.control_rects().values())
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)
            assert 0 <= a[0] and a[2] <= 480 and 48 < a[1] and a[3] <= 320

    def test_no_review_for_calc_final_word_and_electrum(self):
        rng = random.Random(4)
        target = random_mnemonic(rng, 12)
        user = User(target)
        view_cls, _words, _ = self.drive(user, 12, calc=True)
        assert view_cls.__name__.startswith("ToolsCalc") and not user.reviews_seen
        user = User(target)
        with patch("seedsigner.models.seed_storage.ElectrumSeed"):
            self.drive(user, 12, electrum=True)
        assert not user.reviews_seen, "Electrum seeds: SeedSigner's own path, no BIP-39 review"

    def test_stale_jump_is_ignored(self):
        rng = random.Random(5)
        target = random_mnemonic(rng, 12)
        J = seedmax_words.Jump
        for stale in [("back_to", 0, 7), ("forward", 11, 3, 0), "bogus", None,
                      J("back_to", 0, 0, 0, None), J("forward", 11, 0, 1, "zoo"), J("back_to", 2, 0, 99, None),
                      J("forward", 11, 0, 0, None), J("back_to", 0, 0, 1, "zzzz")]:
            self.controller.seedmax_word_jump = stale
            user = User(target)
            view_cls, words, returned = self.drive(user, 12)
            assert view_cls is SeedFinalizeView and words == target, stale
            assert [r for _i, r in returned] == target, f"{stale}: no silent back or skip"
            assert getattr(self.controller, "seedmax_word_jump", None) is None

    def test_jump_from_another_entry_with_words_stored(self):
        """
        review finding M1: a jump left over (e.g. by an exception) must not act on a later
        entry that starts with words already stored ("Review & edit")
        """
        rng = random.Random(6)
        target = random_mnemonic(rng, 12)
        storage = self.controller.storage
        for leftover_entry_offset in (1, -1):
            storage.init_pending_mnemonic(12)
            for i, w in enumerate(target):
                storage.update_pending_mnemonic(w, i)
            entry = getattr(self.controller, seedmax_words.ENTRY_ATTR, 0) + leftover_entry_offset
            self.controller.seedmax_word_jump = seedmax_words.Jump("back_to", 0, 0, entry, None)
            shown = []
            orig = seedmax_words._show

            def spy(view, *a, **k):
                shown.append(view.cur_word_index)
                return orig(view, *a, **k)

            user = User(target)
            with patch.object(seedmax_words, "_show", spy), \
                    patch.object(storage, "init_pending_mnemonic", lambda *a, **k: None):
                view_cls, words, _ = self.drive(user, 12)
            assert view_cls is SeedFinalizeView and words == target
            assert shown == list(range(12)), f"every word shown, none skipped: {shown}"

    def test_home_and_errors_clear_the_jump(self):
        """ a jump never survives the Home screen or an error (the Controller clears it) """
        J = seedmax_words.Jump
        self.controller.seedmax_word_jump = J("back_to", 0, 0, 1, None)
        with patch("seedsigner.views.view.UnhandledExceptionView", create=True):
            try:
                self.controller.handle_exception(RuntimeError("boom"))
            except Exception:
                pass
        assert self.controller.seedmax_word_jump is None
        import inspect
        from seedsigner.controller import Controller
        src = inspect.getsource(Controller.start)
        home = src[src.index("if next_destination.View_cls == MainMenuView:"):src.index("logger.info(f\"\\nback_stack")]
        assert "self.seedmax_word_jump = None" in home, "Home wipes the jump with the other temp state"

    def test_last_word_kept_after_a_rewrite(self):
        """ review Info-B: after rewriting an earlier word, the review comes back with the last word as typed """
        rng = random.Random(8)
        for pos in (0, 4, 10):
            target = random_mnemonic(rng, 12)
            wrong = "zoo" if target[pos] != "zoo" else "zone"
            user = User(target, typos={pos: wrong})
            screens_seen = []
            orig = seedmax_words._show

            def spy(view, kwargs, index, count, current, earlier, start_in_review):
                screens_seen.append((index, start_in_review))
                return orig(view, kwargs, index, count, current, earlier, start_in_review)

            with patch.object(seedmax_words, "_show", spy):
                view_cls, words, _ = self.drive(user, 12)
            assert view_cls is SeedFinalizeView and words == target
            assert screens_seen[-2:] == [(pos, False), (11, True)], screens_seen[-3:]

    def test_no_seed_word_in_the_logs(self, caplog):
        import logging
        caplog.set_level(logging.DEBUG)
        rng = random.Random(9)
        target = random_mnemonic(rng, 24)
        user = User(target, typos={3: "zoo" if target[3] != "zoo" else "zone"}, back_rate=0.2, rng=rng)
        self.controller.seedmax_word_jump = seedmax_words.Jump("forward", 23, 5, -1, target[23])
        view_cls, words, _ = self.drive(user, 24)
        assert words == target
        text = caplog.text
        assert "cleared" in text, "the stale jump was logged as cleared"
        assert not any(f" {w} " in f" {text} " or f"'{w}'" in text for w in set(target) | {"zoo", "zone"}), text[-500:]

    def test_back_while_rewriting_returns_to_review(self):
        """ review finding L1: Back on the word being rewritten keeps it and goes back to the review """
        rng = random.Random(7)
        for pos in (0, 1, 5):
            target = random_mnemonic(rng, 12)
            wrong = "zoo" if target[pos] != "zoo" else "zone"
            user = User(target, typos={pos: wrong})
            orig_next = user.next_tap
            state = {"backed": False}

            def next_tap():
                s = user.screen
                if (not state["backed"] and s.mode == "keys" and s.word_index == pos and not s.text
                        and len(user.reviews_seen) == 1):
                    state["backed"] = True
                    user.typos[pos] = wrong          # pretend: back without fixing
                    return center(screens.control_rects()["back"])
                return orig_next()

            user.next_tap = next_tap
            view_cls, words, _ = self.drive(user, 12)
            assert state["backed"] and view_cls is SeedFinalizeView and words == target
            assert user.reviews_seen[1][pos] == wrong, "back from the rewrite: the review again, word unchanged"

    def test_actions_only_in_their_mode(self):
        """ review finding L2 """
        s = SeedMaxWordEntryScreen(wordlist=WORDLIST, word_index=11, word_count=12, current_word="about",
                                   earlier_words=["abandon"] * 11, start_in_review=True)
        for action in (("letter", "x"), ("delete",), ("word", "act")):
            assert s.apply(action) is None and s.text == "about" and s.mode == "review", action
        s = SeedMaxWordEntryScreen(wordlist=WORDLIST, word_index=3, word_count=12, current_word="act")
        for action in (("show",), ("confirm",), ("edit", 1)):
            assert s.apply(action) is None and s.edit_index is None and s.mode == "keys", action

    def test_candidates_never_take_a_key_tap(self):
        """ review finding L4: a tap anywhere on a key is that key """
        s = SeedMaxWordEntryScreen(wordlist=WORDLIST, word_index=0, word_count=12)
        s.text = "b"
        for ch in "qwertyuiop":
            x1, y1, x2, y2 = screens.key_rects()[ch]
            for y in range(y1, y1 + 3):
                a = s.action_at((x1 + x2) // 2, y)
                assert a is None or a[0] == "letter", (ch, y, a)

    def test_standard_profile_keeps_seedsigner_screen(self):
        from seedsigner.gui.screens import seed_screens
        from seedsigner.hardware import hw_profile
        view = SeedMnemonicEntryView()
        assert seedmax_words.applies(view, seed_screens.SeedMnemonicEntryScreen)
        with patch.object(hw_profile, "is_max_profile", lambda: False):
            assert not seedmax_words.applies(view, seed_screens.SeedMnemonicEntryScreen)
        assert not seedmax_words.applies(view, seed_screens.SeedFinalizeScreen)
