import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import common as c  # noqa: E402
import step1_organize as s1  # noqa: E402


def fake_exiftool(table):
    """table: {filename: record}. Returns a function shaped like exiftool_batch."""
    def fn(paths, ee):
        out = {}
        for p in paths:
            rec = table.get(Path(p).name)
            if rec is not None:
                out[os.path.normcase(os.path.abspath(str(p)))] = rec
        return out
    return fn


class DateTests(unittest.TestCase):
    def test_exif_original_wins(self):
        rec = {"ExifIFD:DateTimeOriginal": "2023:05:14 10:22:31",
               "ExifIFD:CreateDate": "2023:06:01 00:00:00"}
        dt, src = c.date_from_record(rec)
        self.assertEqual(dt, datetime(2023, 5, 14, 10, 22, 31))
        self.assertIn("DateTimeOriginal", src)

    def test_quicktime_creationdate_beats_utc_createdate(self):
        rec = {"QuickTime:CreateDate": "2023:05:15 02:00:00",
               "Keys:CreationDate": "2023:05:14 19:00:00-07:00"}
        dt, _ = c.date_from_record(rec)
        self.assertEqual(dt.date().isoformat(), "2023-05-14")

    def test_m2ts_stream_date(self):
        dt, src = c.date_from_record({"H264:DateTimeOriginal": "2011:07:04 15:30:00"})
        self.assertEqual(dt.date().isoformat(), "2011-07-04")

    def test_bogus_dates_rejected(self):
        for bad in ("0000:00:00 00:00:00", "2000:01:01 00:00:00", "1904:01:01 00:00:00",
                    "2999:01:01 00:00:00"):
            self.assertIsNone(c.date_from_record({"ExifIFD:DateTimeOriginal": bad})[0], bad)

    def test_falls_through_bogus_to_next_tag(self):
        rec = {"ExifIFD:DateTimeOriginal": "0000:00:00 00:00:00",
               "ExifIFD:CreateDate": "2019:03:02 08:00:00"}
        self.assertEqual(c.date_from_record(rec)[0].date().isoformat(), "2019-03-02")

    def test_filename_dates(self):
        cases = {
            "IMG_20230514_102231.jpg": datetime(2023, 5, 14, 10, 22, 31),
            "PXL_20230514_102231123.jpg": datetime(2023, 5, 14, 10, 22, 31),
            "VID-20230514-WA0001.mp4": datetime(2023, 5, 14),
            "2023-05-14 10.22.31.jpg": datetime(2023, 5, 14, 10, 22, 31),
        }
        for name, want in cases.items():
            self.assertEqual(c.date_from_filename(name), want, name)
        self.assertIsNone(c.date_from_filename("IMG_1234.jpg"))
        self.assertIsNone(c.date_from_filename("OV7A2905.CR3"))


class SidecarTests(unittest.TestCase):
    def test_matching_and_renaming(self):
        d = Path("x")
        names = ["00012.MTS", "00012.MTS.modd", "00012.modd", "00012.mdi", "00013.modd",
                 "notes.txt"]
        sib = {n.lower(): d / n for n in names}
        found = [p.name for p in c.sidecars_for(d / "00012.MTS", sib)]
        self.assertEqual(sorted(found), ["00012.MTS.modd", "00012.mdi", "00012.modd"])
        self.assertEqual(c.sidecar_new_name("00012.MTS", "00012.MTS.modd", "a.mts"), "a.mts.modd")
        self.assertEqual(c.sidecar_new_name("00012.MTS", "00012.modd", "a.mts"), "a.modd")


class OrganizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "inbox"
        self.dest = self.root / "library"
        (self.src / "sub").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, rel, data=b"x"):
        p = self.src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_plan_apply_undo_roundtrip(self):
        self.make("a.jpg", b"A")
        self.make("sub/b.CR3", b"B")
        self.make("sub/b.xmp", b"<x/>")
        self.make("sub/00001.MTS", b"V")
        self.make("sub/00001.modd", b"M")
        self.make("sub/mystery.jpg", b"?")
        self.make("sub/Thumbs.db", b"junk")
        table = {
            "a.jpg": {"ExifIFD:DateTimeOriginal": "2023:05:14 10:00:00"},
            "b.CR3": {"ExifIFD:DateTimeOriginal": "2023:05:14 11:00:00"},
            "00001.MTS": {"H264:DateTimeOriginal": "2011:07:04 15:30:01"},
        }
        fn = fake_exiftool(table)

        # dry run changes nothing
        s1.organize(self.src, self.dest, apply=False, exiftool_fn=fn, quiet=True)
        self.assertTrue((self.src / "a.jpg").exists())
        self.assertFalse((self.dest / "2023-05-14").exists())

        s1.organize(self.src, self.dest, apply=True, exiftool_fn=fn, quiet=True)
        day = self.dest / "2023-05-14"
        self.assertTrue((day / "a.jpg").exists())
        self.assertTrue((day / "b.CR3").exists())
        self.assertTrue((day / "b.xmp").exists())          # sidecar followed
        self.assertTrue((self.dest / "2011-07-04" / "2011-07-04_153001.mts").exists())
        self.assertTrue((self.dest / "2011-07-04" / "2011-07-04_153001.modd").exists())
        self.assertTrue((self.dest / c.UNDATED_DIR / "sub" / "mystery.jpg").exists())
        self.assertFalse((self.src / "a.jpg").exists())

        manifests = sorted((self.dest / c.REPORTS_DIR).glob("step1_manifest_*.csv"))
        self.assertEqual(len(manifests), 1)
        c.undo_manifest(manifests[0], apply=True)
        self.assertTrue((self.src / "a.jpg").exists())
        self.assertTrue((self.src / "sub" / "00001.modd").exists())
        self.assertFalse((day / "a.jpg").exists())

    def test_collision_and_identical_routing(self):
        self.make("one/IMG_1.jpg", b"SAME")
        self.make("two/IMG_1.jpg", b"SAME")      # identical content
        self.make("three/IMG_1.jpg", b"DIFFERENT")
        rec = {"ExifIFD:DateTimeOriginal": "2022:01:02 03:04:05"}
        fn = fake_exiftool({"IMG_1.jpg": rec})
        s1.organize(self.src, self.dest, apply=True, exiftool_fn=fn, quiet=True)
        day = self.dest / "2022-01-02"
        names = sorted(p.name for p in day.iterdir())
        self.assertEqual(len(names), 2)               # one original + one renamed
        self.assertTrue(any("(1)" in n for n in names))
        dup_dir = self.dest / c.DUPLICATES_DIR / "2022-01-02"
        self.assertEqual(len(list(dup_dir.iterdir())), 1)

    def test_filesystem_date_is_not_used(self):
        p = self.make("old.jpg")
        os.utime(p, (1_000_000_000, 1_000_000_000))  # 2001
        entries, _ = s1.organize(self.src, self.dest, apply=False,
                                 exiftool_fn=fake_exiftool({}), quiet=True)[0:2]
        self.assertIsNone(entries[0].dt)
        self.assertEqual(entries[0].dst_dir.name, c.UNDATED_DIR)


class DuplicateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import PIL  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Pillow not installed")
        import step3_find_duplicates as s3
        cls.s3 = s3

    def _img(self, seed, size=(320, 240)):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", size, (30, 30, 30))
        d = ImageDraw.Draw(img)
        import random
        rnd = random.Random(seed)
        for _ in range(25):
            x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
            r = rnd.randrange(15, 60)
            d.ellipse([x - r, y - r, x + r, y + r],
                      fill=tuple(rnd.randrange(256) for _ in range(3)))
        return img

    def test_resized_copy_matches_unrelated_does_not(self):
        from PIL import Image
        a = self._img(1)
        a_small = a.resize((160, 120), Image.LANCZOS)
        b = self._img(2)
        ha, hs, hb = (self.s3.dhash(x) for x in (a, a_small, b))
        self.assertLessEqual(self.s3.hamming(ha, hs), 10)
        self.assertGreater(self.s3.hamming(ha, hb), 40)

    def test_keeper_order_and_protection(self):
        s3 = self.s3
        with tempfile.TemporaryDirectory() as d:
            small = Path(d) / "a.jpg"
            big = Path(d) / "b.jpg"
            small.write_bytes(b"1")
            big.write_bytes(b"22")
            a, b = s3.Item(small), s3.Item(big)
            a.w, a.h, b.w, b.h = 100, 100, 200, 200
            self.assertIs(s3.choose_keeper([a, b])[0], b)
            a.protected = True
            self.assertIs(s3.choose_keeper([a, b])[0], a)

    def test_exact_detection(self):
        s3 = self.s3
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for name, data in (("a.jpg", b"same"), ("b.jpg", b"same"), ("c.jpg", b"diff!")):
                p = Path(d) / name
                p.write_bytes(data)
                paths.append(s3.Item(p))
            groups = s3.find_exact(paths)
            self.assertEqual(len(groups), 1)
            self.assertEqual({i.path.name for i in groups[0]}, {"a.jpg", "b.jpg"})


class QuarantineTests(unittest.TestCase):
    def test_quarantine_moves_dups_with_sidecar_and_undo(self):
        import step4_quarantine as s4
        with tempfile.TemporaryDirectory() as t:
            dest = Path(t) / "lib"
            day = dest / "2023-01-01"
            day.mkdir(parents=True)
            keep, dup, side = day / "k.jpg", day / "d.jpg", day / "d.xmp"
            keep.write_bytes(b"K")
            dup.write_bytes(b"D")
            side.write_bytes(b"<x/>")
            rep = dest / c.REPORTS_DIR / "duplicates_x.csv"
            c.write_csv(rep, ["folder", "group_id", "method", "role", "path", "sha256"], [
                {"folder": "2023-01-01", "group_id": "g1", "method": "exact", "role": "keep",
                 "path": str(keep), "sha256": c.sha256_file(keep)},
                {"folder": "2023-01-01", "group_id": "g1", "method": "exact", "role": "dup",
                 "path": str(dup), "sha256": c.sha256_file(dup)},
            ])
            s4.main([str(rep), "--apply"])
            q = dest / c.DUPLICATES_DIR / "2023-01-01"
            self.assertTrue((q / "d.jpg").exists() and (q / "d.xmp").exists())
            self.assertTrue(keep.exists() and not dup.exists())
            mf = next((dest / c.REPORTS_DIR).glob("step4_manifest_*.csv"))
            c.undo_manifest(mf, apply=True)
            self.assertTrue(dup.exists() and side.exists())


if __name__ == "__main__":
    unittest.main()
