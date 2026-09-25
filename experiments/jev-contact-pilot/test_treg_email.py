import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import treg_email


class TregEmailTests(unittest.TestCase):
    def test_only_jev_selected_people_are_looked_up_once(self):
        spec = {"job": {"company": "Example", "domain": "example.com"}}
        people = [{"candidate_id": id, "name": name,
                   "linkedin_url": "https://www.linkedin.com/in/" + id}
                  for id, name in (("one", "One Person"), ("two", "Two Person"),
                                   ("three", "Three Person"))]
        result = {"job": {"company": "Example"}, "shortlist": ["one", "two"],
                  "ranked": people}
        calls = []

        def transport(person, domain, token, cap):
            calls.append(person["candidate_id"])
            return {"request": {"full_name": person["name"], "domain": domain,
                                "linkedin_url": person["linkedin_url"]},
                    "call_id": "fixture", "cost_usd": 0.01,
                    "body": {"output": {"email": person["candidate_id"] + "@example.com"}}}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = treg_email.run(spec, result, out, "secret", 0.05, transport=transport)
            second = treg_email.run(spec, result, out, "secret", 0.05, transport=transport)
            self.assertEqual(calls, ["one", "two"])
            self.assertEqual(first, second)
            self.assertEqual([row["email"] for row in first], ["one@example.com", "two@example.com"])
            self.assertNotIn("secret", (out / "treg-email-results.json").read_text())

    def test_rejects_unranked_or_unsafe_id(self):
        result = {"shortlist": ["../escape"], "ranked": [{"candidate_id": "../escape"}]}
        with self.assertRaises(ValueError):
            treg_email.select_people(result, ())
        with self.assertRaises(ValueError):
            treg_email.select_people(result, ("missing",))


if __name__ == "__main__":
    unittest.main()
