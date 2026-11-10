import io
import math
import struct
import unittest
import warnings
import zipfile
from unittest.mock import patch

from review.tdx_adjustments import _decrypt_block, adjusted_anchor, decode_gbbq, decode_gbbq_zip

# Immutable SYNTHETIC format fixture, not a saved market dataset. Encoded with
# the fixed upstream table's inverse; known clear records are specified below.
# Real official packets are a separate integration acceptance gate.
ENCODED = bytes.fromhex(
    "02000000acfd12bca3ec0967e8b4b6fcabe9d0406824840bc17b357e4000000040"
    "631224b0f311c9a914b20301019c8c297005eeb91deaf33a0000000000"
)


def archive_for(payload=ENCODED, names=("gbbq",), compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # Deliberately duplicate member.
        with zipfile.ZipFile(output, "w", compression=compression) as archive:
            for name in names:
                archive.writestr(name, payload)
    return output.getvalue()


def event(day="2026-06-18", category=1, **values):
    return {"market": "sh", "code": "600000", "date": day, "category": category,
            "cash_dividend": 0.0, "rights_price": 0.0,
            "bonus_ratio": 0.0, "rights_ratio": 0.0, **values}


class GbbqDecodeTests(unittest.TestCase):
    def test_fixed_encrypted_records_decode_all_fields_and_categories(self):
        rows = decode_gbbq(ENCODED)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {
            "market": "sh", "code": "600000", "date": "2026-06-18", "category": 1,
            "cash_dividend": 5.0, "rights_price": 8.0,
            "bonus_ratio": 3.0, "rights_ratio": 2.0,
        })
        self.assertEqual(rows[1]["market"], "sz")
        self.assertEqual(rows[1]["code"], "000001")
        self.assertEqual(rows[1]["date"], "2025-12-31")
        self.assertEqual(rows[1]["category"], 5)
        self.assertIsNone(rows[1]["cash_dividend"])
        self.assertEqual(_decrypt_block(bytes(8)).hex(), "eaa9cb621cfed468")

    def test_count_length_and_empty_file_cannot_establish_absent_events(self):
        for payload in (b"", b"\0" * 4, ENCODED[:3], ENCODED[:-1], ENCODED + b"x",
                        struct.pack("<I", 1) + ENCODED[4:],
                        struct.pack("<I", 0xffffffff) + ENCODED[4:]):
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                decode_gbbq(payload)
        with self.assertRaises(ValueError):
            decode_gbbq(bytearray(ENCODED))

    def test_corrupt_complete_ciphertext_never_silently_drops_record(self):
        corrupted = bytearray(ENCODED)
        corrupted[4:12] = bytes(8)
        with self.assertRaisesRegex(ValueError, "市场或代码"):
            decode_gbbq(bytes(corrupted))

    def test_clear_record_semantics_fail_closed(self):
        clear = bytearray(bytes.fromhex(
            "01363030303030000a273501010000a040000000410000404000000040"))
        for offset, replacement in (
            (0, bytes([255])), (1, b"X"), (7, b" "),
            (8, struct.pack("<I", 20260230)), (12, bytes([0])),
            (13, struct.pack("<f", math.nan)), (25, struct.pack("<f", -1.0)),
        ):
            broken = bytearray(clear)
            broken[offset:offset + len(replacement)] = replacement
            with patch("review.tdx_adjustments._decrypt_block",
                       side_effect=[bytes(broken[:8]), bytes(broken[8:16]), bytes(broken[16:24])]):
                # Bytes 24..28 are unencrypted, so copy them into the actual input.
                encoded = struct.pack("<I", 1) + ENCODED[4:28] + bytes(broken[24:])
                with self.subTest(offset=offset), self.assertRaises(ValueError):
                    decode_gbbq(encoded)

    def test_archive_checks_crc_and_structure_without_disk_extraction(self):
        self.assertEqual(decode_gbbq_zip(archive_for()), decode_gbbq(ENCODED))
        self.assertEqual(decode_gbbq_zip(archive_for(compression=zipfile.ZIP_STORED)),
                         decode_gbbq(ENCODED))
        self.assertEqual(decode_gbbq_zip(archive_for(names=("gbbq", "gbbq.map", "zhb.zip"))),
                         decode_gbbq(ENCODED))
        for names in ((), ("gbbq", "gbbq"), ("gbbq", "GBBQ"), ("extra",),
                      ("../gbbq",), ("dir/gbbq",), ("gbbq/",)):
            with self.subTest(names=names), self.assertRaises(ValueError):
                decode_gbbq_zip(archive_for(names=names))
        with self.assertRaises(ValueError):
            decode_gbbq_zip(b"not a zip")
        with self.assertRaises(ValueError):
            decode_gbbq_zip(archive_for(payload=b"x"))
        # Mutate a stored member's bytes while preserving the central CRC.
        damaged = bytearray(archive_for(compression=zipfile.ZIP_STORED))
        name_len, extra_len = struct.unpack_from("<HH", damaged, 26)
        damaged[30 + name_len + extra_len + 4] ^= 0xff
        with self.assertRaisesRegex(ValueError, "损坏"):
            decode_gbbq_zip(bytes(damaged))
        # Invalid DEFLATE must follow the same ValueError contract as a CRC error.
        damaged = bytearray(archive_for())
        name_len, extra_len = struct.unpack_from("<HH", damaged, 26)
        damaged[30 + name_len + extra_len] = 0xff
        with self.assertRaisesRegex(ValueError, "损坏"):
            decode_gbbq_zip(bytes(damaged))

    def test_archive_rejects_oversize_and_encrypted_members(self):
        with patch("review.tdx_adjustments.MAX_GBBQ_BYTES", len(ENCODED) - 1):
            with self.assertRaises(ValueError):
                decode_gbbq(ENCODED)
        with patch("review.tdx_adjustments.MAX_GBBQ_BYTES", 200):
            with self.assertRaisesRegex(ValueError, "成员"):
                decode_gbbq_zip(archive_for(payload=b"0" * 201))
        encrypted = bytearray(archive_for())
        struct.pack_into("<H", encrypted, 6, struct.unpack_from("<H", encrypted, 6)[0] | 1)
        central = encrypted.index(b"PK\x01\x02")
        struct.pack_into("<H", encrypted, central + 8,
                         struct.unpack_from("<H", encrypted, central + 8)[0] | 1)
        with self.assertRaisesRegex(ValueError, "成员"):
            decode_gbbq_zip(bytes(encrypted))
        symlink = io.BytesIO()
        with zipfile.ZipFile(symlink, "w") as archive:
            info = zipfile.ZipInfo("gbbq")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, ENCODED)
        with self.assertRaisesRegex(ValueError, "成员"):
            decode_gbbq_zip(symlink.getvalue())
        with patch("review.tdx_adjustments.MAX_ARCHIVE_MEMBERS", 2):
            with self.assertRaisesRegex(ValueError, "成员数"):
                decode_gbbq_zip(archive_for(names=("gbbq", "a", "b")))
        with patch("review.tdx_adjustments.MAX_ARCHIVE_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "总解压"):
                decode_gbbq_zip(archive_for(names=("gbbq", "a")))

    def test_official_opaque_markets_categories_and_undated_record_are_retained(self):
        clear = bytearray(bytes.fromhex(
            "01363030303030000a273501010000a040000000410000404000000040"))
        clear[0] = 99
        clear[12] = 15
        struct.pack_into("<I", clear, 8, 18991230)
        with patch("review.tdx_adjustments._decrypt_block",
                   side_effect=[bytes(clear[:8]), bytes(clear[8:16]), bytes(clear[16:24])]):
            rows = decode_gbbq(struct.pack("<I", 1) + ENCODED[4:28] + bytes(clear[24:]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market"], "tdx99")
        self.assertEqual(rows[0]["category"], 15)
        self.assertEqual(rows[0]["date"], "1899-12-30")


class AffineAnchorTests(unittest.TestCase):
    def adjust(self, raw=100, events=(), anchor="2025-12-31", day="2026-11-10"):
        return adjusted_anchor(raw, anchor, day, list(events))

    def test_cash_bonus_and_rights_have_distinct_economic_effects(self):
        self.assertEqual(self.adjust(events=[event(cash_dividend=5)]), 99.5)
        self.assertEqual(self.adjust(events=[event(bonus_ratio=10)]), 50)
        self.assertAlmostEqual(self.adjust(raw=20, events=[
            event(rights_ratio=2, rights_price=5)]), 17.5)
        self.assertAlmostEqual(self.adjust(events=decode_gbbq(ENCODED)[:1]), 67.4)

    def test_multiple_events_compose_in_chronological_economic_order(self):
        cash = event("2026-02-01", cash_dividend=2)
        bonus = event("2026-07-01", bonus_ratio=10)
        self.assertAlmostEqual(self.adjust(events=[cash, bonus]), 49.9)
        self.assertAlmostEqual(self.adjust(events=[bonus, cash]), 49.9)
        # A dividend AFTER a split has a different per-share basis.
        self.assertAlmostEqual(self.adjust(events=[
            event("2026-02-01", bonus_ratio=10),
            event("2026-07-01", cash_dividend=2)]), 49.8)

    def test_asof_boundaries_and_suspended_gaps_do_not_need_event_day_bar(self):
        events = [event("2025-12-31", cash_dividend=50),
                  event("2026-01-03", bonus_ratio=10),
                  event("2026-11-10", cash_dividend=2),
                  event("2026-11-11", cash_dividend=500)]
        self.assertAlmostEqual(self.adjust(events=events), 49.8)
        self.assertEqual(self.adjust(events=events, anchor="2026-11-10"), 100)
        self.assertEqual(self.adjust(events=[event("2026-11-11", category=11)]), 100)

    def test_no_events_and_non_price_equity_records_are_identity(self):
        self.assertEqual(self.adjust(), 100)
        self.assertEqual(self.adjust(events=[event(category=5)]), 100)
        self.assertEqual(self.adjust(events=[event("1899-12-30")]), 100)

    def test_adjustment_keeps_full_precision_and_excludes_nonpositive_anchor(self):
        self.assertAlmostEqual(self.adjust(raw=10, events=[event(bonus_ratio=3)]), 100/13)
        for raw in (0, -1, math.nan, math.inf, True):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.adjust(raw=raw)
        with self.assertRaisesRegex(ValueError, "非正"):
            self.adjust(raw=0.1, events=[event(cash_dividend=5)])

    def test_unsupported_or_ambiguous_actions_exclude_security(self):
        for category in (11, 12, 13, 14, 15):
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, "尚不支持"):
                self.adjust(events=[event(category=category)])
        with self.assertRaisesRegex(ValueError, "重复"):
            self.adjust(events=[event(), event(cash_dividend=5)])
        with self.assertRaisesRegex(ValueError, "多个证券"):
            self.adjust(events=[event(), event(code="600001")])
        with self.assertRaises(ValueError):
            self.adjust(events=[event(cash_dividend=math.inf)])
        with self.assertRaises(ValueError):
            adjusted_anchor(100, "2025-12-31", "2026-11-10", None)
        with self.assertRaises(ValueError):
            self.adjust(events=[event("1899-12-30", cash_dividend=1)])
        with self.assertRaises(ValueError):
            self.adjust(events=[event(market="tdx99")])

    def test_malformed_dates_and_reversed_period_do_not_use_lexical_order(self):
        for anchor, end in (("2026-1-01", "2026-11-10"),
                            ("2026-02-30", "2026-11-10"),
                            ("2026-11-11", "2026-11-10"),
                            ("1899-12-31", "2026-11-10")):
            with self.subTest(anchor=anchor), self.assertRaises(ValueError):
                self.adjust(anchor=anchor, day=end)

