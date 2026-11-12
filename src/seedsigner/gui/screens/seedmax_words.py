"""
Seed Max (0034): typing the seed words, with the Anti-Klepto Check keyboard, and the
review of all the words before the seed is accepted.

Replaces SeedSigner's `SeedMnemonicEntryScreen` on the Max profile only (the swap is in
`View.run_screen`, see `views/seedmax_words.py`); the View that runs it,
`SeedMnemonicEntryView`, stays identical to SeedSigner 0.8.7. Same contract as the screen
it replaces: `display()` returns either a word of `wordlist` for this position, or
RET_CODE__BACK_BUTTON. Never anything else: a word is returned only if it is in the
wordlist (checked again right before returning).

Keyboard (per word, "Word 5 of 12" in the top nav, one segment per word under it):
* QWERTY; only the letters that can still lead to a BIP-39 word are lit, the others are
  dark and ignore taps;
* up to four candidate words above the keyboard (4 letters are always enough to leave a
  single one); tap a word to choose it;
* Back (as the top nav arrow) = the previous word, Delete = the last letter.

Review (only after the LAST word, not in the "calculate final word" tool, not for
Electrum seeds): all the words numbered in columns as on the backup sheet, hidden until
"Show"; with the BIP-39 checksum:
* right: "Confirm" returns the last word (SeedSigner then builds the seed as usual);
* wrong: red line and a blocked "Wrong" button; tapping a word rewrites it. The last word
  is rewritten here; an earlier one is reached by `edit_index` (see views/seedmax_words.py).
The checksum doesn't catch every mistake (a wrong word passes ~1 in 16 times with 12
words, 1 in 256 with 24): the review is there to compare with the backup, word by word.
"""
import time
from dataclasses import dataclass

from embit import bip39

from seedsigner.gui.components import Fonts, GUIConstants, TopNav
from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON, BaseTopNavScreen
from seedsigner.hardware.buttons import HardwareButtonsConstants
from seedsigner.hardware.touch import TouchEvent

W, H = 480, 320
PAD = 8
ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
BAR_Y = (51, 57)
FIELD = (PAD, 61, W - PAD, 89)
CAND_TOP, CAND_H, CAND_SLOTS, CAND_GAP = 93, 34, 4, 6
KEY_W, KEY_H, KEY_GAP, KEYS_TOP = 42, 38, 4, 131
CONTROLS = (260, 312)

# review
GRID_TOP = 52
STATUS_Y = 245
BUTTON = (PAD, 262, W - PAD, 312)

OFF_FILL, OFF_TEXT = "#0A0E2A", "#34406E"
PANEL = "#060A26"


def key_rects() -> dict:
    """ letter -> drawn rect """
    out = {}
    for r, row in enumerate(ROWS):
        width = len(row) * KEY_W + (len(row) - 1) * KEY_GAP
        x = round((W - width) / 2)
        y = KEYS_TOP + r * (KEY_H + KEY_GAP)
        for ch in row:
            out[ch] = (x, y, x + KEY_W, y + KEY_H)
            x += KEY_W + KEY_GAP
    return out


def candidate_rect(slot: int) -> tuple:
    width = (W - 2 * PAD - (CAND_SLOTS - 1) * CAND_GAP) / CAND_SLOTS
    x = PAD + slot * (width + CAND_GAP)
    return (round(x), CAND_TOP, round(x + width), CAND_TOP + CAND_H)


def control_rects() -> dict:
    width = (W - 2 * PAD - CAND_GAP) / 2
    return {"back": (PAD, CONTROLS[0], round(PAD + width), CONTROLS[1]),
            "delete": (round(PAD + width + CAND_GAP), CONTROLS[0], W - PAD, CONTROLS[1])}


