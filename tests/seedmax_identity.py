"""
Seed Max 0031: the signing SD's own look. Run in its own interpreter by
tests/test_seedmax_identity.py (hardware modules are replaced by mocks here), on a
480x320 canvas, with the real screens.

What is checked:
* the splash: the language chips (only languages present in the image, in a fixed
  order), every text fitting its space in every language, a chip setting the display
  language in memory and never on the microSD, START as the only way out;
* the Home top nav: "SEED MAX" in place of the title, the battery never touching it;
* the Home buttons: icon and label side by side, the cyan edge unbroken;
* the palette: SeedSigner's semantic colours unchanged;
* outside the Max profile, SeedSigner's own splash.
"""
import builtins
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

sys.modules["RPi"] = MagicMock()
sys.modules["RPi.GPIO"] = MagicMock()
sys.modules["spidev"] = MagicMock()
sys.modules["seedsigner.hardware.microsd"] = MagicMock()

from PIL import Image, ImageDraw

from seedsigner.gui.renderer import Renderer


class TestRenderer(Renderer):
    @classmethod
    def configure_instance(cls):
        renderer = cls.__new__(cls)
        cls._instance = renderer
        renderer.canvas_width, renderer.canvas_height = 480, 320
        renderer.canvas = Image.new("RGB", (480, 320))
        renderer.draw = ImageDraw.Draw(renderer.canvas)
        renderer.lock = threading.Lock()
        renderer.disp = MagicMock()
        renderer.shows = 0

    def show_image(self, *args, **kwargs):
        self.shows += 1

    def show_image_pan(self, *args, **kwargs):
        pass


TestRenderer.configure_instance()
Renderer.get_instance = classmethod(lambda cls: TestRenderer._instance)

from seedsigner.gui import seedmax
from seedsigner.gui.components import GUIConstants, SeedSignerIconConstants
from seedsigner.gui.screens.screen import ButtonOption, MainMenuScreen, component_rect
from seedsigner.hardware import hw_profile
from seedsigner.hardware.buttons import HardwareButtonsConstants as K
from seedsigner.hardware.touch import TouchEvent
from seedsigner.models.settings import Settings
from seedsigner.models.settings_definition import SettingsConstants
from seedsigner.views import screensaver
from seedsigner.views.screensaver import OpeningSplashScreen, OpeningSplashView, SeedMaxSplashScreen

R = TestRenderer._instance
failures = []


