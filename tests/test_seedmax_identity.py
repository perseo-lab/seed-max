"""
Seed Max 0031: runs tests/seedmax_identity.py in its own interpreter (it replaces
hardware modules with mocks), and checks that the splash's guarantee stays true:
"Signs exactly like SeedSigner 0.8.7 — signing code untouched".
"""
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Every file of models/ (except settings_definition.py, the Max's settings, and since 0032
# settings.py: Persistent Settings removed - storing settings, not signing), helpers/,
# views/psbt_views.py and views/seed_views.py, with its git blob id in SeedSigner 0.8.7
# (commit e0a80d4b). These read the PSBT, check change, derive keys and sign. Since the
# 0031 review also: views/scan_views.py (scanned QR to PSBT/seed), views/tools_views.py
# (dice and camera entropy to a new seed), views/__init__.py, and requirements.txt
# (embit==0.8.0; on the device embit comes from the OS, which the image audit proves
# identical to the official one).
SIGNING_FILES_0_8_7 = {
    "requirements.txt": "7334b127e5383336497a071327e617b30bf4c8db",
    "src/seedsigner/views/__init__.py": "a78d84db3fd7efab6663c8bc5043386105b03415",
    "src/seedsigner/views/scan_views.py": "4f9d9b2d1005d3111246e35374c4cf074a46d17c",
    "src/seedsigner/views/tools_views.py": "b2c6b9fb805771e051aa57c7f339487433033d7b",
    "src/seedsigner/helpers/__init__.py": "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
    "src/seedsigner/helpers/embit_utils.py": "8055379949dd78c12312f01540221967f62dd892",
    "src/seedsigner/helpers/l10n.py": "a971da5805c5f9e1bec7556774853510646d81ff",
    "src/seedsigner/helpers/mnemonic_generation.py": "444c3fed913fb233d816ced53ded6a911007664f",
    "src/seedsigner/helpers/qr.py": "75291efe3e002b7ccbe09a3d5c9b9594e2a97bf6",
    "src/seedsigner/helpers/ur2/LICENSE": "a6d94cb86f05403c056dbca2f46082d66687f8b3",
    "src/seedsigner/helpers/ur2/__init__.py": "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
    "src/seedsigner/helpers/ur2/bytewords.py": "a1204ed2fc6bc34bbbd77707614cb9c8f4666d38",
    "src/seedsigner/helpers/ur2/cbor_lite.py": "ca4c109c9e123d52f06d6a1775bc73f5a70e3fa5",
    "src/seedsigner/helpers/ur2/constants.py": "00a2265db77aba57d6b7c52bc1d22f4f2a305c87",
    "src/seedsigner/helpers/ur2/crc32.py": "05fa874138081354d618591093e0c126fe8c2031",
    "src/seedsigner/helpers/ur2/fountain_decoder.py": "9e8dcdf34f1ad411f3987b1a2daac1ded5a3e80e",
    "src/seedsigner/helpers/ur2/fountain_encoder.py": "af5dd3384a692fdc8237f492adbe0a3ccccf7941",
    "src/seedsigner/helpers/ur2/fountain_utils.py": "6c7074e548bad02d2344dea0d2a2b9424485ae09",
    "src/seedsigner/helpers/ur2/random_sampler.py": "d1b8041155f45da1fe091c7551c794b5ba1f3006",
    "src/seedsigner/helpers/ur2/ur.py": "f48d3d698be7a5ea02a31ef63ca59d6dc104162c",
    "src/seedsigner/helpers/ur2/ur_decoder.py": "bb282b09b07fde0d573c34308447bcff7407ba0f",
    "src/seedsigner/helpers/ur2/ur_encoder.py": "082ad17b25b41c67aace48aeba073ab049704ed8",
    "src/seedsigner/helpers/ur2/utils.py": "31a0dfe1af5037ba0eacf847908cd7b4f94065a7",
    "src/seedsigner/helpers/ur2/xoshiro256.py": "5ddb481c4e3fb3bf7ce3f9cc06d224a09d7b63b4",
    "src/seedsigner/models/__init__.py": "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
    "src/seedsigner/models/decode_qr.py": "6a2b2d23266e7eabbe367efedcc2993be9468801",
    "src/seedsigner/models/encode_qr.py": "86be8c66d0a44524492fa1b55a1fd37d19067edf",
    "src/seedsigner/models/psbt_parser.py": "6f1260998d02ea7ad6863965eb1dffb5b8995210",
    "src/seedsigner/models/qr_type.py": "bd678f7e898083eba3fe7f9b0924fb05705c1665",
    "src/seedsigner/models/seed.py": "82ed9eb2ca1c6b2a9295a8247cfb515fb1755022",
    "src/seedsigner/models/seed_storage.py": "85feb49cb6afc36c22513ee4fd448116625fac26",
    "src/seedsigner/models/singleton.py": "9635106b586bdb981b057fec7111083bed3f49b8",
    "src/seedsigner/models/threads.py": "0fe5651c111a811e119c1f6ea2203b8c7cba3690",
    "src/seedsigner/views/psbt_views.py": "475aef49abd9290ae6d368e1200e89d4f02be543",
    "src/seedsigner/views/seed_views.py": "65830d370a65321b3a37a73af3b0e88ef9d47fc1",
}


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_signing_code_is_seedsigner_0_8_7():
    changed = []
    for path, blob in SIGNING_FILES_0_8_7.items():
        with open(os.path.join(ROOT, path), "rb") as f:
            if git_blob_id(f.read()) != blob:
                changed.append(path)
    assert not changed, f"signing files differ from SeedSigner 0.8.7: {changed}"
    present = set()
    for d in ("src/seedsigner/models", "src/seedsigner/helpers"):
        for root, _dirs, files in os.walk(os.path.join(ROOT, d)):
            if "__pycache__" in root:
                continue
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), ROOT).replace(os.sep, "/")
                if rel.endswith(".py") and rel not in ("src/seedsigner/models/settings_definition.py",
                                                       "src/seedsigner/models/settings.py"):
                    present.add(rel)
    extra = present - set(SIGNING_FILES_0_8_7)
    assert not extra, f"new files among the signing code: {sorted(extra)}"


def test_guarantee_names_the_right_release():
    controller = open(os.path.join(ROOT, "src/seedsigner/controller.py")).read()
    seedmax = open(os.path.join(ROOT, "src/seedsigner/gui/seedmax.py")).read()
    version = re.search(r'^\s*VERSION = "([^"]+)"', controller, re.M).group(1)
    base = re.search(r'^BASE_VERSION = "([^"]+)"', seedmax, re.M).group(1)
    assert version == base == "0.8.7", (version, base)


def test_seedmax_identity():
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
    result = subprocess.run([sys.executable, os.path.join(HERE, "seedmax_identity.py")],
                            capture_output=True, text=True, env=env, timeout=300, cwd=ROOT)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
