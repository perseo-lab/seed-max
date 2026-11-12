"""
Seed Max (0031): the look of the signing SD, drawn with PIL only.

* the "SEED MAX" wordmark, neon tubes made of straight segments (no font, no blur);
* the boot splash: night-blue sky, perspective grid floor, two light trails, the
  wordmark and its reflection, what the device is, a guarantee line, the language
  chips and START.

Nothing here touches seeds, PSBTs or settings other than the display language.
The splash texts have their own small table (STRINGS) instead of the gettext catalogs:
the catalogs live in the pinned seedsigner-translations submodule, which stays
identical to the official one (ssmax_image_audit.py compares them message by message).
"""
import random

from PIL import Image, ImageDraw

# Seed Max version shown on the splash, and the SeedSigner release whose signing code it
# carries unchanged. tests/test_seedmax_identity.py checks BASE_VERSION against Controller.VERSION
# and the signing files against that release, so the guarantee line can't go stale.
SEEDMAX_VERSION = "5.8"
BASE_VERSION = "0.8.7"

# Palette (same family as the Anti-Klepto Check SD, by Ollen's choice of 12 Nov 2026)
NAVY0 = (6, 8, 30)
NAVY1 = (10, 14, 48)
NAVY2 = (36, 52, 160)
CYAN = (69, 230, 214)
CYAN_LIGHT = (190, 255, 248)
TRAIL_BLUE = (120, 170, 255)
WHITE = (240, 246, 255)
DIM = (170, 190, 240)
CHIP_FILL = (6, 10, 40)
CHIP_EDGE = (90, 130, 230)


def hex_color(rgb) -> str:
    return "#%02X%02X%02X" % tuple(rgb[:3])


# ---------------------------------------------------------------------------------------
# Wordmark: each letter is a few polylines in a 1 x 1 box (y down)
# ---------------------------------------------------------------------------------------
GLYPHS = {
    "S": [[(1, 0), (.14, 0), (0, .14), (0, .38), (.12, .5), (.88, .5), (1, .62), (1, .86), (.86, 1), (0, 1)]],
    "E": [[(1, 0), (0, 0), (0, 1), (1, 1)], [(.2, .5), (.78, .5)]],
    "D": [[(0, 0), (.78, 0), (1, .22), (1, .78), (.78, 1), (0, 1), (0, 0)]],
    "M": [[(0, 1), (0, 0), (.5, .52), (1, 0), (1, 1)]],
    "A": [[(0, 1), (0, .26), (.26, 0), (.74, 0), (1, .26), (1, 1)], [(.2, .6), (.8, .6)]],
    "X": [[(0, 0), (.42, .5)], [(.58, .5), (1, 1)], [(1, 0), (0, 1)]],
}
WORD = "SEED MAX"


def word_width(glyph_w: float, gap: float, space: float, text: str = WORD) -> float:
    n = len(text.replace(" ", ""))
    return n * glyph_w + (n - 1) * gap + text.count(" ") * (space - gap)


def word_paths(x0: float, y0: float, glyph_h: float, glyph_w: float, gap: float, space: float, text: str = WORD):
    paths, x = [], x0
    for ch in text:
        if ch == " ":
            x += space
            continue
        for p in GLYPHS[ch]:
            paths.append([(x + px * glyph_w, y0 + py * glyph_h) for px, py in p])
        x += glyph_w + gap
    return paths


