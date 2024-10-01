from werkzeug.utils import cached_property
import os
import http
import websocket
import json
import logging
from enum import Enum
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.request import urlretrieve
import concurrent.futures
import io
from .buffer_struct import Struct, BytesField, UIntField
from octoprint.settings import settings
from octoprint.webcams import get_snapshot_webcam

logging.basicConfig(level=logging.DEBUG)


class BufferMessage(Struct):
    channel = BytesField(64)
    event = BytesField(16)
    dataType = UIntField()
    data = BytesField()


class MessageType(Enum):
    BUFFER = 0
    OBJECT = 1
    NONE = 2


class ForwarderMessage:
    def __init__(self, message):
        data = BufferMessage.unpack(message)
        self.channel = data["channel"].decode("utf-8")
        self.event = data["event"].decode("utf-8")
        self.dataType = MessageType(int(data["dataType"]))
        if self.dataType == MessageType.BUFFER:
            self.data = data["data"]
        elif self.dataType == MessageType.OBJECT:
            self.data = json.loads(data["data"].decode("utf-8"))
        else:
            self.data = None

    def __str__(self):
        return f"{self.channel} {self.event} {self.dataType} {self.data}"


class RequestForwarder:
    def __init__(self, base_uri, ws, logger, path_whitelist):
        self.base_uri = base_uri
        self.ws = ws
        self._channels = {}
        self.logger = logger
        self.path_whitelist = path_whitelist
        self.is_downloading = False
        self.download_progress_pct = 0.00

    def handle_request(self, message):
        channel_id = message.channel
        if channel_id not in self._channels.keys():
            channel = Channel(channel_id, self)
            self._register_channel(channel)
        channel = self._channels[channel_id]
        channel.handle_message(message)

    def _register_channel(self, channel):
        self._channels[channel.id] = channel

    def _destroy_channel(self, channel):
        if self._channels[channel.id] == channel:
            del self._channels[channel.id]


"""
Special type of request, that will download print file (gcode) to Octoprint
gcodes folder. It's special type of request not to transfer big data
through websocket tunnel between websocket proxy server and client.
"""
class FileDownloader:
    _previous_progress_value = 0

    def __init__(self, channel):
        self.channel = channel
        self.channel.handler.is_downloading = True
        self.channel.handler.download_progress_pct = 0.00

    # Function to receive data from channel
    def send(self, data):
        data = {key: value[0] for key, value in parse_qs(data.decode("utf-8")).items()}
        self.download_file_async(
            data["download_url"],  # download url
            os.path.join(settings().getBaseFolder("uploads"), data["file_name"]),  # where to save file
        )

    # Function to be called when file is downloaded.
    def download_callback(self, future):
        try:
            future.result()  # This will raise any exception caught during execution
            # send response to client, that file is downloaded and ready to print
            self.channel.send(
                "headers",
                {
                    "statusCode": 200,
                    "statusMessage": "OK",
                    "headers": [("Content-type","application/json")]
                },
            )
            self.channel.send("data", json.dumps({"message": "File downloaded."}).encode())
            self.channel.send("end")
            self.channel.handler.is_downloading = False
        except Exception as e:
            self.channel.handler.is_downloading = False
            self.channel.handle_error(msg=f"An error occurred during the download: {e}")

    # Download file asynchronously not to block main process/thread.
    def download_file_async(self, download_url, file_path):
        # Wrap the report hook to include self
        def wrapped_report_hook(block_num, block_size, total_size):
            self.download_progress_report_hook(block_num, block_size, total_size)
        executor = concurrent.futures.ThreadPoolExecutor()
        future = executor.submit(urlretrieve, download_url, file_path, wrapped_report_hook)
        future.add_done_callback(self.download_callback)

    def download_progress_report_hook(self, block_num, block_size, total_size):
        if total_size > 0:
            progress = round(min(block_num * block_size / total_size, 1.0) * 100, 2)
        else:
            progress = -1
        # report progress in 5% intervals to protect system journal/log
        if self._previous_progress_value < progress - 5:
            self._previous_progress_value = progress
            self.channel.logger.info(f"Download progress: {progress}%")
        self.channel.handler.download_progress_pct = progress

    # Dummy function to be called by channel. We don't want to close connection
    # automatically and instead close it manually when file is downloaded
    def dummy_end(channel, message):
        pass


