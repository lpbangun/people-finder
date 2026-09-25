import importlib.util
import unittest
from pathlib import Path


path = Path(__file__).with_name("treg_jev.py")
spec = importlib.util.spec_from_file_location("treg_jev", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TregJevTests(unittest.TestCase):
    def test_rejects_same_named_company_without_matching_domain_or_profile(self):
        recording = {
            "call_id": "recorded-call",
            "body": {
                "_treg": {"served_by": "sample.people.search"},
                "output": {"people": [
                    {"first_name": "A", "last_name": "Person", "title": "People Lead",
                     "company_name": "Garage", "company_url": "shopgarage.com",
                     "employee_linkedin": "https://www.linkedin.com/in/a-person"},
                    {"first_name": "B", "last_name": "Person", "title": "People Lead",
                     "company_name": "Garage", "company_url": "garageclothing.com",
                     "employee_linkedin": "https://www.linkedin.com/in/b-person"},
                    {"first_name": "C", "last_name": "Person", "title": "People Lead",
                     "company_name": "Garage", "company_url": "shopgarage.com",
                     "employee_linkedin": ""},
                ]},
            },
        }
        people = module.normalize_people(recording, {"company": "Garage", "domain": "shopgarage.com"}, 0)
        self.assertEqual([person["name"] for person in people], ["A Person"])
        self.assertEqual(people[0]["candidate_id"], "a-person")


if __name__ == "__main__":
    unittest.main()
