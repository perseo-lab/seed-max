"""
Seed Max: menus of SeedSigner's Views that the Max shows differently, without changing
those Views (tools_views.py and seed_views.py stay pinned to SeedSigner 0.8.7).

`View.run_screen()` hands these menus here. The screen gets the list without the hidden
entries; the index the user picks is translated back to the View's own list, so the View
routes exactly as before. A choice that isn't in the list shown is BACK: never an entry
the user didn't see.

* Load a seed (0037, Ollen 13 Nov 2026): no "Create a seed" (it led to SeedSigner's Tools,
  with New seed from photo/dice). "Enter Electrum seed" is always there: the Electrum
  setting is fixed on (settings_definition).
* SeedSigner's Tools (0036): no "New seed" from photo or dice. On the Max the Home opens
  the Max's own Tools (views/seedmax_tools.py); this stays as a second guard.
"""
from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON


def _max() -> bool:
    from seedsigner.hardware import hw_profile
    return hw_profile.is_max_profile()


def hidden_entries(view) -> list:
    if not _max():
        return []
    from seedsigner.views.tools_views import ToolsMenuView
    from seedsigner.views.seed_views import LoadSeedView
    if isinstance(view, ToolsMenuView):
        return [ToolsMenuView.IMAGE, ToolsMenuView.DICE]
    if isinstance(view, LoadSeedView):
        return [LoadSeedView.CREATE]
    return []


def applies(view, kwargs: dict) -> bool:
    return "button_data" in kwargs and bool(hidden_entries(view))


def run(view, Screen_cls, kwargs: dict):
    hidden = hidden_entries(view)
    full = list(kwargs["button_data"])
    shown = [b for b in full if b not in hidden]
    view.screen = Screen_cls(**dict(kwargs, button_data=shown))
    selected = view.screen.display()
    if isinstance(selected, int) and not isinstance(selected, bool) and 0 <= selected < len(shown):
        return full.index(shown[selected])
    # anything else, POWER included (these Views don't handle it): BACK
    return RET_CODE__BACK_BUTTON
