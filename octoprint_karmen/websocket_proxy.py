import http
from tarfile import RECORDSIZE
import websocket
import json
import threading
import time
import logging
from enum import Enum
from urllib.parse import urljoin, urlparse
import io
import sched, time
from .buffer_struct import Struct, BytesField, UIntField
from octoprint.settings import settings
from werkzeug.utils import cached_property

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


from threading import Timer

class RepeatedTimer(object):
    def __init__(self, interval, function, *args, **kwargs):
        self._timer     = None
        self.function   = function
        self.interval   = interval
        self.args       = args
        self.kwargs     = kwargs
        self.is_running = False
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.interval, self._run)
            self._timer.daemon = True
            self._timer.start()
            self.is_running = True

    def stop(self):
        self._timer.cancel()
        self.is_running = False


class PingPonger:
    def __init__(self, ws, logger):
        self.logger = logger
        self.ws = ws
        self.gotPong = True

    def handle_request(self, message):
        if message.event == "pong":
            self.gotPong = True
            self.logger.debug("Received pong")

    def ping(self, on_close):
        if not self.gotPong:
            self.logger.warning("No pong response received")
            try:
                on_close()
                self.logger.warning("closing connection")
                return
            except Exception as e:
                self.logger.error("Unable to reconnect", e)
        else:
            self.logger.debug("Going to ping")
            buf = io.BytesIO()
            msg = {
                "channel": str.encode("ping-pong"),
                "event": str.encode("ping"),
                "dataType": MessageType.NONE.value,
                "data": b"",
            }

            BufferMessage.pack(msg, buf)
            buf.seek(0)
            self.ws.send(buf.read(), websocket.ABNF.OPCODE_BINARY)
            self.gotPong = False


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
        event = message.event
        if event in self.event_handlers.keys():
            self.event_handlers[event](message)
        else:
            self.logger.warning("Unknown event:", event)


    @cached_property
    def snapshot_url(self):
        s = settings()
        return s.get(["webcam", "snapshot"])

    @cached_property
    def path_whitelist(self):
        return list(filter(None, self.handler.path_whitelist.split(";")))

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
                self.logger.warning(f"Access to non default port is allowed for snapshot url only")
                self.handle_error()
                return
        else:
            # For octoprint requests check if path starts with /api/ so only API calls are allowed
            if not self.req_params["url"].startswith(tuple(self.path_whitelist)):
                self.logger.warning(f"Access to non-whitelisted url is not allowed")
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


class Connector:
    def __init__(self, ws_url, base_uri, logger, whitelist):
        self.ws_url = ws_url
        self.ws = None
        self.ws_thread = None
        self.should_end = False
        self.base_uri = base_uri
        self.logger = logger
        self.path_whitelist = whitelist
        self.request_forwarder = None
        self.timer = RepeatedTimer(10, self.on_timer_tick)
        self.connected = False

    def on_message(self, ws, message):
        def run(*args):
            try:
                data = ForwarderMessage(message)
            except Exception as e:
                logging.warning(e)
                return
            if data.channel == 'ping-pong':
                try:
                    self.ping_pong.handle_request(data)
                except Exception as e:
                    logging.error(e)
            else:
                try:
                    self.request_forwarder.handle_request(data)
                except Exception as e:
                    logging.error(e)

        threading.Thread(target=run).start()


    def on_error(self, ws, error):
        self.logger.error(f"ws error: {error}")
        self.connected = False

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.warning(f"Closed connection {close_status_code} {close_msg}")
        self.connected = False

    def on_open(self, ws):
        self.logger.info("Opened connection")        
        self.connected = True

    def on_timer_tick(self):
        self.logger.debug('tick')
        if self.connected:
            self.ping_pong.ping(self.disconnect)
        else:
            self.reconnect()

    def connect(self, sleep=0):
        time.sleep(sleep)
        self.logger.info(f"Connecting to {self.ws_url}")
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self.request_forwarder = RequestForwarder(self.base_uri, self.ws, self.logger, self.path_whitelist)
        self.ping_pong = PingPonger(self.ws, self.logger)
        self.ws.on_open = self.on_open
        self.ws_thread = threading.Thread(
            target=self.ws.run_forever, kwargs={"skip_utf8_validation": True}
        )
        self.ws_thread.daemon = True
        self.ws_thread.start()

    def reconnect(self):
        self.logger.info("Reconnect...")
        if self.connected:
            self.disconnect()
        self.connect(sleep=1)

    def disconnect(self):
        self.logger.info("Disconnecting...")
        self.connected = False
        self.ws.close()
