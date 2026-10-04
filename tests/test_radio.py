"""Radio loading: the two sources must agree, and combine without duplicates."""
from datetime import datetime, timezone

from powometer import RAW
from powometer.radio import load_all, load_console_export, load_messages_tab

EXPORT = RAW / "radio" / "rockblock-export-2026-09-29.csv"
TAB = RAW / "radio" / "messages_tab" / "2026-09-29_sheet-export.jsonl"


def test_console_export_counts():
    msgs = load_console_export(EXPORT)
    assert len(msgs) == 2587                      # MO only (2,588 rows incl. 1 MT)
    assert msgs[0].transmit_utc.tzinfo is timezone.utc


def test_both_formats_describe_the_same_messages():
    """Every Messages-tab message with a payload is in the console export with
    the identical UTC time and payload text (the 22 since 2026-09-26 and the
    two historic re-ingested ones, MOMSN 2430 and 2941)."""
    export = {m.key for m in load_console_export(EXPORT)}
    tab = [m for m in load_messages_tab(TAB) if m.payload_text]
    assert len(tab) == 24
    missing = [m for m in tab if m.key not in export]
    assert missing == []


def test_combined_has_momsn_and_no_duplicates():
    allm = load_all()
    keys = [m.key for m in allm]
    assert len(keys) == len(set(keys))
    by_momsn = {m.momsn: m for m in allm if m.momsn is not None}
    assert set(by_momsn) >= {2430, 2941} | set(range(3128, 3150))
    m3128 = by_momsn[3128]
    assert m3128.transmit_utc == datetime(2026, 9, 26, 20, 39, 40, tzinfo=timezone.utc)
    assert len(m3128.sources) == 2               # seen in both sources
    assert allm == sorted(allm, key=lambda m: m.transmit_utc)
