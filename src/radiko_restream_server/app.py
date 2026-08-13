#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from logging import INFO, Formatter, StreamHandler, getLogger

import ffmpeg
from flask import (
    Flask,
    Response,
    abort,
    send_from_directory,
)
from streamlink.session.session import Streamlink
from streamlink.stream.stream import StreamIO
from werkzeug.exceptions import BadRequest, NotFound

APP_VERSION = "0.0.1"

logger = getLogger(__name__)
handler = StreamHandler(sys.stdout)
formatter = Formatter(
    fmt="[%(asctime)s] [%(process)d] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S %z",
)

handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(INFO)

app = Flask(__name__)

HOST = "0.0.0.0"
PORT = 8888

HLS_DIR = os.path.abspath(os.path.join("hls", "radiko"))
PLAYLIST_NAME = "live.m3u8"
PLAYLIST_PATH = os.path.join(HLS_DIR, PLAYLIST_NAME)
TIMEOUT_SECONDS = 15
FFMPEG_ARGS = {"c": "copy"}
FFMPEG_ARGS_FOR_RADIKO = {"c:a": "aac", "b:a": "128k", "ar": "44100", "ac": "1"}
BUFF_SIZE = 1 << 16

EXCEPTION_MESSAGE = "Exception {0}: {1}\n"

active_ffmpeg_process = None
active_stream_fd = None
active_station_id = None
last_access_time = 0.0
monitor_thread_running = False
bridge_thread_running = False
lock = threading.Lock()


