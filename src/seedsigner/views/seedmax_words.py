"""
Seed Max (0034): runs the AKC-style word entry (gui/screens/seedmax_words.py) in place of
SeedSigner's SeedMnemonicEntryScreen, without changing SeedMnemonicEntryView (pinned to
SeedSigner 0.8.7 by tests/test_seedmax_identity.py).

`View.run_screen()` calls `run()` here only for SeedMnemonicEntryView on the Max profile.
What goes back to the View is exactly what SeedSigner's screen could return: a word of the
wordlist for this position, or RET_CODE__BACK_BUTTON. The View then does what it always
does with it (stores the word, moves on, finalizes the seed, or goes back one word).

Rewriting an earlier word from the review (`edit_index`) is done only with those two
answers, through a `Jump` kept on the controller (`controller.seedmax_word_jump`):
* BACK_TO word i: each word screen after i answers BACK at once, without showing, until
  the View of word i, which shows the keyboard (empty: the word is typed again; Back there
  leaves the word as it was);
* FORWARD: after word i, each following word screen answers at once the word ALREADY
  STORED in its position (the one shown in the review), until the last word, whose screen
  opens directly on the review with the last word typed before (it is not stored until
  Confirm, so the jump carries it; it is checked against the wordlist again).
Each jump names the word position it expects next and the entry it belongs to (a number
that changes every time an entry starts at word 1 without a jump). Anything unexpected
(another position or entry, no stored word, a word not in the wordlist) clears it and the
keyboard shows normally. The jump is cleared before each step and set again only by that
step. Jumps hold seed words: they are never logged.
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

JUMP_ATTR = "seedmax_word_jump"
ENTRY_ATTR = "seedmax_word_entry_id"
BACK_TO, FORWARD = "back_to", "forward"


@dataclass(frozen=True)
class Jump:
    kind: str
    target: int          # BACK_TO: the word to rewrite; FORWARD: the last word
    expected: int        # the word position this jump must meet next
    entry: int           # the entry it belongs to
    last_word: str       # the last word as typed before the review (not stored yet)

    def __repr__(self):
        return f"Jump({self.kind}, {self.target}, {self.expected})"     # never the word


def applies(view, Screen_cls) -> bool:
    from seedsigner.hardware import hw_profile
    if not hw_profile.is_max_profile():
        return False
    from seedsigner.gui.screens import seed_screens
    from seedsigner.views.seed_views import SeedMnemonicEntryView
    return Screen_cls is seed_screens.SeedMnemonicEntryScreen and isinstance(view, SeedMnemonicEntryView)


def _take_jump(controller):
    """ The jump left by the previous step, removed from the controller """
    jump = getattr(controller, JUMP_ATTR, None)
    setattr(controller, JUMP_ATTR, None)
    return jump if isinstance(jump, Jump) else None


def _set_jump(controller, jump):
    setattr(controller, JUMP_ATTR, jump)


def run(view, kwargs: dict):
    from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON

    controller = view.controller
    storage = controller.storage
    index = view.cur_word_index
    count = storage.pending_mnemonic_length
    words_set = frozenset(kwargs["wordlist"])
    stored = storage.get_pending_mnemonic_word(index)
    last = count - 1

    # the review follows the last word, except in the "calculate final word" tool and for Electrum
    earlier = None
    if index == last and not view.is_calc_final_word and not getattr(storage, "_pending_is_electrum", False):
        earlier = [storage.get_pending_mnemonic_word(i) for i in range(last)]
        if any(w not in words_set for w in earlier):
            earlier = None      # an earlier position not filled (can't happen in the normal flow)

    jump = _take_jump(controller)
    entry = getattr(controller, ENTRY_ATTR, 0)
    valid = (jump is not None and jump.expected == index and jump.entry == entry
             and (jump.last_word is None or jump.last_word in words_set))
    if jump is not None and not valid:
        logger.info(f"Seed Max word jump cleared at word {index}")
    if not valid and index == 0:
        entry += 1                                          # a new entry starts here
        setattr(controller, ENTRY_ATTR, entry)

    current, start_in_review = stored, False
    if valid and jump.kind == BACK_TO and index > jump.target:
        _set_jump(controller, Jump(BACK_TO, jump.target, index - 1, entry, jump.last_word))
        return RET_CODE__BACK_BUTTON                        # step back, silently

    if valid and jump.kind == BACK_TO and index == jump.target and stored in words_set and index < last:
        # the word to rewrite: typed again from scratch. Back here cancels the rewrite (the
        # word stays as it was) and goes back to the review, instead of SeedSigner's
        # "previous word", which on word 1 would discard the whole entry.
        result = _show(view, kwargs, index, count, None, None, False)
        if result == RET_CODE__BACK_BUTTON:
            result = stored
        _set_jump(controller, Jump(FORWARD, last, index + 1, entry, jump.last_word))
        return result

    if valid and jump.kind == FORWARD and jump.target == last and index < last and stored in words_set:
        _set_jump(controller, Jump(FORWARD, last, index + 1, entry, jump.last_word))
        return stored                                       # unchanged, as shown in the review

    if valid and jump.kind == FORWARD and jump.target == last and index == last and earlier is not None:
        if jump.last_word in words_set:
            current, start_in_review = jump.last_word, True

    result = _show(view, kwargs, index, count, current, earlier, start_in_review)
    screen = view.screen
    if result == RET_CODE__BACK_BUTTON and screen is not None and screen.edit_index is not None:
        target = screen.edit_index
        if 0 <= target < index:
            last_word = screen.text if screen.text in words_set else None
            _set_jump(controller, Jump(BACK_TO, target, index - 1, entry, last_word))
    return result


def _show(view, kwargs, index, count, current, earlier, start_in_review):
    from seedsigner.gui.screens.seedmax_words import SeedMaxWordEntryScreen
    from seedsigner.models.settings_definition import SettingsConstants
    lang = view.settings.get_value(SettingsConstants.SETTING__LOCALE) or "en"
    view.screen = SeedMaxWordEntryScreen(
        wordlist=kwargs["wordlist"],
        word_index=index,
        word_count=count,
        current_word=current,
        earlier_words=earlier,
        start_in_review=start_in_review,
        lang=lang,
    )
    return view.screen.display()