def grid(count: int):
    """ (columns, rows, cell w, cell h, font size): 12 words 3 x 4, 24 words 4 x 6, as on the sheet """
    cols = 3 if count <= 12 else 4
    rows = -(-count // cols)
    return cols, rows, (W - 2 * PAD) / cols, (STATUS_Y - 10 - GRID_TOP) / rows, (20 if count <= 12 else 17)


def cell_rect(i: int, count: int) -> tuple:
    """ word i (0-based), numbered down each column like the backup sheet """
    cols, rows, cw, ch, _size = grid(count)
    c, r = divmod(i, rows)
    x, y = PAD + c * cw, GRID_TOP + r * ch
    return (round(x) + 2, round(y) + 2, round(x + cw) - 2, round(y + ch) - 2)


def inside(rect, x, y, margin=0) -> bool:
    return rect[0] - margin <= x <= rect[2] + margin and rect[1] - margin <= y <= rect[3] + margin


@dataclass
class SeedMaxWordEntryScreen(BaseTopNavScreen):
    wordlist: list = None
    word_index: int = 0
    word_count: int = 12
    current_word: str = None       # the word already in this position (going back), or None
    earlier_words: list = None     # words 1..N-1 when the review follows this (last) word, else None
    start_in_review: bool = False  # back here after rewriting an earlier word
    lang: str = "en"

    def __post_init__(self):
        from seedsigner.gui import seedmax
        self.sm = seedmax
        self.title = self.t("title", n=self.word_index + 1, m=self.word_count)
        super().__post_init__()
        self.words_set = frozenset(self.wordlist)
        self.text = self.current_word or ""
        self.mode = "keys"
        self.shown = False
        self.pending = None
        self.edit_index = None      # set when the user taps an earlier word in the review
        self.keys = key_rects()
        if self.start_in_review and self.review_applies and self.current_word in self.words_set:
            self.enter_review()

    # ---------------------------------------------------------------------------------
    def t(self, key, **values):
        return self.sm.word_text(self.lang, key, **values)

    def font(self, size, bold=True, mono=False):
        if mono:
            return Fonts.get_font("Inconsolata-SemiBold" if bold else "Inconsolata-Regular", size)
        return Fonts.get_font("OpenSans-SemiBold" if bold else "OpenSans-Regular", size)

    @property
    def review_applies(self) -> bool:
        return self.earlier_words is not None and self.word_index == self.word_count - 1

    def found(self) -> list:
        return [w for w in self.wordlist if w.startswith(self.text)] if self.text else []

    def live_letters(self) -> set:
        if not self.text:
            return {w[0] for w in self.wordlist}
        return {w[len(self.text)] for w in self.found() if len(w) > len(self.text)}

    def candidates(self) -> list:
        return self.found()[:CAND_SLOTS]

    def review_words(self) -> list:
        return list(self.earlier_words) + [self.text]

    def checksum_ok(self) -> bool:
        try:
            return bip39.mnemonic_is_valid(" ".join(self.review_words()))
        except Exception:
            return False

    def set_title(self, text):
        old = self.top_nav
        self.top_nav = TopNav(text=text, font_size=self.title_font_size, width=self.canvas_width,
                              height=GUIConstants.TOP_NAV_HEIGHT, show_back_button=True,
                              show_power_button=False)
        self.components[self.components.index(old)] = self.top_nav

    def enter_review(self):
        self.mode = "review"
        self.shown = False
        self.set_title(self.t("review"))

    def leave_review(self):
        self.mode = "keys"
        self.set_title(self.t("title", n=self.word_index + 1, m=self.word_count))

    # --------------------------------------------------------------------------- drawing
    def button(self, rect, label, kind="on", size=18, mono=False):
        d = self.image_draw
        if kind == "primary":
            d.rounded_rectangle(rect, radius=6, fill=GUIConstants.ACCENT_COLOR)
            color = GUIConstants.BACKGROUND_COLOR
        elif kind == "off":
            d.rounded_rectangle(rect, radius=6, fill=OFF_FILL, outline=GUIConstants.INACTIVE_COLOR)
            color = OFF_TEXT
        elif kind == "error":
            d.rounded_rectangle(rect, radius=6, fill="#3A0A10", outline=GUIConstants.ERROR_COLOR, width=2)
            color = GUIConstants.ERROR_COLOR
        else:
            d.rounded_rectangle(rect, radius=6, fill=GUIConstants.BUTTON_BACKGROUND_COLOR,
                                outline=GUIConstants.ACCENT_COLOR, width=1)
            color = GUIConstants.BODY_FONT_COLOR
        f = self.fit(label, size, rect[2] - rect[0] - 8, mono=mono)
        d.text(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), label, fill=color, font=f, anchor="mm")

    def fit(self, text, size, width, bold=True, mono=False, minimum=10):
        f = self.font(size, bold, mono)
        while size > minimum and self.image_draw.textlength(text, font=f) > width:
            size -= 1
            f = self.font(size, bold, mono)
        return f

    def draw_keys(self):
        d = self.image_draw
        # one segment per word: done, current, still to type
        gap = 3 if self.word_count <= 12 else 2
        seg = (W - 2 * PAD - gap * (self.word_count - 1)) / self.word_count
        for i in range(self.word_count):
            x = PAD + i * (seg + gap)
            color = (GUIConstants.ACCENT_COLOR if i == self.word_index
                     else "#2C6E8C" if i < self.word_index else GUIConstants.INACTIVE_COLOR)
            d.rectangle((round(x), BAR_Y[0], round(x + seg) - 1, BAR_Y[1]), fill=color)

        d.rounded_rectangle(FIELD, radius=4, fill=PANEL, outline=GUIConstants.ACCENT_COLOR, width=1)
        cy = (FIELD[1] + FIELD[3]) / 2
        d.text((FIELD[0] + 10, cy), self.text + "_", fill=GUIConstants.BODY_FONT_COLOR,
               font=self.font(22, mono=True), anchor="lm")
        found = self.found()
        if self.text and len(found) > CAND_SLOTS:
            d.text((FIELD[2] - 10, cy), self.t("matches", n=len(found)), fill=GUIConstants.LABEL_FONT_COLOR,
                   font=self.font(14, bold=False, mono=True), anchor="rm")

        for slot, word in enumerate(self.candidates()):
            self.button(candidate_rect(slot), word, "primary" if len(found) == 1 else "on", size=19, mono=True)

        live = self.live_letters()
        for ch, rect in self.keys.items():
            self.button(rect, ch, "on" if ch in live else "off", size=22, mono=True)

        rects = control_rects()
        self.button(rects["back"], "‹  " + self.t("back"), size=18)
        self.button(rects["delete"], self.t("delete"), "on" if self.text else "off", size=18)

    def draw_review(self):
        d = self.image_draw
        words = self.review_words()
        cols, rows, cw, ch, size = grid(self.word_count)
        f_num, f_word = self.font(size - 4, bold=False, mono=True), self.font(size, mono=True)
        ok = self.checksum_ok()
        for i, word in enumerate(words):
            rect = cell_rect(i, self.word_count)
            d.rounded_rectangle(rect, radius=4, fill=PANEL, outline=GUIConstants.BUTTON_OUTLINE_COLOR)
            cy = (rect[1] + rect[3]) / 2
            d.text((rect[0] + 30, cy), f"{i + 1}", fill=GUIConstants.LABEL_FONT_COLOR, font=f_num, anchor="rm")
            shown = word if self.shown else "•" * 5
            d.text((rect[0] + 36, cy), shown, fill=GUIConstants.BODY_FONT_COLOR, font=f_word, anchor="lm")
        if not self.shown:
            status, color = self.t("hidden"), GUIConstants.LABEL_FONT_COLOR
        elif ok:
            status, color = self.t("ok"), GUIConstants.SUCCESS_COLOR
        else:
            status, color = self.t("bad"), GUIConstants.ERROR_COLOR
        d.text((W / 2, STATUS_Y), status, fill=color, font=self.fit(status, 15, W - 2 * PAD), anchor="mm")
        if not self.shown:
            self.button(BUTTON, self.t("show"), "on", size=20)
        elif ok:
            self.button(BUTTON, self.t("confirm"), "primary", size=20)
        else:
            self.button(BUTTON, self.t("wrong"), "error", size=20)

    def _render(self):
        super()._render()
        if self.mode == "review":
            self.draw_review()
        else:
            self.draw_keys()

    # --------------------------------------------------------------------------- input
    def action_at(self, x, y):
        """ What a tap at (x, y) means on the screen as it is now, or None """
        if self.is_back_touch(x, y):
            return ("back",)
        if self.mode == "review":
            if inside(BUTTON, x, y, 3):
                if not self.shown:
                    return ("show",)
                return ("confirm",) if self.checksum_ok() else None
            if self.shown:
                for i in range(self.word_count):
                    if inside(cell_rect(i, self.word_count), x, y, 2):
                        return ("edit", i)
            return None
        for slot, word in enumerate(self.candidates()):
            if inside(candidate_rect(slot), x, y, 1):      # never reaches into the key row
                return ("word", word)
        live = self.live_letters()
        for ch, rect in self.keys.items():
            if inside(rect, x, y, KEY_GAP // 2):
                return ("letter", ch) if ch in live else None
        rects = control_rects()
        if inside(rects["back"], x, y, 3):
            return ("back",)
        if inside(rects["delete"], x, y, 3):
            return ("delete",) if self.text else None
        return None

    def handle_touch(self, event: TouchEvent):
        if not event.is_tap:
            return None
        action = self.action_at(event.x, event.y)
        if action is None:
            return None
        self.pending = action
        return HardwareButtonsConstants.KEY_PRESS

    def apply(self, action):
        """
        One user action. Returns what `display()` must return (a word or BACK), or None
        to stay on the screen.
        """
        kind = action[0]
        keys_only, review_only = ("letter", "delete", "word"), ("show", "edit", "confirm")
        if (kind in keys_only and self.mode != "keys") or (kind in review_only and self.mode != "review"):
            return None
        if kind == "letter":
            if action[1] in self.live_letters():
                self.text += action[1]
        elif kind == "delete":
            self.text = self.text[:-1]
        elif kind == "word":
            word = action[1]
            if word not in self.words_set or not word.startswith(self.text):
                return None
            self.text = word
            if self.review_applies:
                self.enter_review()
                return None
            return word
        elif kind == "back":
            if self.mode == "review":
                self.leave_review()         # back to the last word, to rewrite it
                return None
            return RET_CODE__BACK_BUTTON
        elif kind == "show":
            self.shown = True
        elif kind == "edit":
            i = action[1]
            if i == self.word_count - 1:
                self.leave_review()
                self.text = ""
                return None
            self.edit_index = i
            return RET_CODE__BACK_BUTTON
        elif kind == "confirm":
            if self.mode == "review" and self.shown and self.checksum_ok():
                return self.text
        return None

    def _run(self):
        while True:
            code = self.hw_inputs.wait_for(HardwareButtonsConstants.ALL_KEYS)
            action, self.pending = self.pending, None
            if code != HardwareButtonsConstants.KEY_PRESS or action is None:
                continue
            result = self.apply(action)
            if result == RET_CODE__BACK_BUTTON:
                return result
            if result is not None:
                if result not in self.words_set:      # never: apply() only returns wordlist words
                    continue
                return result
            with self.renderer.lock:
                self._render()
                self.renderer.show_image()