def start(station_id: str) -> bool:
    global \
        active_ffmpeg_process, \
        active_stream_fd, \
        active_station_id, \
        last_access_time, \
        monitor_thread_running, \
        bridge_thread_running

    logger.info(f"Starting HTTP Live Streaming for {station_id}")

    stop()

    try:
        streams = Streamlink().streams(f"https://radiko.jp/#!/live/{station_id}")

        if not streams or "best" not in streams:
            logger.warning(f"{station_id} is not found")
            active_station_id = None
            return False

        stream = streams["best"]
        active_stream_fd = stream.open()
        active_station_id = station_id

        stream_input = ffmpeg.input("pipe:0")
        stream_output = ffmpeg.output(
            stream_input,
            PLAYLIST_PATH,
            **FFMPEG_ARGS_FOR_RADIKO,
            f="hls",
            hls_time=4,
            hls_list_size=5,
            hls_flags="delete_segments+temp_file",
        )

        ffmpeg_args = ffmpeg.compile(stream_output)

        active_ffmpeg_process = subprocess.Popen(
            ffmpeg_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        bridge_thread_running = True
        bridge_thread = threading.Thread(
            target=transfer, args=(active_stream_fd, active_ffmpeg_process), daemon=True
        )
        bridge_thread.start()

        last_access_time = time.time()

        timeout = 10
        start_time = time.time()
        while not os.path.exists(PLAYLIST_PATH):
            if time.time() - start_time > timeout:
                stop()
                return False

            time.sleep(0.5)

        if not monitor_thread_running:
            monitor_thread_running = True
            t = threading.Thread(target=monitor, daemon=True)
            t.start()

        logger.info(f"Started HTTP Live Streaming for {station_id}")

        return True

    except OSError as e:
        logger.error(f"File/Stream operation error: {e}")
        active_station_id = None
        stop()
        return False

    except subprocess.SubprocessError as e:
        logger.error(f"Process error: {e}")
        active_station_id = None
        stop()
        return False


def stop() -> None:
    global \
        active_ffmpeg_process, \
        active_stream_fd, \
        active_station_id, \
        bridge_thread_running

    logger.info("Stopping HTTP Live Streaming")

    with lock:
        bridge_thread_running = False

        if active_ffmpeg_process:
            if active_ffmpeg_process.stdin is not None:
                try:
                    active_ffmpeg_process.stdin.close()

                except (BrokenPipeError, OSError, ValueError) as e:
                    logger.error(f"FFmpeg stdin close error: {e}")

            try:
                active_ffmpeg_process.terminate()
                active_ffmpeg_process.wait(timeout=2)

            except subprocess.TimeoutExpired as e:
                logger.warning(
                    f"FFmpeg process was not terminated within the time limit: {e}"
                )

                active_ffmpeg_process.kill()

            except OSError as e:
                logger.error(f"FFmpeg process terminate error: {e}")

            active_ffmpeg_process = None

        if active_stream_fd:
            try:
                active_stream_fd.close()

            except OSError as e:
                logger.error(f"Streamlink stream close error: {e}")

            active_stream_fd = None

        active_station_id = None

        if os.path.exists(HLS_DIR):
            shutil.rmtree(HLS_DIR)

        os.makedirs(HLS_DIR)

    logger.info("Stoped HTTP Live Streaming")


def monitor() -> None:
    global monitor_thread_running

    logger.info("Starting timeout monitor thread")

    while True:
        time.sleep(2)

        with lock:
            if (
                active_ffmpeg_process is None
                or active_ffmpeg_process.poll() is not None
            ):
                monitor_thread_running = False
                logger.warning(
                    "Since the process has stopped, monitoring will be terminated"
                )
                break

            elapsed = time.time() - last_access_time
            if elapsed > TIMEOUT_SECONDS:
                monitor_thread_running = False
                break

    if not monitor_thread_running and active_ffmpeg_process:
        stop()

    logger.info("Stoped timeout monitor thread")


def transfer(stream_fd: StreamIO, ffmpeg_proc: subprocess.Popen[bytes]) -> None:
    logger.info("Starting transferring data from Streamlink to FFmpeg")

    try:
        while bridge_thread_running:
            data = stream_fd.read(BUFF_SIZE)
            if not data:
                logger.info("End of the stream has been reached")
                break

            if ffmpeg_proc is None or ffmpeg_proc.stdin is None:
                logger.warning("FFmpeg process or stdin is unavailable")
                break

            elif ffmpeg_proc.poll() is None:
                ffmpeg_proc.stdin.write(data)
                ffmpeg_proc.stdin.flush()

            else:
                break

    except (BrokenPipeError, OSError, ValueError, TypeError) as e:
        logger.error(f"Streaming data transfer error: {e}")

    finally:
        if ffmpeg_proc is not None and ffmpeg_proc.stdin is not None:
            try:
                ffmpeg_proc.stdin.close()

            except BrokenPipeError, OSError, ValueError:
                pass

        logger.info("Stopped transferring data")


def update_access_time() -> None:
    global last_access_time

    with lock:
        last_access_time = time.time()


@app.route("/stream/<station_id>/live.m3u8")
def serve_m3u8(station_id: str) -> Response:
    update_access_time()

    if active_station_id is not None and active_station_id != station_id:
        logger.info(
            f"Requested station changed from {active_station_id} to {station_id}."
        )
        stop()

    if active_ffmpeg_process is None or active_ffmpeg_process.poll() is not None:
        success = start(station_id)
        if not success:
            return abort(500, "Error start the streaming faild.")

    return send_from_directory(HLS_DIR, PLAYLIST_NAME)


@app.route("/stream/<station_id>/<filename>")
def serve_ts(station_id: str, filename: str) -> Response:
    update_access_time()

    return send_from_directory(HLS_DIR, filename)


@app.errorhandler(Exception)
def error(error):
    if isinstance(error, (NotFound, BadRequest)):
        response = app.response_class(
            EXCEPTION_MESSAGE.format(type(error).__name__, error),
            status=error.code,
            mimetype="text/plain",
        )
        return response

    return app.response_class(
        EXCEPTION_MESSAGE.format(type(error).__name__, error),
        status=500,
        mimetype="text/plain",
    )


def isDebug():
    return app.config["DEBUG"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p",
        type=int,
        help="When you want to specific a port number.",
    )
    parser.add_argument("-d", action="store_true", help="Start in debug mode.")

    args = parser.parse_args()

    if os.path.exists(HLS_DIR):
        shutil.rmtree(HLS_DIR)

    logger.info(f"Starting radiko restream server {APP_VERSION}")

    app.run(
        host=HOST,
        port=args.p if args.p is not None else PORT,
        debug=args.d,
        threaded=True,
    )


if "__main__" == __name__:
    main()
