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


def test_texts_fit():
    d = ImageDraw.Draw(Image.new("RGB", (480, 320)))
    mono = lambda size: seedmax_font("Inconsolata-SemiBold", size)
    for lang in seedmax.STRINGS:
        for key in ("g1", "g2", "foot"):
            text = seedmax.text(lang, key)
            if key == "g1":
                font_loader, size, width = lambda n: seedmax_font("OpenSans-SemiBold", n), 13, 400 - 34 - 8
            elif key == "g2":
                font_loader, size, width = lambda n: seedmax_font("Inconsolata-Regular", n), 11, 400 - 34 - 8
            else:
                font_loader, size, width = lambda n: seedmax_font("Inconsolata-Regular", n), 12, 480 - 16
            f = seedmax.fit(d, text, font_loader, size, width)
            check(d.textlength(text, font=f) <= width and f.size >= 9, f"{lang} {key}: fits at {f.size} px")
        for label in seedmax.text(lang, "chips"):
            f = seedmax.fit(d, label, mono, 12, 112)
            check(d.textlength(label, font=f) <= 112, f"{lang} chip {label!r} fits")
        rect = seedmax.start_rect(d, seedmax.text(lang, "start"), mono)
        check(rect[0] >= 40 and rect[2] <= 440, f"{lang} START button inside the screen: {rect}")
        check("{" not in seedmax.text(lang, "g1") + seedmax.text(lang, "foot"), f"{lang}: versions filled in")
        check(seedmax.BASE_VERSION in seedmax.text(lang, "g1") and seedmax.SEEDMAX_VERSION in seedmax.text(lang, "foot"),
              f"{lang}: guarantee names SeedSigner {seedmax.BASE_VERSION}, footer v{seedmax.SEEDMAX_VERSION}")


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


def test_donation():
    """ 0032: the Donate screen, then the BOLT12 offer as a QR that decodes to the exact string """
    from pyzbar.pyzbar import decode
    from seedsigner.gui.screens.settings_screens import SeedMaxDonateScreen
    from seedsigner.models.encode_qr import GenericStaticQrEncoder
    from seedsigner.views.settings_views import DonateView, SeedMaxDonateQRView
    offer = seedmax.DONATION_BOLT12
    check(offer.startswith("lno1") and len(offer) == 334 and offer == offer.lower(), "a BOLT12 offer, 334 characters")
    for lang in seedmax.DONATE_STRINGS:
        set_locale(lang)
        s = SeedMaxDonateScreen()
        with R.lock:
            s._render()
        texts = [c for c in s.components if c.__class__.__name__ == "TextArea"]
        bottom = max(t.screen_y + t.height for t in texts)
        check(bottom < component_rect(s.buttons[0])[1], f"{lang}: texts above the button")
        check(s.buttons[0].text == seedmax.donate_text(lang, "show"), f"{lang}: button {s.buttons[0].text!r}")
    set_locale("en")
    seen = {}
    view = SeedMaxDonateQRView()
    view.run_screen = lambda cls, **kw: seen.update(cls=cls.__name__, **kw)
    dest = view.run()
    enc = seen.get("qr_encoder")
    check(seen.get("cls") == "QRDisplayScreen" and isinstance(enc, GenericStaticQrEncoder) and enc.data == offer,
          "Show QR: the full-size QR screen with the offer")
    check(dest.View_cls is DonateView, "Done: back to Donate")
    img = GenericStaticQrEncoder(data=offer).next_part_image(320, 320, border=3, background_color="ffffff")
    found = [r.data.decode() for r in decode(img.convert("L"))]
    check(found == [offer], f"the 320 px QR decodes to the exact offer ({len(found)} code)")
    v = DonateView()
    v.run_screen = lambda cls, **kw: 0
    check(v.run().View_cls is SeedMaxDonateQRView, "Donate > Show QR opens the QR")
    v.run_screen = lambda cls, **kw: -1
    check(v.run().View_cls.__name__ == "SettingsMenuView", "Donate > back: Settings")


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
