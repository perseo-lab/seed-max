"""
Seed Max (0038): the words of a BIP-85 child seed on one screen, numbered in columns
(gui/screens/seedmax_words.SeedMaxChildWordsScreen), instead of SeedSigner's 4-word pages.
SeedWordsView (pinned to SeedSigner 0.8.7) is unchanged: `View.run_screen()` hands its
pages here when it shows a BIP-85 child on the Max.

SeedWordsView pages through the words (page 1 -> 2 -> 3, then the backup test prompt).
Here page 1 shows all the words; "Done" answers "Next" (index 0, the page's only button),
and the following pages answer "Next" at once, without showing, until the last one
answers "Done" (also index 0): the View then goes on exactly as after its last page.
Going Back from after the words steps back through those pages silently to page 1,
which shows the words again. Back on the words: SeedSigner's Back (the warning before).
A page is skipped only while `controller.seedmax_child_pages` names that same child and
that page; anything else clears it.
"""
PAGES_ATTR = "seedmax_child_pages"


def applies(view, Screen_cls) -> bool:
    from seedsigner.hardware import hw_profile
    if not hw_profile.is_max_profile():
        return False
    from seedsigner.gui.screens import seed_screens
    from seedsigner.views.seed_views import SeedWordsView
    return (Screen_cls is seed_screens.SeedWordsScreen and isinstance(view, SeedWordsView)
            and getattr(view, "bip85_data", None) is not None)


def run(view, kwargs: dict):
    from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON
    from seedsigner.gui.screens.seedmax_words import SeedMaxChildWordsScreen
    from seedsigner.models.settings_definition import SettingsConstants

    controller = view.controller
    data = view.bip85_data
    child = (view.seed_num, data["child_index"], data["num_words"])
    page, pages = view.page_index, kwargs["num_pages"]
    pending = getattr(controller, PAGES_ATTR, None)
    setattr(controller, PAGES_ATTR, None)

    if page > 0:
        if pending == (child, page):
            # forward after "Done" on the words: next page, or the end
            if page < pages - 1:
                setattr(controller, PAGES_ATTR, (child, page + 1))
            return 0
        return RET_CODE__BACK_BUTTON            # coming back: down to page 1, the words

    words = view.seed.get_bip85_child_mnemonic(data["child_index"], data["num_words"]).split()
    network = view.settings.get_value(SettingsConstants.SETTING__NETWORK)
    from gettext import gettext as _
    view.screen = SeedMaxChildWordsScreen(
        title=_("Child #{}").format(data["child_index"]),
        words=words,
        parent_fingerprint=view.seed.get_fingerprint(network),
        button_label=_("Done"),
    )
    result = view.screen.display()
    if result == RET_CODE__BACK_BUTTON:
        return result
    if pages > 1:
        setattr(controller, PAGES_ATTR, (child, 1))
    return 0
