from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from digital_pulse.api import create_app


class ApiTests(unittest.TestCase):
    def test_d1_device_demo_handshake_and_state(self):
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(Path(directory)))
            response = client.get("/api/device/d1-demo?fragment_size=3")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertTrue(body["connected"])
            self.assertEqual([item["command"] for item in body["exchanges"]], ["HELLO", "CAPABILITIES", "START", "STOP"])
            self.assertTrue(all(item["status"] == "ACK" for item in body["exchanges"]))
            self.assertEqual(body["final_state"], "IDLE")

    def test_simulation_endpoint_creates_report(self):
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(Path(directory)))
            self.assertEqual(client.get("/api/health").json()["status"], "ok")
            response = client.post("/api/sessions/simulate", json={"sample_rate_hz": 100, "heart_rate_bpm": 72, "target_forces": [40, 80, 120], "stabilize_s": 0.2, "acquire_s": 4})
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertTrue(body["manifest"]["completed"])
            self.assertTrue(body["report"]["analysis_allowed"])
            self.assertEqual(len(client.get("/api/sessions").json()), 1)

    def test_websocket_streams_samples_and_completion(self):
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(Path(directory)))
            with client.websocket_connect("/ws/simulate") as websocket:
                websocket.send_json({"sample_rate_hz": 50, "heart_rate_bpm": 72, "target_forces": [80], "stabilize_s": 0, "acquire_s": 3})
                sample_messages = 0
                while True:
                    message = websocket.receive_json()
                    if message["type"] == "samples":
                        sample_messages += 1
                    if message["type"] == "complete":
                        self.assertTrue(message["manifest"]["completed"])
                        break
                self.assertGreater(sample_messages, 0)
