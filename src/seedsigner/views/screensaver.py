import logging
import os
import random
import time

from dataclasses import dataclass
from gettext import gettext as _

from seedsigner.gui.components import Fonts, GUIConstants, load_image
from seedsigner.gui.screens.screen import BaseScreen
from seedsigner.models.settings import Settings
from seedsigner.models.settings_definition import SettingsConstants
from seedsigner.views.view import View

logger = logging.getLogger(__name__)



# TODO: This early code is now outdated vis-a-vis Screen vs View distinctions
class LogoScreen(BaseScreen):
    def __init__(self):
        super().__init__()
        self.logo = load_image("logo_black_240.png")

        self.partners = [
            "hrf",
        ]

        self.partner_logos: dict = {}
        for partner in self.partners:
            logo_url = os.path.join("partners", f"{partner}_logo.png")
            self.partner_logos[partner] = load_image(logo_url)


    def _run(self):
        pass


    def get_random_partner(self) -> str:
        return self.partners[random.randrange(len(self.partners))]



@dataclass
class OpeningSplashView(View):
    force_partner_logos: bool|None = None

    def run(self):
        from seedsigner.hardware import hw_profile
        if hw_profile.is_max_profile():
            # Seed Max (0031): its own splash, waits for START, language chips. Whatever
            # goes wrong in it, the boot goes on with SeedSigner's splash.
            try:
                self.run_screen(SeedMaxSplashScreen, wait_for_start=seedmax_splash_waits())
                return
            except Exception as e:
                from seedsigner.gui.renderer import Renderer
                if Renderer.get_instance().is_screenshot_generator:
                    raise           # the screenshot generator ends each capture with an exception
                logger.exception(f"Seed Max splash failed, SeedSigner's instead: {e}")
        self.run_screen(
            OpeningSplashScreen,
            force_partner_logos=self.force_partner_logos
        )



def seedmax_splash_waits() -> bool:
    """
    Seed Max (0031): wait for START only when a tap can reach it. Not when the boot-time
    touch test or calibration is enabled (the panel isn't calibrated yet: they must come
    first), nor without a working touch controller.
    """
    from seedsigner.hardware import hw_profile
    from seedsigner.hardware.buttons import HardwareButtons
    if hw_profile.TOUCH_CALIBRATE_AT_BOOT or hw_profile.TOUCH_RAW_TEST_AT_BOOT:
        return False
    try:
        return HardwareButtons.get_instance()._get_touch_device() is not None
    except Exception:
        return False



@dataclass
class SeedMaxSplashScreen(BaseScreen):
    """
    Seed Max (0031): the boot screen. "SEED MAX", what the device is, the guarantee that
    it signs like SeedSigner, the language chips and START. It stays until START is
    tapped (`wait_for_start`; otherwise it shows for SHOW_SECONDS and goes on).

    A chip sets the display language for this session only, in memory: unlike
    Settings > Language the chip itself never saves, even with Persistent Settings on
    (with it on, a later change in Settings saves everything, the language included). Only the
    languages whose catalogs are in the image get a chip; with none, no chips at all.
    """
    wait_for_start: bool = True
    SHOW_SECONDS = 2

    def __post_init__(self):
        super().__post_init__()
        from seedsigner.gui import seedmax
        self.sm = seedmax
        detected = {code for code, _name in SettingsConstants.get_detected_languages()}
        chips = [c for c in seedmax.LANGUAGE_CHIPS if c == SettingsConstants.LOCALE__ENGLISH or c in detected]
        self.languages = chips if len(chips) > 1 else []
        self.start_pressed = False
        self.fonts = dict(
            semi=lambda size: Fonts.get_font("OpenSans-SemiBold", size),
            mono=lambda size: Fonts.get_font("Inconsolata-SemiBold", size),
            mono_regular=lambda size: Fonts.get_font("Inconsolata-Regular", size),
        )


    @staticmethod
    def current_locale() -> str:
        return Settings.get_instance().get_value(SettingsConstants.SETTING__LOCALE)


    @property
    def lang(self) -> str:
        """ The splash's own texts: the current language if it has them, else English """
        current = self.current_locale()
        return current if current in self.sm.STRINGS else SettingsConstants.LOCALE__ENGLISH


    def render_image(self):
        # A chip is lit only if it is the current language (e.g. none with pt_BR)
        current = self.current_locale()
        lit = current if current in self.languages else None
        return self.sm.render_splash(self.lang, self.languages, self.fonts, start_pressed=self.start_pressed,
                                     lit=lit)


    def start_rect(self):
        from PIL import Image, ImageDraw
        return self.sm.start_rect(ImageDraw.Draw(Image.new("RGB", (1, 1))), self.sm.text(self.lang, "start"), self.fonts["mono"])


    def _render(self):
        self.canvas.paste(self.render_image(), (0, 0))


    def chip_at(self, x: int, y: int):
        for code, _drawn, (x1, y1, x2, y2) in self.sm.chip_rects(self.languages):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return code
        return None


    @staticmethod
    def set_session_locale(code: str):
        """
        The language for this session, in memory only. Settings.set_value() would also
        save the settings to the microSD when Persistent Settings is on: the chip never
        does. Same effect otherwise: the value, then the gettext language.
        """
        settings = Settings.get_instance()
        settings._data[SettingsConstants.SETTING__LOCALE] = code
        settings.load_locale()


    def handle_touch(self, event):
        from seedsigner.hardware.buttons import HardwareButtonsConstants
        if not event.is_tap:
            return None
        x1, y1, x2, y2 = self.start_rect()
        margin = 6
        if x1 - margin <= event.x <= x2 + margin and y1 - margin <= event.y <= y2 + margin:
            self.start_pressed = True
            with self.renderer.lock:
                self._render()
                self.renderer.show_image()
            return HardwareButtonsConstants.KEY_PRESS
        code = self.chip_at(event.x, event.y)
        if code is not None and code != self.current_locale():
            self.set_session_locale(code)
            with self.renderer.lock:
                self._render()
                self.renderer.show_image()
        return None


    def _run(self):
        from seedsigner.hardware.buttons import HardwareButtonsConstants
        if self.renderer.is_screenshot_generator:
            return
        if not self.wait_for_start:
            time.sleep(self.SHOW_SECONDS)
            return
        while True:
            user_input = self.hw_inputs.wait_for(HardwareButtonsConstants.ALL_KEYS)
            if user_input == HardwareButtonsConstants.KEY_PRESS and self.start_pressed:
                return



