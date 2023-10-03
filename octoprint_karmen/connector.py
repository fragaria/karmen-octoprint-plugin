from typing import Optional
import time
import logging
import threading
from dataclasses import dataclass
from threading import Timer
import io
import websocket
from .utils.singleton import Singleton
from .request_forwarder import (
    RequestForwarder,
    ForwarderMessage,
    MessageType,
    BufferMessage
)


@dataclass
class Config:
    "config for Connector"

    ws_url: str
    "url to websocket-proxy server"

    base_uri: str
    "url to forward requests to"

    path_whitelist: tuple
    "Tuple of possible path beginnings"


DISCONNECTED = 'disconnected'
CONNECTING = 'connecting'
CONNECTED = 'connected'
DISCONNECTING = 'disconnecting'


class Connector(metaclass=Singleton):

    def __init__(self, logger: logging.Logger, sentry, **config):
        self._state = DISCONNECTED
        self._timeout = 3
        self.reconnect_delay_sec = 3  # reconnect automatically on disconnection
        self.ws = None
        self.ws_thread = None
        self.should_end = False
        self.request_forwarder = None
        self._reconnection_timer: Optional[Timer] = None  # reconnection timer (None if reconnection is not scheduled)
        self._reconnection_timer_lock = threading.Lock()
        self.auto_reconnect = True
        self._heartbeat_clock = RepeatedTimer(self._timeout*3, logger, self._on_timer_tick)
        self.logger = logger
        self.sentry = sentry
        self.config: Optional[Config] = None
        self.set_config(config)
        self._on_close_watchdog = OnCloseTimer(
            self._timeout*1,
            lambda :self.on_close(self, -1, 'Watchdog!'),
            logger
        )

        # TODO:
        # Condition to wait for the right state
        self.state_condition: threading.Condition = threading.Condition()
        # acquired by on_close and released by connect
        self.disconnected_lock = threading.Lock()

    @property
    def state(self):
        return self._state

    def set_state(self, new_state):
        self._state = new_state
        self.logger.debug('Setting state to %s', self.state)
        self.state_condition.notify()

    def wait_for_state(self, *states):
        self.logger.debug("... waiting for %s state(s) (current: %s)", states, self.state)
        self.state_condition.wait_for(lambda: self.state in states, 0.1)
        if self.state not in states:
            raise InvalidStateException(f"Timeout waiting for {states!r} state(s) (currently '{self.state}'.")

    @property
    def connected(self):
        return self.state in (CONNECTED, DISCONNECTING)

    def set_config(self, config):
        """set / update config

        It is necessary to reconnect in order the config changes to take effect
        """
        self._validate_config(config)
        self.config = Config(**config)

    def on_message(self, ws, message):
        "process message"
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
        """process error event from underlaying websocket

        The on_close is called just after on_error
        """
        self.logger.exception(error)
        self.sentry.captureException(error)

    def on_close(self, ws, close_status_code=None, close_msg=None):
        self.logger.debug(f'waiting for "disconnecting" state (current state: {self.state})')
        with self.state_condition:
            self.wait_for_state(DISCONNECTING)
            self.logger.info(f"Connection closed {close_status_code or 'no status code'} {close_msg or 'no message'}")
            # TODO: is this still necessary after state_condition was implemented
            if not self._on_close_watchdog.cancel():
                self.logger.debug("on_close watchdog not running")
            if close_status_code == -1 and close_msg == 'Watchdog!':
                # called from watchdog
                if self.ws:
                    self.ws.close()
            if self._heartbeat_clock.is_running:
                self._heartbeat_clock.stop()
            self.logger.debug('... clearing ws_thread %r', self.ws_thread)
            self.ws_thread = None
            self.set_state(DISCONNECTED)
        self._try_auto_reconnect()

    def _try_auto_reconnect(self):
        if self.auto_reconnect:
            if self.reconnect_delay_sec:
                self.logger.info(f"Reconnecting in {self.reconnect_delay_sec} seconds")
                with self._reconnection_timer_lock:
                    if self._reconnection_timer is None:
                        self._reconnection_timer = Timer(self.reconnect_delay_sec, self.connect)
                        self._reconnection_timer.start()
            else:
                self.logger.info("Reconnecting")
                self.connect()

    def on_open(self, ws):
        with self.state_condition:
            self.wait_for_state(CONNECTING)
            self.logger.info("Connected")
            self._heartbeat_clock.start()
            self.set_state(CONNECTED)

    def connect(self):
        self.logger.info(f"Connecting to {self.config.ws_url}")
        with self.state_condition:
            self.wait_for_state(DISCONNECTED)
            self.logger.debug("... connecting ...")
            with self._reconnection_timer_lock:
                if self._reconnection_timer:
                    self._reconnection_timer.cancel()
                    self._reconnection_timer = None
            self.ws = self._get_websocket(
                self.config.ws_url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
            )
            self.request_forwarder = RequestForwarder(self.config.base_uri, self.ws, self.logger, self.config.path_whitelist, self.sentry)
            self.logger.debug('... creating ws thread (was %r).', self.ws_thread)
            self.ws_thread = threading.Thread(
                target=self.ws.run_forever, kwargs={
                    "skip_utf8_validation": True,
                },
                daemon=True,
            )
            self.logger.debug('... starting websocket thread %r.', self.ws_thread)
            self.set_state(CONNECTING)
            self.ws_thread.start()
            self.ws.sock.settimeout(self._timeout)
            self.ping_pong = PingPonger(self.ws, self.logger, self.sentry)
            return self.ws

    def _get_websocket(self, *args, **kwargs) -> websocket.WebSocketApp:
        "test injection point"
        return websocket.WebSocketApp(*args, **kwargs)

    def reconnect(self):
        self.logger.info("Reconnecting")
        self.auto_reconnect = True
        if self.connected:
            self._disconnect()

    def disconnect(self):
        self.auto_reconnect = False
        self._disconnect()

    def _disconnect(self):
        self.logger.info("Disconnecting")
        with self.state_condition:
            if self.state in (DISCONNECTED, DISCONNECTING):
                self.logger.warning(f'Trying to disconnect while in "{self.state}" state.')
                return
            self.logger.debug(f'Closing socket {self.ws}')
            self.ws.close()
            self.set_state(DISCONNECTING)
            self.logger.debug(f'... starting on_close watchdog')
            self._on_close_watchdog.start()

            self.logger.debug('Stopping heartbeat clock.')
            if self._heartbeat_clock.is_running:
                self._heartbeat_clock.stop()

    def _validate_config(self, config):
        "set new config during init and before reconnection"
        if 'path_whitelist' in config:
            assert isinstance(config['path_whitelist'], tuple), \
                f"path_whitelist has to be a tuple (got {type(config['path_whitelist'])})"

    def _on_timer_tick(self):
        if self.connected:
            self.ping_pong.ping(self.reconnect)


