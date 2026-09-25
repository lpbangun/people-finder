import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).with_name("jev_rank.py")


class JevRankTests(unittest.TestCase):
    def test_replay_keeps_current_company_and_linkedin_gate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc = {
                "profile_summary": "Founder and learning designer",
                "job": {"company": "ExampleCo", "title": "Chief of Staff", "summary": "Founder operations"},
                "candidates": [
                    {"candidate_id": "valid", "name": "Alex Example", "current_company": "ExampleCo", "current_title": "Operations Lead", "linkedin_url": "https://www.linkedin.com/in/alex-example", "evidence": "Current operations lead"},
                    {"candidate_id": "former", "name": "Blair Example", "current_company": "Elsewhere", "current_title": "Operations Lead", "linkedin_url": "https://www.linkedin.com/in/blair-example", "evidence": "Former employee"},
                ],
            }
            answer = {"answers": {"role_fit": {"score": 2}, "shared_context": {"score": 1}, "evidence": {"score": 2}, "route": {"choice": "peer"}}}
            (root / "input.json").write_text(json.dumps(doc), encoding="utf-8")
            (root / "responses.json").write_text(json.dumps({"valid": answer}), encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPT), str(root / "input.json"), "--responses", str(root / "responses.json"), "--out", str(root / "output.json")], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            output = json.loads((root / "output.json").read_text(encoding="utf-8"))
            self.assertEqual([person["candidate_id"] for person in output["selected"]], ["valid"])
            self.assertEqual(output["shortlist"], ["valid"])
            self.assertFalse(output["email_lookup_authorized_by_this_file"])

    def test_uncertain_route_is_not_shortlisted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc = {
                "profile_summary": "People operations",
                "job": {"company": "ExampleCo", "title": "People Strategy", "summary": "Hiring engine"},
                "candidates": [
                    {"candidate_id": "peer", "name": "Peer Example", "current_company": "ExampleCo",
                     "current_title": "People Ops", "linkedin_url": "https://www.linkedin.com/in/peer-example",
                     "evidence": "Current people team"},
                    {"candidate_id": "uncertain", "name": "Uncertain Example", "current_company": "ExampleCo",
                     "current_title": "Leader", "linkedin_url": "https://www.linkedin.com/in/uncertain-example",
                     "evidence": "Current leader"},
                ],
            }
            def answer(lane, confidence):
                return {"answers": {"role_fit": {"score": 2}, "shared_context": {"score": 2},
                                    "evidence": {"score": 2},
                                    "route": {"choice": lane, "confidence": confidence}}}
            (root / "input.json").write_text(json.dumps(doc), encoding="utf-8")
            (root / "responses.json").write_text(
                json.dumps({"peer": answer("peer", 0.9), "uncertain": answer("hiring", 0.32)}),
                encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPT), str(root / "input.json"),
                                  "--responses", str(root / "responses.json"),
                                  "--out", str(root / "output.json")], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            output = json.loads((root / "output.json").read_text(encoding="utf-8"))
            self.assertEqual(output["shortlist"], ["peer"])
            self.assertTrue(next(row for row in output["ranked"] if row["candidate_id"] == "uncertain")["route_needs_review"])

    def test_peer_hiring_ambiguity_preserves_relevant_contact(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc = {
                "profile_summary": "People operations",
                "job": {"company": "ExampleCo", "title": "People Strategy", "summary": "Hiring engine"},
                "candidates": [
                    {"candidate_id": "ambiguous", "name": "Alex Example", "current_company": "ExampleCo",
                     "current_title": "People Ops", "linkedin_url": "https://www.linkedin.com/in/alex-example",
                     "evidence": "Current people team"},
                ],
            }
            answer = {"answers": {
                "role_fit": {"score": 2}, "shared_context": {"score": 2}, "evidence": {"score": 2},
                "route": {"choice": "peer", "confidence": 0.52,
                          "probabilities": {"peer": 0.68, "hiring": 0.31, "neither": 0.01}},
            }}
            (root / "input.json").write_text(json.dumps(doc), encoding="utf-8")
            (root / "responses.json").write_text(json.dumps({"ambiguous": answer}), encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPT), str(root / "input.json"),
                                  "--responses", str(root / "responses.json"),
                                  "--out", str(root / "output.json")], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            output = json.loads((root / "output.json").read_text(encoding="utf-8"))
            self.assertEqual(output["shortlist"], ["ambiguous"])
            self.assertTrue(output["ranked"][0]["lane_ambiguous"])


if __name__ == "__main__":
    unittest.main()