class OpeningSplashScreen(LogoScreen):
    def __init__(self, force_partner_logos=None):
        self.force_partner_logos = force_partner_logos
        super().__init__()


    def _render(self):
        from PIL import Image
        from seedsigner.controller import Controller
        controller = Controller.get_instance()

        # TODO: Fix for the screenshot generator. When generating screenshots for
        # multiple locales, there is a button still in the canvas from the previous
        # screenshot, even though the Renderer has been reconfigured and re-
        # instantiated. This is a hack to clear the screen for now.
        self.clear_screen()

        show_partner_logos = Settings.get_instance().get_value(SettingsConstants.SETTING__PARTNER_LOGOS) == SettingsConstants.OPTION__ENABLED
        if self.force_partner_logos is not None:
            show_partner_logos = self.force_partner_logos

        logo_offset_x = int((self.canvas_width - self.logo.width)/2)

        if show_partner_logos:
            logo_offset_y = -56
        else:
            logo_offset_y = 0

        background = Image.new("RGBA", size=self.logo.size, color="black")
        if not self.renderer.is_screenshot_generator:
            # Fade in alpha
            for i in range(250, -1, -25):
                self.logo.putalpha(255 - i)
                self.renderer.canvas.paste(
                    Image.alpha_composite(background, self.logo),
                    (logo_offset_x, logo_offset_y)
                )
                self.renderer.show_image()
        else:
            # Skip animation for the screenshot generator
            self.renderer.canvas.paste(self.logo, (logo_offset_x, logo_offset_y))

        # Display version num below SeedSigner logo
        font = Fonts.get_font(GUIConstants.get_body_font_name(), GUIConstants.get_top_nav_title_font_size())
        version = f"v{controller.VERSION}"

        # The logo png is 240x240, but the actual logo is 70px tall, vertically centered
        logo_height = 70
        version_x = int(self.renderer.canvas_width/2)
        version_y = int(self.canvas_height/2) + int(logo_height/2) + logo_offset_y + GUIConstants.COMPONENT_PADDING
        self.renderer.draw.text(xy=(version_x, version_y), text=version, font=font, fill=GUIConstants.ACCENT_COLOR, anchor="mt")

        if not self.renderer.is_screenshot_generator:
            self.renderer.show_image()

        if show_partner_logos:
            if not self.renderer.is_screenshot_generator:
                # Hold on the version num for a moment
                time.sleep(1)

            # Set up the partner logo
            partner_logo: Image.Image = self.partner_logos[self.get_random_partner()]
            font = Fonts.get_font(GUIConstants.get_top_nav_title_font_name(), GUIConstants.get_body_font_size())
            # TRANSLATOR_NOTE: This is on the opening splash screen, displayed above the HRF logo
            sponsor_text = _("With support from:")
            (left, top, tw, th) = font.getbbox(sponsor_text, anchor="lt")

            x = int((self.renderer.canvas_width) / 2)
            y = self.canvas_height - GUIConstants.COMPONENT_PADDING - partner_logo.height - int(GUIConstants.COMPONENT_PADDING/2) - th
            self.renderer.draw.text(xy=(x, y), text=sponsor_text, font=font, fill="#ccc", anchor="mt")
            self.renderer.canvas.paste(
                partner_logo,
                (
                    int((self.renderer.canvas_width - partner_logo.width) / 2),
                    y + th + int(GUIConstants.COMPONENT_PADDING/2)
                )
            )

            self.renderer.show_image()

        if not self.renderer.is_screenshot_generator:
            # Hold on the splash screen for a moment
            time.sleep(2)



