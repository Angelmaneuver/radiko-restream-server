#!/usr/bin/env python3

import argparse

from flask import Flask, Response, abort, request
from streamlink.session.session import Streamlink
from streamlink.stream.stream import StreamIO

app = Flask(__name__)

HOST = "0.0.0.0"
PORT = 8888

BUFF_SIZE = 1 << 16
PAUSE = 600

EXCEPTION_MESSAGE = "Exception {0}: {1}\n"


@app.route("/radiko", methods=["GET"])
def radiko() -> Response:
    args = request.args.to_dict()
    option = {}

    if "sid" not in args:
        abort(
            400,
            EXCEPTION_MESSAGE.format("Invalid parameter", "Require sid."),
        )

    option["sid"] = args["sid"]
    option["stream"] = args.get("stream", "best")

    streams = Streamlink().streams("https://radiko.jp/#!/live/{}".format(option["sid"]))

    if streams is None or option["stream"] not in streams:
        abort(
            404,
            EXCEPTION_MESSAGE.format("Stream not found", option["stream"]),
        )

    return Response(
        response=__streaming(streams[option["stream"]].open()),
        mimetype="audio/mpeg",
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Expires": "0",
        },
        direct_passthrough=True,
    )


@app.errorhandler(Exception)
def error(error):
    abort(
        500,
        EXCEPTION_MESSAGE.format(type(error).__name__, error),
    )


def __streaming(fd: StreamIO):
    chunk = True

    with fd:
        while chunk:
            chunk = fd.read(BUFF_SIZE)

            yield chunk


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

    app.run(debug=args.d, host=HOST, port=args.p if args.p is not None else PORT)


if "__main__" == __name__:
    main()
