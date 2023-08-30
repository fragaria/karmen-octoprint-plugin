from typing import Optional
import time
import logging
import threading
from dataclasses import dataclass
from threading import Timer
import io
import websocket
from .request_forwarder import RequestForwarder, ForwarderMessage, MessageType, BufferMessage

@dataclass
class Config:
    "config for Connector"
    ws_url: str
    "url to websocket-proxy server"
    base_uri: str
    "url to forward requests to"
    path_whitelist: tuple
    "Tuple of possible path beginnings"

class Connector:
    def __init__(self, logger: logging.Logger, sentry, **config):
        self.ws = None
        self.ws_thread = None
        self.should_end = False
        self.request_forwarder = None
        self.connected = False
        self.reconnect_automaticaly = True  # reconnect automatically on disconnection
        self._heartbeat_clock = RepeatedTimer(30, self._on_timer_tick)
        self.logger = logger
        self.sentry = sentry
        self.config: Optional[Config] = None
        self.set_config(config)

    def set_config(self, config):
        self._validate_config(config)
        self.config = Config(**config)

    def on_message(self, ws, message):
        try:
            data = ForwarderMessage(message)
        except Exception as e:
            logging.warning(e)
            self.sentry.captureException(e)
            return
        if data.channel == 'ping-pong':
            try:
                self.ping_pong.handle_request(data)
            except Exception as e:
                logging.warning(e)
                self.sentry.captureException(e)
                return
        else:
            try:
                self.request_forwarder.handle_request(data)
            except Exception as e:
                logging.error(e)
                self.sentry.captureException(e)


    def on_error(self, ws, error):
        self.logger.error(f"ws error: {error}")
        self.sentry.captureException(error)
        self.connected = False

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.info(f"Closed connection {close_status_code} {close_msg}")
        self.connected = False
        if self.reconnect_automaticaly:
            self.connect()

    def on_open(self, ws):
        self.logger.info("Opened connection")
        self.connected = True

    def connect(self):
        self.logger.info(f"Connecting to {self.config.ws_url}")
        self.request_forwarder = RequestForwarder(self.config.base_uri, self.ws, self.logger, self.config.path_whitelist, self.sentry)
        self.ws = self._get_websocket(
            self.config.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self.ws_thread = threading.Thread(
            target=self.ws.run_forever, kwargs={"skip_utf8_validation": True},
            daemon=True,
        )
        self.ws_thread.start()
        self.ping_pong = PingPonger(self.ws, self.logger, self.sentry)
        self.connected = True
        self._heartbeat_clock.start()
        return self.ws

    def _get_websocket(self, *args, **kwargs) -> websocket.WebSocketApp:
        "test injection point"
        return websocket.WebSocketApp(*args, **kwargs)

    def reconnect(self):
        self.logger.info("Reconnect...")
        self.reconnect_automaticaly = True
        if self.connected:
            self._disconnect()

    def disconnect(self):
        self.reconnect_automaticaly = False
        self._disconnect()

    def _disconnect(self):
        if not self.connected:
            self.logger.warning("Requested while not still connected.")
            return
        self.logger.info("Disconnecting...")
        self.ws.close()
        self._heartbeat_clock.stop()

    def _validate_config(self, config):
        "set new config during init and before reconnection"
        if 'path_whitelist' in config:
            assert isinstance(config['path_whitelist'], tuple), \
                f"path_whitelist has to be a tuple (got {type(config['path_whitelist'])})"

    def _on_timer_tick(self):
        print('tick')
        if self.connected:
            self.ping_pong.ping(self.reconnect)


class RepeatedTimer(object):
    "run @function each @interval seconds in a separate thread"

    def __init__(self, interval, tick_callback: callable, *args, **kwargs):
        self._timer_thread = None
        self.tick_callback = tick_callback  # tick callback
        self.interval = interval
        self.args = args
        self.kwargs = kwargs

    def _run(self):
        self.start()
        self.tick_callback(*self.args, **self.kwargs)

    def start(self):
        print('starting heartbeat')
        self._timer_thread = Timer(self.interval, self._run)
        self._timer_thread.daemon = True
        self._timer_thread.start()

    def stop(self):
        self._timer_thread.cancel()
        self._timer_thread = None


class PingPonger:
    def __init__(self, ws, logger, sentry):
        self.logger = logger
        self.ws = ws
        self.gotPong = True
        self.sentry = sentry

    def handle_request(self, message):
        if message.event == "pong":
            self.gotPong = True
            self.logger.debug("Received pong")

    def ping(self, on_close):
        if not self.gotPong:
            self.logger.warning("No pong response received")
            self.sentry.captureMessage("No pong received")
            try:
                on_close()
                self.logger.warning("closing connection")
                return
            except Exception as e:
                self.logger.error("Unable to reconnect", e)
                self.sentry.captureException(e)
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