def neon(image: Image.Image, paths, width: int, color=CYAN, core=CYAN_LIGHT, glow: int = 3):
    """
    A neon tube without blur (too slow on the Pi Zero): wide, faint strokes under the tube,
    then the tube, then a light core. `glow` = how many halo layers.
    """
    halo = Image.new("RGBA", image.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(halo)
    for extra, alpha in ((10, 18), (7, 30), (4, 60))[3 - glow:]:
        for p in paths:
            d.line(p, fill=color + (alpha,), width=width + extra, joint="curve")
    image.alpha_composite(halo)
    d = ImageDraw.Draw(image)
    for p in paths:
        d.line(p, fill=color + (255,), width=width, joint="curve")
    for p in paths:
        d.line(p, fill=core + (255,), width=max(1, width // 3), joint="curve")


_wordmark_cache = {}


def wordmark(height: int) -> Image.Image:
    """ "SEED MAX" for a top nav, transparent background, cached per height """
    if height not in _wordmark_cache:
        gh = height
        gw, gap, space = round(gh * .72), max(2, round(gh * .22)), round(gh * .55)
        # word_width() leaves out the gap before the space that word_paths() adds:
        # without it the X lost its right edge (0033)
        width = int(word_width(gw, gap, space)) + gap + 12
        img = Image.new("RGBA", (width, gh + 12), (0, 0, 0, 0))
        neon(img, word_paths(6, 6, gh, gw, gap, space), 2, glow=2)
        _wordmark_cache[height] = img
    return _wordmark_cache[height]


# ---------------------------------------------------------------------------------------
# Splash texts (English, plus the languages that have a chip). Never translated: "SEED
# MAX", "SeedSigner" and the version numbers.
# ---------------------------------------------------------------------------------------
LANGUAGE_CHIPS = ("en", "it", "fr", "es", "de")

STRINGS = {
    "en": dict(device="SIGNING DEVICE", chips=("PSBT SIGNING", "SEEDQR", "MULTISIG"),
               g1="Signs exactly like SeedSigner {base}",
               g2="signing code untouched · changed: interface, touch, camera, battery",
               start="START", foot="reproducible build · verify it yourself · signer SD v{version}"),
    "it": dict(device="DISPOSITIVO DI FIRMA", chips=("FIRMA PSBT", "SEEDQR", "MULTISIG"),
               g1="Firma esattamente come SeedSigner {base}",
               g2="codice di firma intatto · cambiano: interfaccia, touch, camera, batteria",
               start="AVVIA", foot="build riproducibile · verificala tu · SD di firma v{version}"),
    "fr": dict(device="APPAREIL DE SIGNATURE", chips=("SIGNATURE PSBT", "SEEDQR", "MULTISIG"),
               g1="Signe exactement comme SeedSigner {base}",
               g2="code de signature intact · changent : interface, tactile, caméra, batterie",
               start="DÉMARRER", foot="build reproductible · vérifiez vous-même · SD de signature v{version}"),
    "es": dict(device="DISPOSITIVO DE FIRMA", chips=("FIRMA PSBT", "SEEDQR", "MULTISIG"),
               g1="Firma exactamente como SeedSigner {base}",
               g2="código de firma intacto · cambian: interfaz, táctil, cámara, batería",
               start="INICIAR", foot="build reproducible · verifícalo tú · SD de firma v{version}"),
    "de": dict(device="SIGNIERGERÄT", chips=("PSBT SIGNIEREN", "SEEDQR", "MULTISIG"),
               g1="Signiert genau wie SeedSigner {base}",
               g2="Signiercode unverändert · geändert: Oberfläche, Touch, Kamera, Akku",
               start="START", foot="reproduzierbarer Build · selbst prüfen · Signier-SD v{version}"),
}


# ---------------------------------------------------------------------------------------
# Donations (0032, Ollen's choice of 12 Nov 2026): Lightning, a BOLT12 offer. It has
# no amount (the donor chooses), no description and no node id: one blinded path whose
# introduction node is ACINQ's (Phoenix). Reusable forever, nothing on-chain reused.
# Payable by BOLT12 wallets (Phoenix, Zeus, Core Lightning...), not by Sparrow (on-chain).
# tests/seedmax_identity.py decodes the displayed QR and compares it with this string.
# ---------------------------------------------------------------------------------------
DONATION_BOLT12 = (
    "lno1zrxq8pjw7qjlm68mtp7e3yvxee4y5xrgjhhyf2fxhlphpckrvevh50u0qfxc0n6m228mgt5rlpmqp63upgwf7zysppmpe"
    "ar64gjp6taxvp6e2qsr026hacegmnfhu9zd40k9qkplmy3kqee6vfu99esl7nvj35l86wqqqve5llhdydajwyz64hmauj7qk93"
    "xjkaca4pa96tttzr9tfts2tmpu9mlwv39m494t6j9qht5dpqggel0rxvgqfguxstts7ff65m9xhm60x3ea2kae8xjqy84qzes7t"
    "um7w780vh36qqsnus40074hc0y64ksea2xvfwsgq"
)

# 0035 (Ollen, 12 Nov 2026): also on-chain, to a Silent Payments address (BIP-352). One
# static address, yet every payment lands on a fresh taproot output only the owner can
# find: nothing is reused on-chain and the address reveals no balance. Version 0, 116
# characters: scan key (02...) and spend key (03...). Payable by wallets that SEND to sp1
# addresses (Sparrow 2.3+, Cake Wallet, BlueWallet, Wasabi, Nunchuk...). tests/seedmax_identity.py
# checks the bech32m checksum, the two keys and the QR.
DONATION_SILENT_PAYMENT = (
    "sp1qqt75n5wr03rpt33xutl40cnf8z9ahg2gmcl3nn5trxq5ff5jjcvf7qlg94hvwzx527zy5tl654vsv5k3dxa7rz97me3ku9w249zsev5ujycl3kaf"
)

DONATE_STRINGS = {
    "en": dict(free="Seed Max is free and open source.", how="Support it with:",
               ln="Lightning (BOLT12)", sp="On-chain · Silent Payments",
               wallets="LN: Phoenix, Zeus · on-chain: Sparrow 2.3+, Cake, BlueWallet"),
    "it": dict(free="Seed Max è libero e open source.", how="Sostienilo con:",
               ln="Lightning (BOLT12)", sp="On-chain · Silent Payments",
               wallets="LN: Phoenix, Zeus · on-chain: Sparrow 2.3+, Cake, BlueWallet"),
    "fr": dict(free="Seed Max est libre et open source.", how="Soutenez-le avec :",
               ln="Lightning (BOLT12)", sp="On-chain · Silent Payments",
               wallets="LN : Phoenix, Zeus · on-chain : Sparrow 2.3+, Cake, BlueWallet"),
    "es": dict(free="Seed Max es libre y de código abierto.", how="Apóyalo con:",
               ln="Lightning (BOLT12)", sp="On-chain · Silent Payments",
               wallets="LN: Phoenix, Zeus · on-chain: Sparrow 2.3+, Cake, BlueWallet"),
    "de": dict(free="Seed Max ist frei und Open Source.", how="Unterstütze es mit:",
               ln="Lightning (BOLT12)", sp="On-chain · Silent Payments",
               wallets="LN: Phoenix, Zeus · On-chain: Sparrow 2.3+, Cake, BlueWallet"),
}

DONATIONS = {"ln": DONATION_BOLT12, "sp": DONATION_SILENT_PAYMENT}


# 0034: word entry (AKC keyboard) and the review of all the words
WORD_STRINGS = {
    "en": dict(title="Word {n} of {m}", matches="{n} words", back="Back", delete="Delete",
               review="Review words", show="Show words", hidden="Words hidden: tap Show to compare with your backup",
               ok="Checksum OK · compare word by word with your backup", confirm="Confirm",
               bad="Wrong checksum: tap the wrong word to rewrite it", wrong="Wrong"),
    "it": dict(title="Parola {n} di {m}", matches="{n} parole", back="Indietro", delete="Cancella",
               review="Riepilogo parole", show="Mostra parole", hidden="Parole nascoste: tocca Mostra per confrontarle col backup",
               ok="Checksum OK · confronta parola per parola col backup", confirm="Conferma",
               bad="Checksum errato: tocca la parola sbagliata per riscriverla", wrong="Errato"),
    "fr": dict(title="Mot {n} sur {m}", matches="{n} mots", back="Retour", delete="Effacer",
               review="Revue des mots", show="Afficher", hidden="Mots masqués : touchez Afficher pour comparer à la sauvegarde",
               ok="Checksum OK · comparez mot par mot avec la sauvegarde", confirm="Confirmer",
               bad="Checksum faux : touchez le mot erroné pour le réécrire", wrong="Erroné"),
    "es": dict(title="Palabra {n} de {m}", matches="{n} palabras", back="Atrás", delete="Borrar",
               review="Revisar palabras", show="Mostrar", hidden="Palabras ocultas: toca Mostrar para compararlas con el respaldo",
               ok="Checksum OK · compara palabra por palabra con el respaldo", confirm="Confirmar",
               bad="Checksum erróneo: toca la palabra errónea para reescribirla", wrong="Erróneo"),
    "de": dict(title="Wort {n} von {m}", matches="{n} Wörter", back="Zurück", delete="Löschen",
               review="Wörter prüfen", show="Zeigen", hidden="Wörter verborgen: Zeigen tippen, um mit dem Backup zu vergleichen",
               ok="Prüfsumme OK · Wort für Wort mit dem Backup vergleichen", confirm="Bestätigen",
               bad="Falsche Prüfsumme: falsches Wort antippen und neu schreiben", wrong="Falsch"),
}


def word_text(lang: str, key: str, **values) -> str:
    return WORD_STRINGS.get(lang, WORD_STRINGS["en"])[key].format(**values)


SCREENSAVER_BACKGROUND = NAVY1       # = GUIConstants.BACKGROUND_COLOR #0A0E30


def screensaver_logo(height: int = 44) -> Image.Image:
    """
    Seed Max (0033): what bounces around in the screensaver, in place of SeedSigner's logo.
    The neon wordmark on the night-blue background, opaque (the screensaver pastes it on
    a larger night-blue canvas and slides a screen-sized window over it).
    """
    mark = wordmark(height)
    img = Image.new("RGBA", mark.size, NAVY1 + (255,))
    img.alpha_composite(mark)
    return img.convert("RGB")


def donate_text(lang: str, key: str) -> str:
    return DONATE_STRINGS.get(lang, DONATE_STRINGS["en"])[key]


def text(lang: str, key: str):
    value = STRINGS.get(lang, STRINGS["en"])[key]
    if isinstance(value, str):
        return value.format(base=BASE_VERSION, version=SEEDMAX_VERSION)
    return value


# ---------------------------------------------------------------------------------------
# Splash layout (480 x 320)
# ---------------------------------------------------------------------------------------
W, H = 480, 320
HORIZON = 184
CHIP_W, CHIP_H, CHIP_GAP, CHIP_TOP = 42, 24, 8, 6     # 0033: centred, more space between
CHIP_TOUCH_BOTTOM = 35                     # the tap zone reaches below the drawn chip
START = (160, 245, 320, 279)               # widened for long words, see start_rect()
# 0033: bigger texts, Ollen found them too small on the 3.5" panel. The guarantee box
# spans the screen and its second line is split in two at " · ".
GUARANTEE = (14, 164, 466, 236)
G1_SIZE, G2_SIZE = 17, 14
GUARANTEE_INNER = GUARANTEE[2] - (GUARANTEE[0] + 34) - 8
WORDMARK_Y = (42, 90)
DEVICE_CY = 108
FEATURES_Y = (124, 152)
FEATURE_W, FEATURE_STEP, FEATURE_SIZE = 140, 150, 16
FOOTER_CY = 306
FOOTER_SIZE = 13
STARLESS_BAND = (96, 156)                  # "SIGNING DEVICE" and the three chips


def chip_rects(languages) -> list:
    """ (code, drawn rect, touch rect), centred at the top (0033; they were right-aligned) """
    n = len(languages)
    x = round((W - n * CHIP_W - (n - 1) * CHIP_GAP) / 2)
    out = []
    for code in languages:
        drawn = (x, CHIP_TOP, x + CHIP_W, CHIP_TOP + CHIP_H)
        touch = (x - CHIP_GAP // 2, 0, x + CHIP_W + CHIP_GAP // 2, CHIP_TOUCH_BOTTOM)
        out.append((code, drawn, touch))
        x += CHIP_W + CHIP_GAP
    return out


def _blend(c0, c1, t):
    return tuple(int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(3))


_background = None


def splash_background() -> Image.Image:
    """ Everything that doesn't depend on the language; drawn once per boot """
    global _background
    if _background is not None:
        return _background
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        d.line((0, y, W, y), fill=_blend(NAVY0, NAVY2, y / (H - 1)) + (255,))

    rng = random.Random(7)                 # fixed stars: the same sky at every boot
    for _ in range(40):
        x, y = rng.randrange(W), rng.randrange(HORIZON - 8)
        if STARLESS_BAND[0] <= y <= STARLESS_BAND[1]:
            continue                       # never a dot on a letter of the texts below the name
        d.point((x, y), fill=(160, 200, 255))

    lines = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(lines)
    vx = W / 2
    for i in range(-18, 19):                                     # floor rays
        g.line((vx + i * 5, HORIZON, vx + i * 52, H), fill=CYAN + (90,))
    y, step = float(HORIZON), 2.4                                 # cross lines
    while y < H:
        g.line((0, int(y), W, int(y)), fill=CYAN + (int(50 + 160 * (y - HORIZON) / (H - HORIZON)),))
        step *= 1.28
        y += step
    for k in range(26):                                          # fade into the horizon
        g.line((0, HORIZON + k, W, HORIZON + k), fill=NAVY1 + (int(200 * (1 - k / 26)),))
    for width, alpha in ((15, 20), (9, 40), (5, 90), (2, 255)):   # horizon glow
        g.line((0, HORIZON, W, HORIZON), fill=CYAN_LIGHT + (alpha,), width=width)
    img.alpha_composite(lines)

    neon(img, [[(-10, 300), (130, 300), (182, 234), (230, 192)]], 3, glow=2)
    neon(img, [[(490, 270), (362, 270), (300, 218), (254, 194)]], 3, color=TRAIL_BLUE, glow=2)

    # 0033: a little smaller and lower, to leave air under the centred language chips
    gw, gh, gap, space = 37, WORDMARK_Y[1] - WORDMARK_Y[0], 10, 28
    x0, y0 = (W - word_width(gw, gap, space) - gap) / 2, WORDMARK_Y[0]
    paths = word_paths(x0, y0, gh, gw, gap, space)
    neon(img, paths, 6)
    refl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    neon(refl, paths, 6)
    band = refl.crop((0, y0 - 6, W, y0 + gh + 6)).transpose(Image.FLIP_TOP_BOTTOM)
    band = band.resize((W, int(band.height * .45)))
    band.putalpha(band.getchannel("A").point(lambda v: int(v * .22)))
    img.alpha_composite(band, (0, HORIZON + 4))

    glass = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)
    gd.rounded_rectangle(GUARANTEE, radius=8, fill=(4, 8, 30, 232), outline=CYAN + (170,), width=1)
    gd.rectangle((0, FOOTER_CY - 11, W, FOOTER_CY + 11), fill=(4, 6, 24, 215))
    img.alpha_composite(glass)
    _background = img
    return img


def fit(draw, s: str, font_loader, size: int, width: float, min_size: int = 9):
    """ The largest font size (down to min_size) at which `s` fits in `width` """
    while size > min_size and draw.textlength(s, font=font_loader(size)) > width:
        size -= 1
    return font_loader(size)


def spaced_width(draw, s: str, font, spacing: float) -> float:
    return sum(draw.textlength(c, font=font) for c in s) + spacing * (len(s) - 1)


def draw_spaced(draw, cx: float, cy: float, s: str, font, fill, spacing: float):
    x = cx - spaced_width(draw, s, font, spacing) / 2
    for c in s:
        draw.text((x, cy), c, font=font, fill=fill, anchor="lm")
        x += draw.textlength(c, font=font) + spacing


def start_rect(draw, label: str, mono) -> tuple:
    width = max(START[2] - START[0], spaced_width(draw, label, mono(20), 5) + 70)
    cx = W / 2
    return (int(cx - width / 2), START[1], int(cx + width / 2), START[3])


def guarantee_lines(lang: str) -> list:
    """ The guarantee's second line, split at " · " (0033) """
    return [part.strip() for part in text(lang, "g2").split(" · ")]


def check_mark(draw, x, y, r=8, color=CYAN):
    draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=2)
    draw.line(((x - r * .45, y), (x - r * .1, y + r * .4), (x + r * .5, y - r * .4)), fill=color, width=2)


def render_splash(lang: str, languages, fonts, start_pressed: bool = False, lit: str = "") -> Image.Image:
    """
    The whole splash for `lang`. `languages`: the chips to show (empty: none); `lit`:
    the chip to light ("" = `lang`, None = none).
    `fonts`: dict of callables size -> ImageFont for "semi", "mono", "mono_regular".
    """
    img = splash_background().copy()
    d = ImageDraw.Draw(img)
    semi, mono, mono_r = fonts["semi"], fonts["mono"], fonts["mono_regular"]

    if lit == "":
        lit = lang
    for code, drawn, _touch in chip_rects(languages):
        if code == lit:
            d.rounded_rectangle(drawn, radius=4, fill=CYAN)
            color = NAVY0
        else:
            d.rounded_rectangle(drawn, radius=4, fill=CHIP_FILL, outline=CHIP_EDGE)
            color = (190, 205, 255)
        d.text(((drawn[0] + drawn[2]) / 2, (drawn[1] + drawn[3]) / 2), code.upper(), font=mono(13),
               fill=color, anchor="mm")

    device = text(lang, "device")
    spacing = 7 if len(device) <= 16 else 4
    f = mono(14)
    while spaced_width(d, device, f, spacing) > W - 40 and spacing > 1:
        spacing -= 1
    draw_spaced(d, W / 2, DEVICE_CY, device, f, CYAN, spacing)

    for i, label in enumerate(text(lang, "chips")):
        x = W / 2 + (i - 1) * FEATURE_STEP
        d.rounded_rectangle((x - FEATURE_W / 2, FEATURES_Y[0], x + FEATURE_W / 2, FEATURES_Y[1]), radius=5,
                            fill=CHIP_FILL, outline=CHIP_EDGE)
        d.text((x, sum(FEATURES_Y) / 2), label, font=fit(d, label, mono, FEATURE_SIZE, FEATURE_W - 10),
               fill=WHITE, anchor="mm")

    bx0, by0, bx1, by1 = GUARANTEE
    check_mark(d, bx0 + 18, (by0 + by1) / 2)
    g1 = text(lang, "g1")
    d.text((bx0 + 34, by0 + 16), g1, font=fit(d, g1, semi, G1_SIZE, GUARANTEE_INNER), fill=WHITE, anchor="lm")
    for i, line in enumerate(guarantee_lines(lang)):
        d.text((bx0 + 34, by0 + 39 + i * 19), line, font=fit(d, line, mono_r, G2_SIZE, GUARANTEE_INNER),
               fill=DIM, anchor="lm")

    label = text(lang, "start")
    rect = start_rect(d, label, mono)
    cy = (rect[1] + rect[3]) / 2
    radius = (rect[3] - rect[1]) // 2
    for extra, alpha in ((6, 40), (3, 90)):
        halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle((rect[0] - extra, rect[1] - extra, rect[2] + extra, rect[3] + extra),
                                               radius=radius + extra, outline=CYAN + (alpha,), width=2)
        img.alpha_composite(halo)
    d = ImageDraw.Draw(img)
    if start_pressed:
        d.rounded_rectangle(rect, radius=radius, fill=CYAN)
        label_color, arrow_color = NAVY0, NAVY0
    else:
        d.rounded_rectangle(rect, radius=radius, fill=(6, 10, 34), outline=CYAN, width=2)
        label_color, arrow_color = CYAN_LIGHT, CYAN
    f = mono(20)
    tw = spaced_width(d, label, f, 5)
    draw_spaced(d, W / 2 - 8, cy + 1, label, f, label_color, 5)
    ax = W / 2 - 8 + tw / 2 + 10
    d.polygon(((ax, cy - 7), (ax + 10, cy), (ax, cy + 7)), fill=arrow_color)

    foot = text(lang, "foot")
    d.text((W / 2, FOOTER_CY), foot, font=fit(d, foot, mono_r, FOOTER_SIZE, W - 16), fill=(200, 215, 255), anchor="mm")
    return img.convert("RGB")
