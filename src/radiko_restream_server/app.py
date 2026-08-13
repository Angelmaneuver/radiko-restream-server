#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import threading
import time

import ffmpeg
from flask import (
    Flask,
    Response,
    abort,
    send_from_directory,
)
from streamlink.session.session import Streamlink
from werkzeug.exceptions import BadRequest, NotFound

app = Flask(__name__)

HOST = "0.0.0.0"
PORT = 8888

BUFF_SIZE = 1 << 16

HLS_DIR = os.path.abspath(os.path.join("hls", "radiko"))
PLAYLIST_NAME = "live.m3u8"
PLAYLIST_PATH = os.path.join(HLS_DIR, PLAYLIST_NAME)

TIMEOUT_SECONDS = 15

EXCEPTION_MESSAGE = "Exception {0}: {1}\n"

active_ffmpeg_process = None
active_stream_fd = None
last_access_time = 0.0
monitor_thread_running = False
bridge_thread_running = False
lock = threading.Lock()


def start(station_id: str) -> bool:
    global \
        active_ffmpeg_process, \
        active_stream_fd, \
        last_access_time, \
        monitor_thread_running, \
        bridge_thread_running

    stop()

    try:
        streams = Streamlink().streams(f"https://radiko.jp/#!/live/{station_id}")

        if not streams or "best" not in streams:
            print(f"{station_id} is not found.")
            return False

        stream = streams["best"]
        active_stream_fd = stream.open()

        stream_input = ffmpeg.input("pipe:0")
        stream_output = ffmpeg.output(
            stream_input,
            PLAYLIST_PATH,
            **{"c:a": "aac", "b:a": "64", "ar": "44100", "ac": "1"},
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

        return True

    except OSError as e:
        print(f"Error file/stream operation: {e}")
        stop()
        return False

    except subprocess.SubprocessError as e:
        print(f"Error process execute failed: {e}")
        stop()
        return False


def stop() -> None:
    global active_ffmpeg_process, active_stream_fd, bridge_thread_running

    with lock:
        bridge_thread_running = False

        if active_ffmpeg_process:
            print("Stoping streaming.")

            if active_ffmpeg_process.stdin is not None:
                try:
                    active_ffmpeg_process.stdin.close()

                except BrokenPipeError, OSError, ValueError:
                    print("Error failed to close ffmpeg stdin.")

            try:
                active_ffmpeg_process.terminate()
                active_ffmpeg_process.wait(timeout=2)

            except subprocess.TimeoutExpired:
                active_ffmpeg_process.kill()

            except OSError as e:
                print(f"Error terminating process: {e}")

            active_ffmpeg_process = None

        if active_stream_fd:
            try:
                active_stream_fd.close()

            except OSError as e:
                print(f"Error closing stream: {e}")

            active_stream_fd = None

        if os.path.exists(HLS_DIR):
            shutil.rmtree(HLS_DIR)

        os.makedirs(HLS_DIR)


def monitor() -> None:
    global monitor_thread_running

    print("Start the timeout monitoring thread.")

    while True:
        time.sleep(2)

        with lock:
            if (
                active_ffmpeg_process is None
                or active_ffmpeg_process.poll() is not None
            ):
                monitor_thread_running = False
                print("Since the process has stopped, monitoring will be terminated.")
                break

            elapsed = time.time() - last_access_time
            if elapsed > TIMEOUT_SECONDS:
                monitor_thread_running = False
                break

    if not monitor_thread_running and active_ffmpeg_process:
        stop()


def transfer(stream_fd, ffmpeg_proc) -> None:
    print("Started transferring data from Streamlink to FFmpeg.")

    try:
        while bridge_thread_running:
            data = stream_fd.read(BUFF_SIZE)
            if not data:
                print("The end of the stream has been reached.")
                break

            if ffmpeg_proc is None or ffmpeg_proc.stdin is None:
                print("ffmpeg process or stdin is unavailable.")
                break

            elif ffmpeg_proc.poll() is None:
                ffmpeg_proc.stdin.write(data)
                ffmpeg_proc.stdin.flush()

            else:
                break

    except (BrokenPipeError, OSError, ValueError, TypeError) as e:
        print(f"Error transfering stream: {e}")

    finally:
        if ffmpeg_proc is not None and ffmpeg_proc.stdin is not None:
            try:
                ffmpeg_proc.stdin.close()

            except BrokenPipeError, OSError, ValueError:
                pass

        print("Ended transferring data from Streamlin to FFmpeg.")


def update_access_time() -> None:
    global last_access_time

    with lock:
        last_access_time = time.time()


@app.route("/stream/<station_id>/live.m3u8")
def serve_m3u8(station_id: str) -> Response:
    update_access_time()

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

    app.run(
        host=HOST,
        port=args.p if args.p is not None else PORT,
        debug=args.d,
        threaded=True,
    )


if "__main__" == __name__:
    main()