class Channel:
    def __init__(self, id, handler):
        self.id = id
        self.event_handlers = {
            "headers": self.handle_headers,
            "data": self.handle_data,
            "end": self.handle_end,
        }
        self.handler = handler
        self.connection = None
        self.logger = handler.logger

    def handle_message(self, message):
        try:
            event = message.event
            if event in self.event_handlers.keys():
                self.event_handlers[event](message)
            else:
                self.logger.warning("Unknown event:", event)
                self.handle_error(400, "Unknown event {event}")
        except Exception as e:
            self.logger.exception(e)


    @cached_property
    def snapshot_url(self):
        return get_snapshot_webcam().config.snapshotDisplay

    @cached_property
    def path_whitelist(self):
        return list(filter(None, self.handler.path_whitelist))

    def handle_headers(self, message):
        ireq = message.data
        forward_to_url = urljoin(self.handler.base_uri, ireq["url"])
        self.req_params = {
            "method": ireq.get("method"),
            "url": forward_to_url,
            "headers": ireq.get("headers"),
            "params": ireq.get("search", ""),
            "port": ireq.get("headers", {}).get("x-karmen-port")
        }
        headers = ireq.get("headers")

        if headers["host"]:
            del headers["host"]
        # For requests with x-karmen-port header check if this port matches webcam snapshot port
        if self.req_params["port"]:
            if self.snapshot_url:
                parsed = urlparse(self.snapshot_url)
                port = parsed.port
                host = parsed.hostname
                if port == int(self.req_params["port"]):
                    self.connection = http.client.HTTPConnection(host, port=port)
                else:
                    self.logger.warning(f"Only allowed port is snapshot port {port}")
                    self.handle_error()
                    return
            else:
                self.logger.warning("Access to non default port is allowed for snapshot url only")
                self.handle_error()
                return
        elif self.req_params["url"].startswith("/$download_file"):
            self.connection = FileDownloader(self)
            self.event_handlers['end'] = self.connection.dummy_end
            return
        else:
            # For octoprint requests check if path starts with /api/ so only API calls are allowed
            if not self.req_params["url"].startswith(tuple(self.path_whitelist)):
                self.logger.warning(f"Access to non-whitelisted url is not allowed {self.req_params['url']}")
                self.handle_error()
                return
            self.connection = http.client.HTTPConnection(self.handler.base_uri)
        self.connection.connect()

        self.logger.debug(
            f'incoming request: {self.id} {self.req_params["method"]} {ireq["url"]}'
        )
        self.connection.putrequest(
            self.req_params["method"], ireq["url"], skip_host=True
        )

        for k, v in headers.items():
            self.connection.putheader(k, v)
        self.connection.endheaders()

    def handle_data(self, message):
        if self.connection:
            self.connection.send(message.data.encode())

    def handle_end(self, message):
        if self.connection:
            response = self.connection.getresponse()
            headers = response.getheaders()
            body = response.read()
            status = response.status
            self.logger.debug(f"reply to request: {self.id} {status} {response.reason}")
            self.send(
                "headers",
                {
                    "statusCode": status,
                    "statusMessage": response.reason,
                    "headers": headers,
                },
            )
            self.send("data", body)
            self.connection.close()
        self.send("end")


    def handle_error(self, status=400, msg="Invalid request"):
        self.send(
            "headers",
            {
                "statusCode": status,
                "statusMessage": msg,
            },
        )
        self.send("end")
        if self.connection:
            self.connection.close()


    def send(self, event, data=None):
        data_type = MessageType.BUFFER
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            data_type = MessageType.OBJECT
        if data is None:
            data = b""
            data_type = MessageType.NONE
        buf = io.BytesIO()
        msg = {
            "channel": str.encode(self.id),
            "event": str.encode(event),
            "dataType": data_type.value,
            "data": data,
        }

        BufferMessage.pack(msg, buf)
        buf.seek(0)
        self.handler.ws.send(buf.read(), websocket.ABNF.OPCODE_BINARY)


