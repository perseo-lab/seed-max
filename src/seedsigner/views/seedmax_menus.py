"""
Seed Max (0036, Ollen, 12 Nov 2026): menu entries the Max doesn't offer, removed without
changing the Views that list them (tools_views.py stays pinned to SeedSigner 0.8.7).

Tools: no "New seed" from a camera photo or from D6 dice (Ollen makes his seed with dice
off the device; "Calc 12th/24th word" stays for that). `View.run_screen()` hands the menu
here: the screen gets the list without the hidden entries, and the index the user picks is
translated back to the View's own list, so the View routes exactly as before.
"""

def hidden_entries(view) -> list:
    from seedsigner.hardware import hw_profile
    if not hw_profile.is_max_profile():
        return []
    from seedsigner.views.tools_views import ToolsMenuView
    if isinstance(view, ToolsMenuView):
        return [ToolsMenuView.IMAGE, ToolsMenuView.DICE]
    return []


def applies(view, kwargs: dict) -> bool:
    return "button_data" in kwargs and bool(hidden_entries(view))


def run(view, Screen_cls, kwargs: dict):
    hidden = hidden_entries(view)
    full = list(kwargs["button_data"])
    shown = [b for b in full if b not in hidden]
    view.screen = Screen_cls(**dict(kwargs, button_data=shown))
    selected = view.screen.display()
    from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON
    if isinstance(selected, int) and not isinstance(selected, bool) and 0 <= selected < len(shown):
        return full.index(shown[selected])
    if selected in (RET_CODE__BACK_BUTTON, RET_CODE__POWER_BUTTON):
        return selected
    return RET_CODE__BACK_BUTTON     # anything else: never an entry the user didn't see
