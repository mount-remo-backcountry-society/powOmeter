"""The privacy check (tools/privacy_check.py), code review F11.

Fake secrets are assembled at run time, so this file itself passes the check.
"""
import importlib.util
import struct
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "privacy_check", Path(__file__).parent.parent / "tools" / "privacy_check.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


def hits(text, home=()):
    return pc.scan_text("f", text, set(), list(home))


def test_imei_with_separators():
    for sep in ("-", " "):
        fake = sep.join(["35", "2" * 6, "3" * 6, "4"])
        assert any("IMEI" in h for h in hits(f"label: {fake}"))
    assert hits("2026-09-26 20:45:00") == []


def test_sheet_id_in_one_case_is_caught():
    fake = "1" + ("ab3" * 15)[:43]             # lower case letters and digits only
    assert any("spreadsheet" in h for h in hits(f"id = '{fake}'"))
    assert hits("1" + "a" * 43) == []          # no digits: not an ID


def test_two_decimal_coordinates_near_home():
    home = [(12.345, -123.456)]
    assert any("home" in h for h in hits("12.35,-123.46", home))
    assert hits("12.35,-123.46") == []        # no home file: nothing to compare
    assert hits("48.12,-110.01", home) == []


def jpeg(tags_ifd0, gps_tags=()):
    """A minimal JPEG with an EXIF block (little-endian TIFF)."""
    ifd0 = 8
    gps = ifd0 + 2 + 12 * len(tags_ifd0) + 4
    t = b"II*\x00" + struct.pack("<I", ifd0) + struct.pack("<H", len(tags_ifd0))
    for tag in tags_ifd0:
        t += struct.pack("<HHII", tag, 4, 1, gps if tag == 0x8825 else 0)
    t += b"\x00" * 4 + struct.pack("<H", len(gps_tags))
    for tag in gps_tags:
        t += struct.pack("<HHII", tag, 5, 3, 0)
    t += b"\x00" * 4
    app1 = b"Exif\x00\x00" + t
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xd9"


def test_photo_with_gps_is_refused():
    assert pc.image_has_gps(jpeg([0x010F, 0x8825], gps_tags=[1, 2, 3, 4]))


def test_photo_without_gps_passes():
    assert not pc.image_has_gps(jpeg([0x010F]))
    assert not pc.image_has_gps(jpeg([0x8825], gps_tags=[0]))   # GPS section, but no position
    assert not pc.image_has_gps(b"\xff\xd8 no exif at all \xff\xd9")
