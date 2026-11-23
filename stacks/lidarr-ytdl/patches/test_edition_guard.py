import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edition_guard as g


def alb(i, title, have, total):
    return {"id": i, "title": title, "statistics": {"trackFileCount": have, "trackCount": total}}


class Guard(unittest.TestCase):
    def test_base_title(self):
        self.assertEqual(g.base_title("Daydream (30th Anniversary)"), "daydream")
        self.assertEqual(g.base_title("Nevermind (30th Anniversary Super Deluxe)"), "nevermind")
        self.assertEqual(g.base_title("After Hours (AFTER HOURS TIL DAWN COLLECTION)"), "after hours")
        self.assertEqual(g.base_title("Wildflowers & All the Rest (Super Deluxe Edition)"), "wildflowers all the rest")
        self.assertEqual(g.base_title("Greatest Hits II"), "greatest hits ii")

    def test_edition_of_owned_album_is_skipped(self):
        lib = [alb(1, "After Hours", 18, 18), alb(2, "After Hours (AFTER HOURS TIL DAWN COLLECTION)", 0, 15)]
        self.assertEqual(g.owned_sibling(lib[1], lib)["id"], 1)

    def test_different_album_is_not(self):
        lib = [alb(1, "Greatest Hits", 15, 15), alb(2, "Greatest Hits II", 0, 15),
               alb(3, "Curtain Call: The Hits", 17, 17), alb(4, "Curtain Call 2", 0, 35)]
        self.assertIsNone(g.owned_sibling(lib[1], lib))
        self.assertIsNone(g.owned_sibling(lib[3], lib))

    def test_barely_owned_sibling_does_not_count(self):
        lib = [alb(1, "Nevermind", 3, 12), alb(2, "Nevermind (Deluxe)", 0, 30)]
        self.assertIsNone(g.owned_sibling(lib[1], lib))

    def test_never_matches_itself_and_empty_base(self):
        lib = [alb(1, "Deluxe", 10, 10), alb(2, "Deluxe Edition", 0, 10)]
        self.assertIsNone(g.owned_sibling(lib[1], lib))
        self.assertIsNone(g.owned_sibling(alb(5, "X", 0, 1), [alb(5, "X", 1, 1)]))

    def test_prefers_most_owned_sibling(self):
        lib = [alb(1, "Born Sinner", 10, 21), alb(2, "Born Sinner (Deluxe)", 21, 21), alb(3, "Born Sinner (Explicit)", 0, 21)]
        self.assertEqual(g.owned_sibling(lib[2], lib)["id"], 2)


if __name__ == "__main__":
    unittest.main()