class RepeatedTimer(object):
    "run @function each @interval seconds in a separate thread"

    def __init__(self, interval, logger: logging.Logger, tick_callback: callable, *args, **kwargs):
        self._timer_thread = None
        self.tick_callback = tick_callback  # tick callback
        self.interval = interval
        self.args = args
        self.kwargs = kwargs

    def _run(self):
        self.start()
        try:
            self.tick_callback(*self.args, **self.kwargs)
        except Exception as error:
            self.logger.exception(error)

    @property
    def is_running(self):
        return self._timer_thread

    def start(self):
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
                self.logger.warning("closing connection")
                on_close()
                return
            except Exception as e:
                self.logger.error("Unable to reconnect %s", e)
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


class OnCloseTimer:
    """Implement threading timer with threading lock

    I haven't used bare Timer becuse I wanted to move thread-safe agenda around
    it to a separate piece of code.
    """

    def __init__(self, timeout_secs, on_close, logger):
        self._timer = None
        self._timeout_secs = timeout_secs
        self._on_close = on_close
        self.lock = threading.Lock()
        self._logger = logger

    def start(self):
        with self.lock:
            assert self._timer is None
            self._timer = Timer(
                self._timeout_secs, self._alarm
            )
            self._timer.start()

    def cancel(self):
        with self.lock:
            if self.running:
                self._logger.debug("Cancelling on_close timer.")
                self._timer.cancel()
                self._timer = None
                return True
            else:
                self._logger.debug("on_close timer not running.")
                return False

    @property
    def running(self):
        return self._timer is not None and self._timer.is_alive()

    def _alarm(self):
        with self.lock:
            self._timer = None
        try:
            self._logger.warning("on_close not called on time, watchdog acts.")
            self._on_close()
        except Exception as error:
            self._logger.exception(error)

class InvalidStateException(TimeoutError):
    "Indicates invalid state for an operation"
