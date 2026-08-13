import unittest

from flask import Response

from radiko_restream_server import app as app_module


class ServeM3u8StationSwitchTests(unittest.TestCase):
    def setUp(self):
        self.original_start = app_module.start
        self.original_stop = app_module.stop
        self.original_active_station_id = getattr(app_module, "active_station_id", None)
        self.original_active_ffmpeg_process = app_module.active_ffmpeg_process
        self.original_send_from_directory = app_module.send_from_directory

        self.calls = []

        def fake_start(station_id):
            self.calls.append(("start", station_id))
            app_module.active_station_id = station_id
            return True

        def fake_stop():
            self.calls.append(("stop", app_module.active_station_id))
            app_module.active_station_id = None
            app_module.active_ffmpeg_process = None

        class DummyProcess:
            def poll(self):
                return None

        app_module.start = fake_start
        app_module.stop = fake_stop
        app_module.active_station_id = "station_a"
        app_module.active_ffmpeg_process = DummyProcess()
        app_module.send_from_directory = lambda directory, filename: Response("ok")

    def tearDown(self):
        app_module.start = self.original_start
        app_module.stop = self.original_stop
        app_module.active_station_id = self.original_active_station_id
        app_module.active_ffmpeg_process = self.original_active_ffmpeg_process
        app_module.send_from_directory = self.original_send_from_directory

    def test_stop_then_start_when_station_changes(self):
        client = app_module.app.test_client()

        response = client.get("/stream/station_b/live.m3u8")

        self.assertEqual(response.data, b"ok")
        self.assertEqual(
            self.calls,
            [("stop", "station_a"), ("start", "station_b")],
        )


if __name__ == "__main__":
    unittest.main()
