from typing import Optional
import time
import logging
import threading
from dataclasses import dataclass
from threading import Timer
import io
import websocket
import atexit
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

    auto_reconnect: bool = True
    "reconnect upon lost connection (disabled by calling `disconnect()`"

    reconnect_delay_sec: int = 3
    "reconnection delay if `auto_reconnect` enabled (seconds)"




DISCONNECTED = 'disconnected'
CONNECTING = 'connecting'
CONNECTED = 'connected'
DISCONNECTING = 'disconnecting'


class Connector(metaclass=Singleton):

    def __init__(self, logger: logging.Logger, sentry, **config):
        #
        self.__state = DISCONNECTED
        self._timeout = 3
        # private
        self.ws = None
        self.ws_thread = None
        self.request_forwarder = None
        self.on_state_change = None  # hook to notify about state changes
        self._reconnection_timer: Optional[Timer] = None  # reconnection timer (None if reconnection is not scheduled)
        # TODO: remove or move to timer
        self._reconnection_timer_lock = threading.Lock()
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
        self.last_error = None

        # TODO:
        # Condition to wait for the right state
        self.state_condition: threading.Condition = threading.Condition()
        # acquired by on_close and released by connect
        self.disconnected_lock = threading.Lock()
        logger.debug(f'Registering on_exit')

    @property
    def state(self):
        return self.__state

    def set_state(self, new_state):
        self.__state = new_state
        self.logger.debug('Setting state to %s', self.state)
        self.state_condition.notify()
        self.on_state_change()

    def wait_for_state(self, *states, timeout=0.1):
        self.logger.debug("... waiting for %s state(s) (current: %s)", states, self.state)
        self.state_condition.wait_for(lambda: self.state in states, timeout=timeout)
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

    def _try_auto_reconnect(self):
        if self._auto_reconnect:
            if self.config.reconnect_delay_sec:
                self.logger.info(f"Reconnecting in {self.config.reconnect_delay_sec} seconds")
                with self._reconnection_timer_lock:
                    if self._reconnection_timer is None:
                        self._reconnection_timer = Timer(self.config.reconnect_delay_sec, self.connect)
                        self._reconnection_timer.start()
            else:
                self.logger.info("Reconnecting")
                self.connect()

    def connect(self):
        self._auto_reconnect = self.config.auto_reconnect
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
                name='WS Thread',
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
            try:
                self.wait_for_state(CONNECTED, timeout=1)
            except InvalidStateException:
                pass
            return self.ws

    def _get_websocket(self, *args, **kwargs) -> websocket.WebSocketApp:
        "test injection point"
        return websocket.WebSocketApp(*args, **kwargs)

    def reconnect(self):
        self.logger.info("Reconnecting")
        self._auto_reconnect = True
        if self.connected:
            self._disconnect()

    def disconnect(self):
        self._auto_reconnect = False
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
            self.logger.debug('... starting on_close watchdog')
            self._on_close_watchdog.start()

    def _validate_config(self, config):
        "set new config during init and before reconnection"
        if 'path_whitelist' in config:
            assert isinstance(config['path_whitelist'], tuple), \
                f"path_whitelist has to be a tuple (got {type(config['path_whitelist'])})"

    def _on_timer_tick(self):
        if self.connected:
            self.ping_pong.ping(self.reconnect)
        if self.on_state_change:
            self.on_state_change()

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


    def on_error(self, ws, error: Exception):
        """process error event from underlaying websocket

        The on_close is called just after on_error
        """
        self.logger.debug('on_error called')
        self.logger.exception(error)
        self.sentry.captureException(error)
        self.last_error = error
        with self.state_condition:
            self.set_state(DISCONNECTING)

    def on_close(self, ws, close_status_code=None, close_msg=None):
        self.logger.debug('on_close called')
        if ws != self.ws:
            self.logger.warning(f"`on_close` called from an old websocket")
        self.logger.debug(f'waiting for "disconnecting" state (current state: {self.state})')
        with self.state_condition:
            self.wait_for_state(CONNECTED, DISCONNECTING)
            self.logger.info(f"Connection closed {close_status_code or 'no status code'} {close_msg or 'no message'}")
            if not self._on_close_watchdog.cancel():
                self.logger.debug("on_close watchdog not running")
            if close_status_code == -1 and close_msg == 'Watchdog!':
                # called from watchdog
                if self.ws:
                    self.ws.close()
                    self.ws.on_close = None
            self._heartbeat_clock.stop()
            self.logger.debug('... clearing ws_thread %r', self.ws_thread)
            self.ws_thread = None
            self.set_state(DISCONNECTED)
        self._try_auto_reconnect()

    def on_open(self, ws):
        self.last_error = None
        self.logger.debug(f"On_open with thred {self.ws_thread}")
        with self.state_condition:
            try:
                self.wait_for_state(CONNECTING)
                self.logger.info("Connected")
                self._heartbeat_clock.start()
                self.set_state(CONNECTED)
            except InvalidStateException:  # not in connecting state
                self.logger.warning(f"on_open received while in {self.state} state (expecting {CONNECTING}).")

    def on_exit(self):
        if threading.main_thread().ident != threading.get_ident():
            # not running in main thread
            raise RuntimeError("On exit should be run from main thread.")
        if self.state not in (DISCONNECTED, DISCONNECTING):
            self.disconnect()

        start_time = time.time()
        while self.state != DISCONNECTED and time.time() - start_time < 3:
            time.sleep(0.1)
        if self.ws_thread:
            self.ws_thread.join()
        if self._heartbeat_clock:
            self._heartbeat_clock.stop()


class RepeatedTimer:
    "run @function each @interval seconds in a separate thread"

    def __init__(self, interval, logger: logging.Logger, tick_callback: callable, *args, **kwargs):
        self._timer_thread = None
        self.tick_callback = tick_callback  # tick callback
        self.interval = interval
        self.args = args
        self.kwargs = kwargs
        self.logger = logger

    def _run(self):
        try:
            self.tick_callback(*self.args, **self.kwargs)
        except Exception as error:
            self.logger.exception(error)
        finally:
            self.start()

    @property
    def is_running(self):
        return self._timer_thread

    def start(self):
        self._timer_thread = Timer(self.interval, self._run)
        self._timer_thread.name = 'repeated thread'
        self._timer_thread.daemon = True
        self._timer_thread.start()

    def stop(self):
        if self.is_running:
            self._timer_thread.cancel()
            try:
                self._timer_thread.join()
            except RuntimeError:
                "Either started from this thread or not started at all."
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
            try:
                self.ws.send(buf.read(), websocket.ABNF.OPCODE_BINARY)
            except websocket.WebSocketException as error:
                self.logger.info(f"Websocket exception {error} during ping. Closing connection.")
                on_close()

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

def cleanup():
    connector = Singleton._instances[Connector]
    connector.on_exit()
atexit.register(cleanup)