class ScreensaverScreen(LogoScreen):
    def __init__(self, buttons):
        from PIL import Image
        super().__init__()

        self.buttons = buttons

        # Seed Max (0033): "SEED MAX" bounces on night blue instead of SeedSigner's logo
        # on black. Same timing and same exit (any input); if the logo can't be drawn,
        # SeedSigner's own is kept.
        background = (0, 0, 0)
        from seedsigner.hardware import hw_profile
        if hw_profile.is_max_profile():
            try:
                from seedsigner.gui import seedmax
                self.logo = seedmax.screensaver_logo()
                background = seedmax.SCREENSAVER_BACKGROUND
            except Exception as e:
                logger.exception(f"Seed Max screensaver logo failed, SeedSigner's instead: {e}")

        # Paste the logo in a bigger image that is the canvas + the logo dims (half the
        # logo will render off the canvas at each edge).
        self.image = Image.new("RGB", (self.renderer.canvas_width + self.logo.width, self.renderer.canvas_height + self.logo.height), background)

        # Place the logo centered on the larger image
        logo_x = int((self.image.width - self.logo.width) / 2)
        logo_y = int((self.image.height - self.logo.height) / 2)
        self.image.paste(self.logo, (logo_x, logo_y))

        self.min_coords = (0, 0)
        self.max_coords = (self.renderer.canvas_width, self.renderer.canvas_height)

        # Update our first rendering position so we're centered
        self.cur_x = int(self.logo.width / 2)
        self.cur_y = int(self.logo.height / 2)

        self.increment_x = self.rand_increment()
        self.increment_y = self.rand_increment()

        self._is_running = False
        self.last_screen = None


    @property
    def is_running(self):
        return self._is_running
    

    def rand_increment(self):
        max_increment = 10.0
        min_increment = 1.0
        increment = random.uniform(min_increment, max_increment)
        if random.uniform(-1.0, 1.0) < 0.0:
            return -1.0 * increment
        return increment


    def start(self):
        if self.is_running:
            return

        self._is_running = True

        # Store the current screen in order to restore it later
        self.last_screen = self.renderer.canvas.copy()

        screensaver_start = int(time.time() * 1000)

        # Screensaver must block any attempts to use the Renderer in another thread so it
        # never gives up the lock until it returns.
        with self.renderer.lock:
            try:
                while self._is_running:
                    if self.buttons.has_any_input() or self.buttons.override_ind:
                        break

                    # Must crop the image to the exact display size
                    crop = self.image.crop((
                        self.cur_x, self.cur_y,
                        self.cur_x + self.renderer.canvas_width, self.cur_y + self.renderer.canvas_height))
                    self.renderer.disp.show_image(crop, 0, 0)

                    self.cur_x += self.increment_x
                    self.cur_y += self.increment_y

                    # At each edge bump, calculate a new random rate of change for that axis
                    if self.cur_x < self.min_coords[0]:
                        self.cur_x = self.min_coords[0]
                        self.increment_x = self.rand_increment()
                        if self.increment_x < 0.0:
                            self.increment_x *= -1.0
                    elif self.cur_x > self.max_coords[0]:
                        self.cur_x = self.max_coords[0]
                        self.increment_x = self.rand_increment()
                        if self.increment_x > 0.0:
                            self.increment_x *= -1.0

                    if self.cur_y < self.min_coords[1]:
                        self.cur_y = self.min_coords[1]
                        self.increment_y = self.rand_increment()
                        if self.increment_y < 0.0:
                            self.increment_y *= -1.0
                    elif self.cur_y > self.max_coords[1]:
                        self.cur_y = self.max_coords[1]
                        self.increment_y = self.rand_increment()
                        if self.increment_y > 0.0:
                            self.increment_y *= -1.0

            except KeyboardInterrupt as e:
                # Exit triggered; close gracefully
                logger.info("Shutting down Screensaver")

                # Have to let the interrupt bubble up to exit the main app
                raise e

            finally:
                self._is_running = False

                # Restore the original screen
                self.renderer.show_image(self.last_screen)



    def stop(self):
        self._is_running = False