def check(condition, message):
    print(("ok    " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


def tap(x, y):
    return TouchEvent(TouchEvent.TYPE__UP, int(x), int(y), tap=True)


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def center(rect):
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


ALL = [(c, c) for c in ("en", "it", "fr", "es", "de", "nl", "ca")]


def set_locale(code):
    Settings.get_instance()._data[SettingsConstants.SETTING__LOCALE] = code
    Settings.get_instance().load_locale()


def splash(detected=ALL, **kwargs):
    with patch.object(SettingsConstants, "get_detected_languages", classmethod(lambda cls: list(detected))):
        s = SeedMaxSplashScreen(**kwargs)
    with R.lock:
        s._render()
    return s


# ---------------------------------------------------------------------------------------
def test_palette():
    # SeedSigner 0.8.7's semantic colours: a warning must still look like a warning
    expected = dict(WARNING_COLOR="#FFD60A", DIRE_WARNING_COLOR="#FF5700", ERROR_COLOR="#FF1B0A",
                    SUCCESS_COLOR="#30D158", INFO_COLOR="#409CFF", TESTNET_COLOR="#00F100", REGTEST_COLOR="#00CAF1")
    for name, value in expected.items():
        check(getattr(GUIConstants, name).upper() == value, f"{name} unchanged ({value})")
    check(GUIConstants.ACCENT_COLOR.upper() == "#45E6D6" and GUIConstants.BACKGROUND_COLOR.upper() == "#0A0E30",
          "accent cyan, background night blue")
    check(GUIConstants.BUTTON_SELECTED_FONT_COLOR == GUIConstants.BACKGROUND_COLOR, "selected text: dark on cyan")
    check(GUIConstants.MAINNET_COLOR.upper() == "#FF9F0A" and GUIConstants.MAINNET_COLOR != GUIConstants.REGTEST_COLOR
          and GUIConstants.MAINNET_COLOR != GUIConstants.TESTNET_COLOR, "mainnet bitcoin icon orange, unlike testnet and regtest")


def test_amount_icon_colour_per_network():
    from seedsigner.gui.components import BtcAmount
    for network, color in ((SettingsConstants.MAINNET, GUIConstants.MAINNET_COLOR),
                           (SettingsConstants.REGTEST, GUIConstants.REGTEST_COLOR),
                           (SettingsConstants.TESTNET, GUIConstants.TESTNET_COLOR)):
        data = dict(Settings.get_instance()._data)
        data[SettingsConstants.SETTING__NETWORK] = network
        with patch.object(Settings.get_instance(), "_data", data):
            img = Image.new("RGB", (480, 60), rgb(GUIConstants.BACKGROUND_COLOR))
            amount = BtcAmount(image_draw=ImageDraw.Draw(img), canvas=img, total_sats=1250000, screen_y=10)
            amount.render()
        want = rgb(color)
        hits = sum(1 for p in img.getdata() if p == want)
        check(hits > 20, f"{network}: bitcoin icon in {color} ({hits} px)")


def test_hold_bar_visible():
    """ review 0031 #1: the hold bar contrasts with the selected (cyan) button """
    from seedsigner.gui.screens.psbt_screens import PSBTFinalizeScreen
    s = PSBTFinalizeScreen(button_data=[ButtonOption("Approve transaction")])
    with R.lock:
        s._render()
    s._render_hold_progress(0.5)
    x1, y1, x2, y2 = component_rect(s.buttons[s.selected_button])
    bar = R.canvas.getpixel((x1 + int((x2 - x1) * 0.25), y2 - 3))
    button = R.canvas.getpixel((x1 + int((x2 - x1) * 0.75), y2 - 3))
    check(bar == rgb(GUIConstants.HOLD_BAR_COLOR) and button == rgb(GUIConstants.ACCENT_COLOR),
          f"bar {bar} on the cyan button {button}")
    contrast = sum(abs(a - b) for a, b in zip(bar, button))
    check(contrast > 400, f"bar and button far apart in colour ({contrast})")


def test_psbt_math_readable():
    """ review 0031 #6: the change line and every amount stay visible on night blue """
    from seedsigner.gui.screens.psbt_screens import PSBTMathScreen
    s = PSBTMathScreen(input_amount=1250000000, num_inputs=2, spend_amount=516449130, num_recipients=5,
                       fee_amount=7614, change_amount=733543256)
    with R.lock:
        s._render()
    bg = rgb(GUIConstants.BACKGROUND_COLOR)
    body = R.canvas.crop((0, s.top_nav.height, 480, component_rect(s.buttons[0])[1]))
    accent = sum(1 for p in body.getdata() if abs(p[0] - 69) < 40 and p[1] > 150 and p[2] > 140)
    check(accent > 150, f"\"btc change\" drawn in the accent ({accent} px)")
    far = sum(1 for p in body.getdata() if sum(abs(a - b) for a, b in zip(p, bg)) > 150)
    check(far > 1500, f"amounts and labels stand out from the background ({far} px)")


def test_language_chips():
    set_locale("en")
    s = splash()
    check([c for c, _d, _t in seedmax.chip_rects(s.languages)] == ["en", "it", "fr", "es", "de"],
          f"chips EN IT FR ES DE, in that order (detected also nl, ca): {s.languages}")
    s = splash([("en", "en"), ("de", "de"), ("ja", "ja")])
    check(s.languages == ["en", "de"], f"only detected languages get a chip: {s.languages}")
    s = splash([("en", "en")])
    check(s.languages == [], "only English in the image: no chips at all")
    before = R.canvas.copy()
    check(s.handle_touch(tap(440, 14)) is None and R.canvas.tobytes() == before.tobytes(),
          "without chips a tap in the chip corner does nothing")

    s = splash()
    rects = seedmax.chip_rects(s.languages)
    for i, (code, drawn, touch) in enumerate(rects):
        check(touch[0] <= drawn[0] and touch[2] >= drawn[2] and touch[1] <= drawn[1] and touch[3] >= drawn[3],
              f"{code}: tap zone covers the drawn chip")
        if i:
            check(rects[i - 1][2][2] <= touch[0], f"{code}: tap zones don't overlap")
        check(drawn[2] - drawn[0] >= 40 and touch[3] - touch[1] >= 34, f"{code}: big enough for a stylus or finger")
    start = s.start_rect()
    check(all(t[3] < start[1] for _c, _d, t in rects), "chip zones far from START")
    left, right = rects[0][1][0], rects[-1][1][2]
    check(abs(left - (480 - right)) <= 1, f"0033: chips centred ({left} px left, {480 - right} px right)")
    check(all(rects[i][1][0] - rects[i - 1][1][2] >= 8 for i in range(1, len(rects))), "0033: at least 8 px between chips")
    check(max(t[3] for _c, _d, t in rects) < seedmax.WORDMARK_Y[0], "chip tap zones end above \"SEED MAX\"")
    order = [seedmax.CHIP_TOP + seedmax.CHIP_H, seedmax.WORDMARK_Y[0], seedmax.WORDMARK_Y[1], seedmax.DEVICE_CY,
             seedmax.FEATURES_Y[0], seedmax.FEATURES_Y[1], seedmax.GUARANTEE[1], seedmax.GUARANTEE[3], start[1]]
    check(all(b - a >= 8 for a, b in zip(order, order[1:])), f"0033: the blocks are stacked with air between: {order}")


def test_texts_fit():
    """ every text fits its space, and (0033) none shrinks below a readable size """
    d = ImageDraw.Draw(Image.new("RGB", (480, 320)))
    mono = lambda size: seedmax_font("Inconsolata-SemiBold", size)
    semi = lambda n: seedmax_font("OpenSans-SemiBold", n)
    mono_r = lambda n: seedmax_font("Inconsolata-Regular", n)
    inner = seedmax.GUARANTEE_INNER
    for lang in seedmax.STRINGS:
        lines = [("g1", seedmax.text(lang, "g1"), semi, seedmax.G1_SIZE, inner, 15)]
        g2 = seedmax.guarantee_lines(lang)
        check(len(g2) == 2 and " ".join(g2) == seedmax.text(lang, "g2").replace(" · ", " "),
              f"{lang}: second guarantee line split in two, nothing lost")
        lines += [("g2", line, mono_r, seedmax.G2_SIZE, inner, 13) for line in g2]
        lines.append(("foot", seedmax.text(lang, "foot"), mono_r, seedmax.FOOTER_SIZE, 480 - 16, 12))
        lines += [("chip", label, mono, seedmax.FEATURE_SIZE, seedmax.FEATURE_W - 10, 15)
                  for label in seedmax.text(lang, "chips")]
        for key, text, loader, size, width, minimum in lines:
            f = seedmax.fit(d, text, loader, size, width)
            check(d.textlength(text, font=f) <= width and f.size >= minimum,
                  f"{lang} {key} {text[:24]!r}: fits at {f.size} px (min {minimum})")
        rect = seedmax.start_rect(d, seedmax.text(lang, "start"), mono)
        check(rect[0] >= 40 and rect[2] <= 440 and rect[1] > seedmax.GUARANTEE[3],
              f"{lang} START button inside the screen, below the guarantee: {rect}")
        check("{" not in seedmax.text(lang, "g1") + seedmax.text(lang, "foot"), f"{lang}: versions filled in")
        check(seedmax.BASE_VERSION in seedmax.text(lang, "g1") and seedmax.SEEDMAX_VERSION in seedmax.text(lang, "foot"),
              f"{lang}: guarantee names SeedSigner {seedmax.BASE_VERSION}, footer v{seedmax.SEEDMAX_VERSION}")
    xs = [seedmax.W / 2 + (i - 1) * seedmax.FEATURE_STEP for i in range(3)]
    check(xs[0] - seedmax.FEATURE_W / 2 >= 8 and xs[2] + seedmax.FEATURE_W / 2 <= seedmax.W - 8
          and seedmax.FEATURE_STEP > seedmax.FEATURE_W, "the three feature chips inside the screen, apart")
    check(seedmax.FEATURES_Y[1] < seedmax.GUARANTEE[1], "feature chips above the guarantee box")


def seedmax_font(name, size):
    from seedsigner.gui.components import Fonts
    return Fonts.get_font(name, size)


def test_chip_sets_language_in_memory_only():
    set_locale("en")
    check(Settings.get_instance().get_value(SettingsConstants.SETTING__PERSISTENT_SETTINGS) == SettingsConstants.OPTION__DISABLED,
          "Persistent Settings off (the default)")
    s = splash()
    rects = {c: t for c, _d, t in seedmax.chip_rects(s.languages)}
    written = []
    real_open = builtins.open

    def spy_open(path, mode="r", *args, **kwargs):
        if "w" in mode or "a" in mode:
            written.append(path)
        return real_open(path, mode, *args, **kwargs)

    with patch("builtins.open", spy_open):
        shows = R.shows
        code = s.handle_touch(tap(*center(rects["it"])))
    check(code is None and s.lang == "it", "tap on IT: Italian, the splash stays")
    check(Settings.get_instance().get_value(SettingsConstants.SETTING__LOCALE) == "it" and os.environ.get("LANGUAGE") == "it",
          "the display language is now Italian for the whole session")
    check(R.shows > shows, "the splash is redrawn at once")
    check(not written, f"nothing written anywhere (no settings file on the microSD): {written}")
    px = R.canvas.getpixel(center(seedmax.chip_rects(s.languages)[1][1]))
    check(px[:3] == seedmax.CYAN, f"the IT chip is lit: {px}")
    # every pixel of each chip's zone selects that chip
    for c, t in rects.items():
        misses = 0
        for y in range(t[1], t[3] + 1, 3):
            for x in range(t[0] + 1, t[2], 3):
                if s.chip_at(x, y) != c:
                    misses += 1
        check(misses == 0, f"{c}: the whole tap zone selects it")
    s.handle_touch(tap(*center(rects["en"])))
    check(Settings.get_instance().get_value(SettingsConstants.SETTING__LOCALE) == "en", "back to English")

    # review 0031 #4: never written, even with Persistent Settings on and a microSD inserted
    settings = Settings.get_instance()
    settings._data[SettingsConstants.SETTING__PERSISTENT_SETTINGS] = SettingsConstants.OPTION__ENABLED
    from seedsigner.hardware.microsd import MicroSD
    MicroSD.get_instance.return_value.is_inserted = True
    written.clear()
    try:
        with patch("builtins.open", spy_open):
            s.handle_touch(tap(*center(rects["de"])))
            control = len(written)
            settings.save()                      # the same conditions do save through Settings
        check(settings.get_value(SettingsConstants.SETTING__LOCALE) == "de", "Persistent Settings on: DE chosen")
        # 0032: Persistent Settings removed, so not even Settings.save() writes any more
        check(control == 0 and len(written) == 0, f"... and nothing written, by the chip ({control}) "
                                                   f"or by Settings.save() ({len(written) - control})")
    finally:
        settings._data[SettingsConstants.SETTING__PERSISTENT_SETTINGS] = SettingsConstants.OPTION__DISABLED
        if os.path.exists(Settings.SETTINGS_FILENAME):
            os.remove(Settings.SETTINGS_FILENAME)
    set_locale("en")


def test_other_locale_at_boot():
    """ review 0031: a saved locale without a chip (pt_BR): no chip lit, EN reachable """
    set_locale("pt_BR")
    s = splash()
    en = seedmax.chip_rects(s.languages)[0]
    check(R.canvas.getpixel(center(en[1]))[:3] != seedmax.CYAN, "pt_BR: the EN chip is not lit")
    check(s.lang == "en", "pt_BR: the splash's own texts in English")
    s.handle_touch(tap(*center(en[2])))
    check(Settings.get_instance().get_value(SettingsConstants.SETTING__LOCALE) == "en", "tap on EN switches to English")
    set_locale("en")


def test_splash_never_blocks_the_boot():
    """ review 0031 #3: no wait without a usable touch panel; any error: SeedSigner's splash """
    from seedsigner.hardware.buttons import HardwareButtons
    hb = HardwareButtons.get_instance()
    with patch.object(hw_profile, "TOUCH_CALIBRATE_AT_BOOT", True):
        check(screensaver.seedmax_splash_waits() is False, "calibration at boot: no wait for START")
    with patch.object(hw_profile, "TOUCH_RAW_TEST_AT_BOOT", True):
        check(screensaver.seedmax_splash_waits() is False, "touch test at boot: no wait for START")
    with patch.object(type(hb), "_get_touch_device", lambda self: None):
        check(screensaver.seedmax_splash_waits() is False, "no touch controller: no wait for START")
    with patch.object(type(hb), "_get_touch_device", lambda self: object()):
        check(screensaver.seedmax_splash_waits() is True, "calibrated panel: wait for START")
    s = splash(wait_for_start=False)
    s.hw_inputs = MagicMock()
    with patch.object(screensaver.time, "sleep") as sleep:
        done = []
        t = threading.Thread(target=lambda: done.append(s._run()), daemon=True)
        t.start()
        t.join(5)
    check(done == [None] and not s.hw_inputs.wait_for.called and sleep.call_args[0][0] == s.SHOW_SECONDS,
          "without waiting: shown for a moment, then the boot goes on")
    seen = []

    def run_screen(self, cls, **kw):
        seen.append(cls)
        if cls is SeedMaxSplashScreen:
            raise RuntimeError("font missing")

    with patch.object(OpeningSplashView, "run_screen", run_screen), \
            patch.object(screensaver, "seedmax_splash_waits", lambda: True):
        OpeningSplashView().run()
    check(seen == [SeedMaxSplashScreen, OpeningSplashScreen], f"an error in the Seed Max splash: SeedSigner's ({seen})")


def test_start_is_the_only_way_out():
    set_locale("en")
    s = splash()
    before = R.canvas.tobytes()
    for x, y in [(240, 70), (40, 300), (440, 300), (240, 143), (240, 213), (20, 14)]:
        check(s.handle_touch(tap(x, y)) is None, f"tap at ({x},{y}) does nothing")
    check(R.canvas.tobytes() == before and not s.start_pressed, "... and the screen is unchanged")
    swipe = TouchEvent(TouchEvent.TYPE__SWIPE, 240, 262, direction=TouchEvent.SWIPE__UP)
    down = TouchEvent(TouchEvent.TYPE__DOWN, 240, 262)
    check(s.handle_touch(swipe) is None and s.handle_touch(down) is None, "a swipe or a touch-down on START does nothing")

    calls = []

    def wait_for(keys):
        calls.append(1)
        if len(calls) == 1:
            return K.KEY_UP
        if len(calls) == 2:
            return K.KEY_PRESS                       # a key press without START (no joystick on the Max)
        return s.handle_touch(tap(*center(s.start_rect())))

    s.hw_inputs = MagicMock()
    s.hw_inputs.wait_for = wait_for
    done = []
    t = threading.Thread(target=lambda: done.append(s._run()), daemon=True)
    t.start()
    t.join(5)
    check(done == [None] and len(calls) == 3, f"_run() returns only after START ({len(calls)} inputs)")
    px = R.canvas.getpixel((s.start_rect()[0] + 30, s.start_rect()[1] + 6))
    check(px[:3] == seedmax.CYAN, f"START lights up when tapped: {px}")


def test_splash_speed():
    seedmax._background = None
    t0 = time.perf_counter()
    seedmax.splash_background()
    t1 = time.perf_counter()
    check(t1 - t0 < 1.0, f"background drawn once, quickly ({(t1 - t0) * 1000:.0f} ms on this PC)")
    check(seedmax.splash_background() is seedmax.splash_background(), "kept for the whole boot")


def test_standard_profile_keeps_seedsigner_splash():
    seen = []
    with patch.object(hw_profile, "is_max_profile", lambda: False), \
            patch.object(OpeningSplashView, "run_screen", lambda self, cls, **kw: seen.append(cls)):
        OpeningSplashView().run()
    check(seen == [OpeningSplashScreen], f"standard profile: SeedSigner's splash ({seen})")
    seen.clear()
    with patch.object(OpeningSplashView, "run_screen", lambda self, cls, **kw: seen.append(cls)):
        OpeningSplashView().run()
    check(seen == [SeedMaxSplashScreen], f"Max profile: the Seed Max splash ({seen})")


def test_persistent_settings_removed():
    """ 0032: the option is gone and nothing can bring it back """
    from seedsigner.models.settings_definition import SettingsDefinition
    entry = SettingsDefinition.get_settings_entry(SettingsConstants.SETTING__PERSISTENT_SETTINGS)
    check(SettingsConstants.PERSISTENT_SETTINGS_AVAILABLE is False, "Persistent Settings switched off in the image")
    check(entry.visibility == SettingsConstants.VISIBILITY__HIDDEN, "not in the Settings menu")
    check(entry.selection_options == SettingsConstants.OPTIONS__ONLY_DISABLED, "DISABLED is the only value")
    settings = Settings.get_instance()
    real_open = builtins.open
    written = []

    def spy_open(path, mode="r", *args, **kwargs):
        if "w" in mode or "a" in mode:
            written.append(path)
        return real_open(path, mode, *args, **kwargs)

    with patch("builtins.open", spy_open), patch("os.remove") as removed:
        settings.set_value(SettingsConstants.SETTING__PERSISTENT_SETTINGS, SettingsConstants.OPTION__ENABLED)
        _name, qr = Settings.parse_settingsqr("settings::v1 persistent=E qr_density=H")
        settings.update(qr)
        settings._data[SettingsConstants.SETTING__PERSISTENT_SETTINGS] = SettingsConstants.OPTION__ENABLED
        settings.save()
    check(not written and not removed.called, f"set_value, SettingsQR and save() write or delete nothing: {written}")
    settings._data[SettingsConstants.SETTING__PERSISTENT_SETTINGS] = SettingsConstants.OPTION__DISABLED
    settings.update(qr)
    check(settings.get_value(SettingsConstants.SETTING__PERSISTENT_SETTINGS) == SettingsConstants.OPTION__DISABLED
          and settings.get_value(SettingsConstants.SETTING__QR_DENSITY) == SettingsConstants.DENSITY__HIGH,
          "a SettingsQR with persistent=E: applied, persistence off")
    settings.update({})
    set_locale("en")


def bech32m_decode(address):
    """ (hrp, 5-bit data without checksum) if the bech32m checksum is right, else None """
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    if address.lower() != address or "1" not in address:
        return None
    hrp, data = address.rsplit("1", 1)
    if any(c not in charset for c in data):
        return None
    values = [charset.index(c) for c in data]
    chk = 1
    for v in [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i, g in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)):
            chk ^= g if (top >> i) & 1 else 0
    return (hrp, values[:-6]) if chk == 0x2bc830a3 else None


def test_silent_payment_address():
    """ 0035: the on-chain donation address is a well-formed BIP-352 v0 address """
    from embit.ec import PublicKey
    address = seedmax.DONATION_SILENT_PAYMENT
    decoded = bech32m_decode(address)
    check(decoded is not None and decoded[0] == "sp", "sp1 address, bech32m checksum right")
    version, bits = decoded[1][0], decoded[1][1:]
    acc = n = 0
    payload = []
    for v in bits:
        acc, n = (acc << 5) | v, n + 5
        while n >= 8:
            n -= 8
            payload.append((acc >> n) & 255)
    payload = bytes(payload)
    check(version == 0 and len(address) == 116 and len(payload) == 66, f"version 0, 116 chars, 66 bytes ({len(payload)})")
    try:
        scan, spend = PublicKey.parse(payload[:33]), PublicKey.parse(payload[33:])
        keys_ok = scan.sec() == payload[:33] and spend.sec() == payload[33:]
    except Exception:
        keys_ok = False
    check(keys_ok, "scan and spend keys are valid points")
    flipped = address[:-2] + ("q" if address[-2] != "q" else "p") + address[-1]
    check(bech32m_decode(flipped) is None, "one character changed: checksum wrong (the check works)")


def test_donation():
    """ 0032, 0035: the Donate screen with two choices, each QR decoding to its exact string """
    from pyzbar.pyzbar import decode
    from seedsigner.gui.screens.settings_screens import SeedMaxDonateScreen
    from seedsigner.models.encode_qr import GenericStaticQrEncoder
    from seedsigner.views.settings_views import DonateView, SeedMaxDonateQRView
    offer = seedmax.DONATION_BOLT12
    check(seedmax.DONATE_STRINGS["it"]["free"] == "Seed Max è gratuito e open source.", "0039: 'gratuito', not 'libero'")
    check(offer.startswith("lno1") and len(offer) == 334 and offer == offer.lower(), "a BOLT12 offer, 334 characters")
    for lang in seedmax.DONATE_STRINGS:
        set_locale(lang)
        s = SeedMaxDonateScreen()
        with R.lock:
            s._render()
        texts = [c for c in s.components if c.__class__.__name__ == "TextArea"]
        bottom = max(t.screen_y + t.height for t in texts)
        check(bottom < component_rect(s.buttons[0])[1], f"{lang}: texts above the buttons")
        check([b.text for b in s.buttons] == [seedmax.donate_text(lang, "ln"), seedmax.donate_text(lang, "sp")],
              f"{lang}: two buttons {[b.text for b in s.buttons]}")
        check(component_rect(s.buttons[1])[3] <= 320, f"{lang}: both buttons on screen")
    set_locale("en")
    for kind, data in (("ln", offer), ("sp", seedmax.DONATION_SILENT_PAYMENT)):
        seen = {}
        view = SeedMaxDonateQRView(kind=kind)
        view.run_screen = lambda cls, **kw: seen.update(cls=cls.__name__, **kw)
        dest = view.run()
        enc = seen.get("qr_encoder")
        check(seen.get("cls") == "QRDisplayScreen" and isinstance(enc, GenericStaticQrEncoder) and enc.data == data,
              f"{kind}: the full-size QR screen with its string")
        check(dest.View_cls is DonateView, f"{kind}: Done goes back to Donate")
        img = GenericStaticQrEncoder(data=data).next_part_image(320, 320, border=3, background_color="ffffff")
        found = [r.data.decode() for r in decode(img.convert("L"))]
        check(found == [data], f"{kind}: the 320 px QR decodes to the exact string ({len(found)} code)")
    check(SeedMaxDonateQRView(kind="other").kind == "ln", "an unknown kind falls back to Lightning")
    v = DonateView()
    for selected, kind in ((0, "ln"), (1, "sp")):
        v.run_screen = lambda cls, **kw: selected
        dest = v.run()
        check(dest.View_cls is SeedMaxDonateQRView and dest.view_args == {"kind": kind}, f"Donate > button {selected}: {kind} QR")
    v.run_screen = lambda cls, **kw: -1
    check(v.run().View_cls.__name__ == "SettingsMenuView", "Donate > back: Settings")


def test_screensaver():
    """ 0033: "SEED MAX" bounces on night blue; SeedSigner's logo outside the Max profile """
    from seedsigner.views.screensaver import ScreensaverScreen
    buttons = MagicMock()
    s = ScreensaverScreen(buttons)
    navy = rgb(GUIConstants.BACKGROUND_COLOR)
    check(seedmax.SCREENSAVER_BACKGROUND == navy, "background = the Seed Max background")
    check(s.logo.size == seedmax.screensaver_logo().size, f"the Seed Max logo ({s.logo.size})")
    corners = [s.image.getpixel(p) for p in ((0, 0), (s.image.width - 1, s.image.height - 1))]
    check(all(c == navy for c in corners), f"night blue around the logo: {corners}")
    x0 = (s.image.width - s.logo.width) // 2
    y0 = (s.image.height - s.logo.height) // 2
    window = s.image.crop((x0, y0, x0 + s.logo.width, y0 + s.logo.height))
    cyan = sum(1 for p in window.getdata() if p[1] > 180 and p[2] > 160)
    check(cyan > 1500, f"the neon wordmark in the middle ({cyan} cyan pixels)")
    edge = [window.getpixel((window.width - 1 - dx, y)) for dx in range(2) for y in range(window.height)]
    check(all(p == navy for p in edge), "the X is whole: nothing lit on the logo's right edge")
    # same behaviour: any input stops it and the screen underneath comes back
    buttons.has_any_input.side_effect = [False, False, True]
    buttons.override_ind = False
    R.disp.show_image.reset_mock()
    before = R.shows
    s.start()
    check(R.disp.show_image.call_count == 2 and R.shows == before + 1 and not s.is_running,
          f"bounces until a touch, then restores the screen ({R.disp.show_image.call_count} frames)")
    frame = R.disp.show_image.call_args_list[0][0][0]
    check(frame.size == (480, 320), f"each frame is exactly the screen ({frame.size})")
    # 0039: the neon-grid scene, built at the first start
    sc = s.seedmax_scene
    check(isinstance(sc, seedmax.ScreensaverScene), "0039: the neon-grid scene")
    sky = frame.getpixel((240, 2))
    floor = [frame.getpixel((x, 300)) for x in range(0, 480, 4)]
    check(any(p[1] > 150 and p[2] > 150 for p in floor), "grid floor drawn under the horizon")
    boxes = [sc.logo_box(fx, fy) for fx in (0, .5, 1, -1, 2) for fy in (0, .5, 1, -1, 2)]
    check(all(0 <= x1 and x2 <= 480 and 0 <= y1 and y2 <= seedmax.HORIZON for x1, y1, x2, y2 in boxes),
          "the wordmark always whole, in the sky above the horizon")
    x1, y1, x2, y2 = sc.logo_box(.5, .5)
    f = sc.frame(.5, .5)
    lit = sum(1 for x in range(x1, x2) for y in range(y1, y2) if f.getpixel((x, y))[1] > 200 and f.getpixel((x, y))[2] > 180)
    check(lit > 1500, f"SEED MAX lit where it should be ({lit} px)")
    check(len({sc.frames[k].crop((0, seedmax.HORIZON + 30, 480, 320)).tobytes() for k in range(sc.FLOOR_PHASES)}) == sc.FLOOR_PHASES,
          f"the floor moves: {sc.FLOOR_PHASES} different positions")
    it = seedmax.ScreensaverScene(device=seedmax.text("it", "device"))
    check(it.logo.size[0] == sc.logo.size[0], "the subtitle follows the language and never widens the logo")
    with patch.object(seedmax, "ScreensaverScene", side_effect=RuntimeError("boom")):
        plain = ScreensaverScreen(MagicMock())
        b2 = MagicMock()
        b2.has_any_input.side_effect = [False, True]
        b2.override_ind = False
        plain.buttons = b2
        R.disp.show_image.reset_mock()
        plain.start()
    check(plain.seedmax_scene is None and R.disp.show_image.call_count == 1,
          "if the scene fails: the plain bouncing logo, never no screensaver")
    # a frame that fails mid-loop: plain logo for that frame and from then on, no error out
    s2 = ScreensaverScreen(MagicMock())
    s2.seedmax_scene = seedmax.ScreensaverScene()
    b3 = MagicMock()
    b3.has_any_input.side_effect = [False, False, True, False, True]
    b3.override_ind = False
    s2.buttons = b3
    R.disp.show_image.reset_mock()
    try:
        with patch.object(seedmax.ScreensaverScene, "frame", side_effect=OSError("boom")):
            s2.start()
            s2.start()
        ok = True
    except Exception:
        ok = False
    check(ok and s2.seedmax_scene is None and R.disp.show_image.call_count >= 3,
          "a failing frame never escapes: the plain logo from then on")
    with patch.object(hw_profile, "is_max_profile", lambda: False):
        std = ScreensaverScreen(MagicMock())
    check(std.logo.size == (240, 240) and std.image.getpixel((0, 0)) == (0, 0, 0),
          "standard profile: SeedSigner's logo on black")
    with patch.object(seedmax, "screensaver_logo", side_effect=RuntimeError("boom")):
        broken = ScreensaverScreen(MagicMock())
    check(broken.logo.size == (240, 240), "if the Seed Max logo fails: SeedSigner's, never no screensaver")


def test_no_language_in_settings():
    """ 0033: the language comes from the splash chips; Settings has no Language entry """
    from seedsigner.views.settings_views import SettingsMenuView
    seen = {}

    def run(view):
        def fake(cls, **kw):
            seen.clear()
            seen.update(kw)
            return -1
        view.run_screen = fake
        view.screen = MagicMock()
        view.run()
        return [b.button_label for b in seen["button_data"]]

    labels = run(SettingsMenuView())
    check(bool(labels) and "Language" not in labels,
          f"Max: no Language in Settings ({labels})")
    for vis in (SettingsConstants.VISIBILITY__ADVANCED, SettingsConstants.VISIBILITY__HARDWARE):
        check("Language" not in run(SettingsMenuView(visibility=vis)), f"... nor in {vis}")
    # the entries list and the buttons still line up: each choice opens its own entry
    v = SettingsMenuView()
    v.run_screen = lambda cls, **kw: 0
    v.screen = MagicMock()
    dest = v.run()
    check(labels[0] == "Bitcoin network" and dest.view_args.get("attr_name") == SettingsConstants.SETTING__NETWORK,
          f"first button opens its own entry ({labels[0]} -> {dest.view_args.get('attr_name')})")
    with patch.object(hw_profile, "is_max_profile", lambda: False):
        check("Language" in run(SettingsMenuView()), "standard profile keeps Settings > Language")


def sample_addresses():
    """ one real address per type and network, from a fixed key """
    from embit import script
    from embit.ec import PrivateKey
    from embit.networks import NETWORKS
    key = PrivateKey(bytes(range(1, 33)))
    pub = key.get_public_key()
    multi = script.multisig(2, [pub, PrivateKey(bytes(range(2, 34))).get_public_key(), PrivateKey(bytes(range(3, 35))).get_public_key()])
    out = {}
    for net in ("main", "test", "regtest"):
        n = NETWORKS[net]
        out[f"{net} p2wpkh"] = script.p2wpkh(pub).address(n)
        out[f"{net} p2wsh"] = script.p2wsh(multi).address(n)
        out[f"{net} p2tr"] = script.p2tr(pub).address(n)
        out[f"{net} p2sh-p2wpkh"] = script.p2sh(script.p2wpkh(pub)).address(n)
        out[f"{net} p2pkh"] = script.p2pkh(pub).address(n)
    return out


def test_verified_address_whole():
    """ 0033: Address Verified shows the whole address in groups of 4, nothing cut """
    from seedsigner.gui.components import FormattedAddress, SeedMaxGroupedAddress
    from seedsigner.gui.screens.seed_screens import SeedAddressVerificationSuccessScreen
    set_locale("en")
    lengths = set()
    for name, address in sample_addresses().items():
        lengths.add(len(address))
        s = SeedAddressVerificationSuccessScreen(address=address, verified_index=8, verified_index_is_change=False)
        with R.lock:
            s._render()
        comp = [c for c in s.components if isinstance(c, SeedMaxGroupedAddress)]
        check(len(comp) == 1 and not any(isinstance(c, FormattedAddress) for c in s.components),
              f"{name}: the grouped address, not SeedSigner's abbreviated one")
        a = comp[0]
        drawn = [p[1] for p in a.text_params]
        check("".join(drawn) == address and all(len(g) == 4 for g in drawn[:-1]) and "..." not in "".join(drawn),
              f"{name} ({len(address)} chars): every character drawn, groups of 4")
        lit = [p[1] for p in a.text_params if p[2] == GUIConstants.ACCENT_COLOR]
        check(lit == drawn[:2] + drawn[-2:], f"{name}: first two and last two groups lit")
        right = max(p[0][0] + len(p[1]) * a.char_width for p in a.text_params)
        left = min(p[0][0] for p in a.text_params)
        texts = [c for c in s.components if c.__class__.__name__ == "TextArea"]
        below = [t for t in texts if t.screen_y > a.screen_y]
        bottom = max(t.screen_y + t.height for t in below)
        check(left >= 0 and right <= 480 and below and below[0].screen_y >= a.screen_y + a.height
              and bottom < component_rect(s.buttons[0])[1],
              f"{name}: inside the screen, above \"receive address\", index and OK (bottom {bottom})")
    check({34, 35, 42, 44, 62, 64} <= lengths, f"address lengths covered: {sorted(lengths)}")
    # review finding L3: longer than any seed address (witness v2+, up to 90): still 3 lines, nothing lost
    for n in (66, 74, 90):
        address = ("bcrt1z" + "qpzry9x8gf2tvdw0s3jn54khce6mua7l" * 3)[:n]
        s = SeedAddressVerificationSuccessScreen(address=address, verified_index=8, verified_index_is_change=True)
        with R.lock:
            s._render()
        a = [c for c in s.components if isinstance(c, SeedMaxGroupedAddress)][0]
        texts = [c for c in s.components if c.__class__.__name__ == "TextArea" and c.screen_y > a.screen_y]
        bottom = max(t.screen_y + t.height for t in texts)
        right = max(p[0][0] + len(p[1]) * a.char_width for p in a.text_params)
        check("".join(p[1] for p in a.text_params) == address and len(a.lines) <= 3 and right <= 480
              and bottom < component_rect(s.buttons[0])[1], f"{n} chars: whole, {len(a.lines)} lines, above OK (bottom {bottom})")
    with patch.object(hw_profile, "is_max_profile", lambda: False):
        s = SeedAddressVerificationSuccessScreen(address=sample_addresses()["main p2wpkh"], verified_index=0,
                                                 verified_index_is_change=False)
    check(any(isinstance(c, FormattedAddress) for c in s.components), "standard profile: SeedSigner's own format")


def test_word_screens_render():
    """ 0034: the AKC keyboard and the review drawn for real, in every language """
    from embit.wordlists.bip39 import WORDLIST
    from seedsigner.gui.screens import seedmax_words as ws
    words = ["abandon"] * 11 + ["about"]
    d = ImageDraw.Draw(Image.new("RGB", (480, 320)))
    for lang in seedmax.WORD_STRINGS:
        s = ws.SeedMaxWordEntryScreen(wordlist=WORDLIST, word_index=4, word_count=12, lang=lang)
        s.text = "ba"
        with R.lock:
            s._render()
        px = R.canvas.getpixel((ws.key_rects()["x"][0] + 4, ws.key_rects()["x"][1] + 4))
        check(px[:3] != rgb(GUIConstants.ACCENT_COLOR), f"{lang}: keyboard drawn")
        r = ws.SeedMaxWordEntryScreen(wordlist=WORDLIST, word_index=11, word_count=12, lang=lang,
                                      current_word="about", earlier_words=words[:11], start_in_review=True)
        for shown, text in ((False, "about"), (True, "about"), (True, "zoo")):
            r.shown, r.text = shown, text
            with R.lock:
                r._render()
        for key in ("hidden", "ok", "bad"):
            t = seedmax.word_text(lang, key)
            f = r.fit(t, 15, 480 - 16)
            check(f.size >= 12, f"{lang} review line {key!r} readable: {f.size} px")
        for key in ("back", "delete", "show", "confirm", "wrong"):
            t = seedmax.word_text(lang, key)
            check(d.textlength(t, font=r.font(18)) < 220, f"{lang} {key!r} fits its button")
        check(len(seedmax.word_text(lang, "title", n=24, m=24)) <= 22, f"{lang}: title short enough for the top nav")


def test_settings_fixed_on_the_max():
    """ 0036: the settings fixed on the Max: not in any menu, and nothing moves them """
    from seedsigner.models import settings_definition as sd
    from seedsigner.models.settings_definition import SettingsDefinition
    from seedsigner.views.settings_views import SettingsMenuView
    C = SettingsConstants
    locked = sd.SEEDMAX_LOCKED_SETTINGS
    expected = {C.SETTING__XPUB_QR_FORMAT: [C.XPUB_QR_FORMAT__UR_CRYPTO_ACCOUNT if hasattr(C, "XPUB_QR_FORMAT__UR_CRYPTO_ACCOUNT") else "urca", "sta"],
                C.SETTING__XPUB_DETAILS: C.OPTION__ENABLED, C.SETTING__PASSPHRASE: C.OPTION__ENABLED,
                C.SETTING__MICROSD_TOAST_TIMER: C.MICROSD_TOAST_TIMER_DISABLED, C.SETTING__MESSAGE_SIGNING: C.OPTION__DISABLED,
                C.SETTING__PARTNER_LOGOS: C.OPTION__DISABLED, C.SETTING__PRIVACY_WARNINGS: C.OPTION__ENABLED,
                C.SETTING__DIRE_WARNINGS: C.OPTION__ENABLED, C.SETTING__QR_BRIGHTNESS_TIPS: C.OPTION__ENABLED,
                C.SETTING__CAMERA_ROTATION: hw_profile.CAMERA_ROTATION, C.SETTING__DISPLAY_COLOR_INVERTED: C.OPTION__DISABLED}
    for attr, value in expected.items():
        check(locked.get(attr) == value, f"{attr} fixed at {value!r} ({locked.get(attr)!r})")
    check(locked.get(C.SETTING__SCRIPT_TYPES) == [v for v, _n in C.ALL_SCRIPT_TYPES]
          and {C.LEGACY_P2PKH, C.CUSTOM_DERIVATION} <= set(locked[C.SETTING__SCRIPT_TYPES]),
          "0039: all 5 script types, Legacy and Custom Derivation included")
    for attr in (C.SETTING__DISPLAY_CONFIGURATION, C.SETTING__MAX_CAMERA_RESOLUTION, C.SETTING__MAX_DISPLAY_SPI_MHZ):
        check(attr in locked, f"{attr} fixed at the build's value ({locked.get(attr)!r})")
    zoom = SettingsDefinition.get_settings_entry(C.SETTING__MAX_CAMERA_ZOOM)
    check(C.SETTING__MAX_CAMERA_ZOOM not in locked and zoom.visibility == C.VISIBILITY__HIDDEN,
          "camera zoom: not in the menus, but the scan screen's zoom button still changes it")
    Settings.get_instance().set_value(C.SETTING__MAX_CAMERA_ZOOM, 2)
    check(Settings.get_instance().get_value(C.SETTING__MAX_CAMERA_ZOOM) == 2, "... and set_value keeps it")
    Settings.get_instance().set_value(C.SETTING__MAX_CAMERA_ZOOM, zoom.default_value)
    for attr in locked:
        check(SettingsDefinition.get_settings_entry(attr).visibility == C.VISIBILITY__HIDDEN, f"{attr}: in no menu")
    settings = Settings.get_instance()
    flips = {C.SETTING__MESSAGE_SIGNING: C.OPTION__ENABLED, C.SETTING__PRIVACY_WARNINGS: C.OPTION__DISABLED,
             C.SETTING__DIRE_WARNINGS: C.OPTION__DISABLED, C.SETTING__XPUB_QR_FORMAT: ["spl"],
             C.SETTING__CAMERA_ROTATION: 0, C.SETTING__MICROSD_TOAST_TIMER: C.MICROSD_TOAST_TIMER_FOREVER,
             C.SETTING__PASSPHRASE: C.OPTION__REQUIRED, C.SETTING__DISPLAY_COLOR_INVERTED: C.OPTION__ENABLED}
    for attr, value in flips.items():
        settings.set_value(attr, value)
        check(settings.get_value(attr) == locked[attr], f"set_value can't move {attr}")
    _name, qr = Settings.parse_settingsqr("settings::v1 xpub_qr=spl xpub_details=D passphrase=R camera=0 "
                                          "priv_warn=D dire_warn=D partners=E rgb_inv=E sigs=ss scripts=nat "
                                          "compact_seedqr=D bip85=D electrum=D denom=sats")
    settings.update(qr)
    check(all(settings.get_value(a) == v for a, v in locked.items()), "a SettingsQR can't move any of them")
    check(settings.get_value(C.SETTING__BTC_DENOMINATION) == "sats", "... while the rest of the QR is applied")
    settings.update({})
    check(settings.get_value(C.SETTING__BTC_DENOMINATION) == SettingsDefinition.get_settings_entry(C.SETTING__BTC_DENOMINATION).default_value,
          "back to defaults")

    seen = {}

    def labels(view):
        view.run_screen = lambda cls, **kw: (seen.clear(), seen.update(kw), -1)[2]
        view.screen = MagicMock()
        view.run()
        return [b.button_label for b in seen["button_data"]]

    flat = ["Bitcoin network", "Denomination display", "Touch calibration", "Battery", "Donate"]
    with patch.object(hw_profile, "has_battery_gauge", lambda: True):
        for vis in (C.VISIBILITY__GENERAL, C.VISIBILITY__ADVANCED, C.VISIBILITY__HARDWARE):
            got = labels(SettingsMenuView(visibility=vis))
            check(got == flat, f"0037: one Settings list, also when coming back from {vis}: {got}")
    check(locked.get(C.SETTING__ELECTRUM_SEEDS) == C.OPTION__ENABLED, "0037: Electrum seeds fixed on")
    check(locked.get(C.SETTING__BIP85_CHILD_SEEDS) == C.OPTION__ENABLED, "0037: BIP-85 fixed on (used from Tools)")
    check(C.SETTING__NETWORK not in locked, "0037: Bitcoin network in Settings, changeable")
    settings.set_value(C.SETTING__NETWORK, C.TESTNET)
    _name, qr = Settings.parse_settingsqr("settings::v1 sigs=ss")
    settings.update(qr)
    check(settings.get_value(C.SETTING__NETWORK) == C.MAINNET, "a SettingsQR without network: back to mainnet, as in SeedSigner")
    settings.update({})

    # each button opens its own entry, back from an entry lands on it, the extra buttons route
    from seedsigner.views.settings_views import (SettingsEntryUpdateSelectionView, TouchCalibrationView,
                                                 BatteryInfoView, DonateView)
    with patch.object(hw_profile, "has_battery_gauge", lambda: True):
        for i, label in enumerate(flat):
            v = SettingsMenuView()
            v.run_screen = lambda cls, i=i, **kw: i
            v.screen = MagicMock()
            dest = v.run()
            if label in ("Touch calibration", "Battery", "Donate"):
                want = {"Touch calibration": TouchCalibrationView, "Battery": BatteryInfoView, "Donate": DonateView}[label]
                check(dest.View_cls is want, f"{label} -> {dest.View_cls.__name__}")
            else:
                entry = SettingsDefinition.get_settings_entry(dest.view_args["attr_name"])
                check(dest.View_cls is SettingsEntryUpdateSelectionView and entry.display_name == label,
                      f"{label} -> its own entry ({entry.display_name})")
                upd = SettingsEntryUpdateSelectionView(attr_name=entry.attr_name)
                upd.run_screen = lambda cls, **kw: -1 if False else 1000
                back = upd.run()
                seen.clear()
                back_view = back.View_cls(**back.view_args)
                back_view.run_screen = lambda cls, **kw: (seen.update(kw), 1000)[1]
                back_view.screen = MagicMock()
                home = back_view.run()
                check(seen["button_data"][seen["selected_button"]].button_label == label and home.View_cls.__name__ == "MainMenuView",
                      f"back from {label}: the flat list, on {label}; back again: Home")
        for odd in (99, -1, True, None):
            v = SettingsMenuView()
            v.run_screen = lambda cls, odd=odd, **kw: odd
            v.screen = MagicMock()
            check(v.run().View_cls.__name__ == "MainMenuView", f"choice {odd!r}: Home, never another entry")


def test_message_signing_off_without_a_way_back():
    """ 0036: a sign-message QR says the option is off, with no "Update setting" button """
    from seedsigner.views.view import OptionDisabledView
    seen = {}
    v = OptionDisabledView(settings_attr=SettingsConstants.SETTING__MESSAGE_SIGNING)
    v.run_screen = lambda cls, **kw: (seen.update(kw), 0)[1]
    dest = v.run()
    check([b.button_label for b in seen["button_data"]] == ["Back to Main Menu"] and dest.View_cls.__name__ == "MainMenuView",
          f"only Back to Main Menu ({[b.button_label for b in seen['button_data']]})")
    v = OptionDisabledView(settings_attr=SettingsConstants.SETTING__BTC_DENOMINATION)
    v.run_screen = lambda cls, **kw: (seen.update(kw), 0)[1]
    v.run()
    check(len(seen["button_data"]) == 2, "an option that isn't fixed still offers Update setting")


def test_tools_menu():
    """ 0037: the Max's own Tools; SeedSigner's Tools still filtered as a second guard """
    from seedsigner.views import tools_views, seedmax_tools
    from seedsigner.views.view import MainMenuView
    from seedsigner.views.scan_views import ScanAddressView
    from seedsigner.views.seed_views import SeedBIP85SelectNumWordsView, LoadSeedView
    from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON
    shown = {}
    picks = []

    class FakeScreen:
        def __init__(self, **kw):
            shown.clear()
            shown.update(kw)

        def display(self):
            return picks.pop(0)

    v = MainMenuView()
    v.run_screen = lambda cls, **kw: [b.button_label for b in kw["button_data"]].index("Tools")
    check(v.run().View_cls is seedmax_tools.SeedMaxToolsMenuView, "Home > Tools: the Max's Tools")
    with patch.object(hw_profile, "is_max_profile", lambda: False):
        check(v.run().View_cls is tools_views.ToolsMenuView, "standard profile: SeedSigner's Tools")

    expected = {"Calc 12th/24th word": tools_views.ToolsCalcFinalWordNumWordsView,
                "Address explorer": tools_views.ToolsAddressExplorerSelectSourceView,
                "Verify address": ScanAddressView,
                "BIP-85 child seed": seedmax_tools.SeedMaxBIP85SelectSeedView}
    for i, (label, view_cls) in enumerate(expected.items()):
        picks[:] = [i]
        with patch.object(seedmax_tools, "ButtonListScreen", FakeScreen):
            dest = seedmax_tools.SeedMaxToolsMenuView().run()
        check([b.button_label for b in shown["button_data"]] == list(expected), f"Tools: {[b.button_label for b in shown['button_data']]}")
        check(dest.View_cls is view_cls, f"tap {i} ({label}) -> {dest.View_cls.__name__}")
    for odd in (4, 99, True, None, -1, RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON):
        picks[:] = [odd]
        with patch.object(seedmax_tools, "ButtonListScreen", FakeScreen):
            dest = seedmax_tools.SeedMaxToolsMenuView().run()
        check(dest.View_cls.__name__ == "BackStackView", f"Tools choice {odd!r}: back")

    # BIP-85: the parent seed among the loaded ones (Electrum seeds can't be parents)
    from seedsigner.models.seed import Seed, ElectrumSeed
    from seedsigner.gui.screens import seed_screens
    from seedsigner.controller import Controller as Ctl
    ctl = Ctl.get_instance()
    saved = list(ctl.storage.seeds)
    ctl.storage.seeds.clear()
    try:
        picks[:] = [0]
        with patch.object(seed_screens, "SeedSelectSeedScreen", FakeScreen):
            dest = seedmax_tools.SeedMaxBIP85SelectSeedView().run()
        check([b.button_label for b in shown["button_data"]] == ["Load a seed"] and shown["text"] == "Load the parent seed"
              and dest.View_cls is LoadSeedView, "no seed loaded: Load a seed")
        a = Seed(["abandon"] * 11 + ["about"])
        e = ElectrumSeed("regular reject rare profit once math fringe chase until ketchup century escape".split())
        b = Seed(["zoo"] * 11 + ["wrong"])
        check(not e.bip85_supported and a.bip85_supported, "Electrum seeds: no BIP-85")
        ctl.storage.seeds.extend([a, e, b])
        for pick, want_num in ((0, 0), (1, 2)):
            picks[:] = [pick]
            with patch.object(seed_screens, "SeedSelectSeedScreen", FakeScreen):
                dest = seedmax_tools.SeedMaxBIP85SelectSeedView().run()
            check([bt.button_label for bt in shown["button_data"]] == ["73c5da0a", b.get_fingerprint(), "Load a seed"],
                  f"parents listed, Electrum left out: {[bt.button_label for bt in shown['button_data']]}")
            check(dest.View_cls is SeedBIP85SelectNumWordsView and dest.view_args == {"seed_num": want_num},
                  f"pick {pick}: BIP-85 of seed #{want_num} ({dest.view_args})")
        for odd in (3, 99, None, RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON):
            picks[:] = [odd]
            with patch.object(seed_screens, "SeedSelectSeedScreen", FakeScreen):
                dest = seedmax_tools.SeedMaxBIP85SelectSeedView().run()
            check(dest.View_cls.__name__ == "BackStackView", f"BIP-85 choice {odd!r}: back")
    finally:
        ctl.storage.seeds[:] = saved

    # SeedSigner's own Tools, if ever reached: still without New seed from photo/dice
    picks[:] = [0]
    with patch.object(tools_views, "ButtonListScreen", FakeScreen):
        dest = tools_views.ToolsMenuView().run()
    check([b.button_label for b in shown["button_data"]] == ["Calc 12th/24th word", "Address explorer", "Verify address"]
          and dest.View_cls is tools_views.ToolsCalcFinalWordNumWordsView, "SeedSigner's Tools filtered (second guard)")


def test_load_seed_menu():
    """ 0037: Load a seed without "Create a seed", with "Enter Electrum seed" """
    from seedsigner.views import seed_views
    shown = {}
    picks = []

    class FakeScreen:
        def __init__(self, **kw):
            shown.clear()
            shown.update(kw)

        def display(self):
            return picks.pop(0)

    want = {"Scan a SeedQR": "ScanSeedQRView", "Enter 12-word seed": "SeedMnemonicEntryView",
            "Enter 24-word seed": "SeedMnemonicEntryView", "Enter Electrum seed": "SeedElectrumMnemonicStartView"}
    for i, (label, view_name) in enumerate(want.items()):
        picks[:] = [i]
        with patch.object(seed_views, "ButtonListScreen", FakeScreen):
            dest = seed_views.LoadSeedView().run()
        check([b.button_label for b in shown["button_data"]] == list(want), f"Load a seed: {[b.button_label for b in shown['button_data']]}")
        check(dest.View_cls.__name__ == view_name, f"{label} -> {dest.View_cls.__name__}")
    for odd in (4, 9, None):
        picks[:] = [odd]
        with patch.object(seed_views, "ButtonListScreen", FakeScreen):
            dest = seed_views.LoadSeedView().run()
        check(dest.View_cls.__name__ == "BackStackView", f"choice {odd!r}: back, never Create a seed")


def test_qr_density_on_the_qr_screen():
    """ 0038: the density button on the QR screen: same data, bigger or smaller frames """
    import base64
    from embit.psbt import PSBT
    from seedsigner.gui.screens.screen import QRDisplayScreen
    from seedsigner.models.decode_qr import DecodeQR
    from seedsigner.models.encode_qr import UrPsbtQrEncoder, GenericStaticQrEncoder
    from seedsigner.hardware.buttons import HardwareButtonsConstants as K
    from seedsigner.hardware.touch import TouchEvent
    from seedsigner.models.settings_definition import SettingsDefinition
    sys.path.insert(0, "tests")
    from psbt_testing_util import PSBTTestData
    C = SettingsConstants
    check(SettingsDefinition.get_settings_entry(C.SETTING__QR_DENSITY).visibility == C.VISIBILITY__HIDDEN
          and C.SETTING__QR_DENSITY not in __import__("seedsigner.models.settings_definition", fromlist=["x"]).SEEDMAX_LOCKED_SETTINGS,
          "QR density: out of Settings, changeable from the QR screen")
    raw = base64.b64decode(PSBTTestData.SINGLE_SIG_NATIVE_SEGWIT_1_INPUT)
    psbt = PSBT.parse(raw)
    settings = Settings.get_instance()
    settings.set_value(C.SETTING__QR_DENSITY, C.DENSITY__MEDIUM)
    s = QRDisplayScreen(qr_encoder=UrPsbtQrEncoder(psbt=psbt, qr_density=C.DENSITY__MEDIUM))
    check(s.density_control and s.density_level() == 2, "animated PSBT QR: density button, medium")
    r = s.control_rects()
    dx, dy = (r["density"][0] + r["density"][2]) // 2, (r["density"][1] + r["density"][3]) // 2
    check(s.handle_touch(TouchEvent(TouchEvent.TYPE__UP, dx, dy, tap=True)) == K.KEY_LEFT, "tap on it: the density key")
    check(r["density"][3] < r["done"][1] and r["density"][2] < 480 - 80, "above Done, in the left bar")
    gap_top, gap_bottom = r["density"][3] + 1, r["done"][1] - s.DONE_TOUCH_SLOP - 1
    check(gap_bottom - gap_top >= 18 and all(s.touch_zone(40, y) is None for y in range(gap_top, gap_bottom + 1)),
          f"dead space between density and Done: y {gap_top}..{gap_bottom} (a low tap never closes the QR)")
    lengths = {}
    for expected in (C.DENSITY__HIGH, C.DENSITY__LOW, C.DENSITY__MEDIUM):
        s.next_density()
        enc = s.qr_encoder
        check(enc.qr_density == expected and s.threads[-1].qr_encoder is enc and settings.get_value(C.SETTING__QR_DENSITY) == expected,
              f"next: {expected}, shown and kept for the next QR")
        dec = DecodeQR()
        for _ in range(500):
            dec.add_data(enc.next_part())
            if dec.is_complete:
                break
        check(dec.is_complete and dec.get_psbt().serialize() == raw, f"{expected}: decodes to exactly the same PSBT")
        lengths[expected] = len(enc.cur_part())
    check(lengths[C.DENSITY__LOW] < lengths[C.DENSITY__MEDIUM] < lengths[C.DENSITY__HIGH], f"frame sizes {lengths}")
    # the frame shows the button
    img = Image.new("RGB", (480, 320), rgb(GUIConstants.BACKGROUND_COLOR))
    s.render_touch_controls(img)
    lit = sum(1 for x in range(r["density"][0], r["density"][2]) for y in range(r["density"][1], r["density"][3])
              if img.getpixel((x, y))[:3] == rgb(GUIConstants.ACCENT_COLOR))
    check(lit > 50, f"density bars drawn ({lit} accent pixels)")
    # the screen's loop: the density key changes it and the QR stays up; only Done closes
    before = s.qr_encoder.qr_density
    s.hw_inputs = MagicMock()
    s.hw_inputs.wait_for.side_effect = [K.KEY_LEFT, K.KEY_UP, K.KEY_PRESS]
    s.threads[-1] = MagicMock()
    s.threads[-1].is_alive.return_value = False
    s._run()
    check(s.qr_encoder.qr_density != before and s.hw_inputs.wait_for.call_count == 3 and s.threads[-1].stop.called,
          "density key: changed, still on screen; Done: closed")
    static = QRDisplayScreen(qr_encoder=GenericStaticQrEncoder(data=seedmax.DONATION_BOLT12))
    check(not static.density_control and static.touch_zone(dx, dy) is None, "a static QR (donation): no density button")
    settings.set_value(C.SETTING__QR_DENSITY, C.DENSITY__MEDIUM)


def home():
    set_locale("en")
    data = [ButtonOption("Scan", SeedSignerIconConstants.SCAN), ButtonOption("Seeds", SeedSignerIconConstants.SEEDS),
            ButtonOption("Tools", SeedSignerIconConstants.TOOLS), ButtonOption("Settings", SeedSignerIconConstants.SETTINGS)]
    s = MainMenuScreen(title="Home", button_data=data)
    with R.lock:
        s._render()
    return s


def test_home():
    s = home()
    nav = s.top_nav
    check(nav.brand and nav.show_power_button and not nav.show_back_button, "Home top nav: brand, power, no back")
    x1, y1, x2, y2 = nav.brand_box()
    lit = sum(1 for y in range(y1, y2) for x in range(x1, x2) if R.canvas.getpixel((x, y))[1] > 180)
    check(lit > 150, f"\"SEED MAX\" drawn at the left of the top nav ({lit} bright pixels)")
    title_px = [R.canvas.getpixel((x, y)) for x in range(200, 280) for y in range(10, 38)]
    check(all(p == rgb(GUIConstants.BACKGROUND_COLOR) for p in title_px), "no \"Home\" title in the middle")
    check(R.canvas.getpixel((240, nav.height - 1)) == rgb(GUIConstants.TOP_NAV_LINE_COLOR), "cyan line under the top nav")
    layout = nav.battery_layout("100%")
    check(layout is not None and (layout[0] or layout[1]) >= x2 + 6, f"battery right of the wordmark: {layout}, mark ends {x2}")
    accent = rgb(GUIConstants.ACCENT_COLOR)
    for i, b in enumerate(s.buttons):
        r = component_rect(b)
        check(b.is_icon_inline and b.icon_name, f"button {i}: icon beside the label")
        if i == s.selected_button:
            continue
        broken = [y for y in range(r[1] + 10, r[3] - 10) if R.canvas.getpixel((r[0], y)) != accent
                  and R.canvas.getpixel((r[0] + 1, y)) != accent]
        check(not broken, f"button {i}: left edge unbroken, label row included ({len(broken)} gaps)")
    # tap still opens the button under the finger
    code = s.handle_touch(tap(*center(component_rect(s.buttons[3]))))
    check(code == K.KEY_PRESS and s.selected_button == 3, "tap on Settings selects it")


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        print(f"--- {name}")
        fn()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
