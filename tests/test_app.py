import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radiko_restream_server.app import app


class RadikoRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()

    def test_radiko_serves_stream_with_audio_mime_type(self) -> None:
        with patch("radiko_restream_server.app.Streamlink") as mock_streamlink_cls:
            mock_streamlink_instance = Mock()
            mock_stream = Mock()
            mock_stream.open.return_value = io.BytesIO(b"dummy")
            mock_streamlink_instance.streams.return_value = {"best": mock_stream}
            mock_streamlink_cls.return_value = mock_streamlink_instance

            response = self.client.get("/radiko?sid=test", buffered=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "audio/mpeg")
        self.assertEqual(response.headers.get("Cache-Control"), "no-cache")


if __name__ == "__main__":
    unittest.main()
