import logging
from logging.config import dictConfig
import pytest
from unittest.mock import MagicMock, patch
import time


from octoprint_karmen.connector import Connector, Config, RepeatedTimer, InvalidStateException, DISCONNECTED
from octoprint_karmen.utils.tests import WebSockAppMock, wait_until

logger = logging.getLogger(__name__)


def test_creates_websocket(connector: Connector):
    "Connector creates websocket app connection."
    connector.connect()
    ws = connector.ws
    assert not ws._m_errors
    assert ws._m_is_running
    assert connector.ws_thread.is_alive()
    connector.disconnect()
    wait_until(lambda: not ws._m_is_running)
    assert not ws._m_errors


def test_cleans_all_threads(connector: Connector):
    "thread is closed on disconnect"
    assert connector.ws_thread is None
    ws = connector.connect()
    assert ws._m_is_running, "WS loop is running."
    assert connector.ws_thread.is_alive(), "WS thread is running"
    ws_thread = connector.ws_thread
    connector.disconnect()
    wait_until(lambda :not ws_thread.is_alive())
    assert not ws_thread.is_alive(), f"Thread {ws_thread} is still alive."


def test_reconnect(connector: Connector):
    "reconnect and keep only one connection alive at all times"
    connector.connect()._m_close_delay = 0.01
    connector.config.reconnect_delay_sec = 0.01
    thread_a = connector.ws_thread
    # let's give a slight delay between disconnect and 'on_close' event
    time.sleep(0.03)
    connector.reconnect()
    wait_until(lambda :connector.ws_thread != thread_a)
    time.sleep(0.01)
    thread_b = connector.ws_thread
    assert thread_a != thread_b, "New thread started"
    wait_until(lambda :thread_b.is_alive())
    assert thread_b.is_alive(), "New thread has started"
    wait_until(lambda :not thread_a.is_alive())
    assert not thread_a.is_alive(), "Old thread is not alive"
    connector.disconnect()


def test_ping_pong_reconnects(connector: Connector):
    "running without ping-pong response triggers recconnect"
    with patch.object(connector, '_heartbeat_clock', RepeatedTimer(0.01, logger, connector._on_timer_tick)):
        connector.connect()
        with patch.object(connector, '_disconnect'):
            time.sleep(0.03)
            assert connector._disconnect.called
        connector.disconnect()


def test_request_forwarded(connector: Connector):
    "starting request calls message forwarder"
    ws: WebSockAppMock = connector.connect()
    with patch.object(connector, 'request_forwarder'):
        ws._m_fake_receive_message('headers', { 'method': 'GET', 'url': '/', 'headers': {} })
        time.sleep(0.02)
        assert connector.request_forwarder.handle_request.called
    connector.disconnect()

def test_on_close_watchdog(connector: Connector):
    connector._on_close_watchdog._timeout_secs = 0.01 # start imediately
    ws: WebSockAppMock = connector.connect()
    ws._m_close_delay = 0.03
    connector._disconnect()
    connector._auto_reconnect = True
    assert connector._on_close_watchdog.running
    with patch.object(connector, 'on_close') as on_close:
        time.sleep(0.02)
        assert on_close.called
    connector.disconnect()


def test_no_connect_before_on_close(connector: Connector):
    # wait 30ms before force-closing connection
    ws: WebSockAppMock = connector.connect()
    connector._on_close_watchdog._timeout_secs = 0.3 # start imediately
    ws._m_close_delay = 0.2
    connector.disconnect()
    with patch.object(connector, 'on_close') as on_close:
        with pytest.raises(InvalidStateException):
            d('e')
            connector._timeout = 0.1
            connector.connect()
            connector._timeout = 3
            assert not on_close.called
    wait_until(lambda :on_close.called, timeout=0.5)
    connector.connect()
    connector.disconnect()

# ---- FIXTURES ----


@pytest.fixture
def connector():
    @classmethod
    def get_websocket(*args, **kwargs):
        return WebSockAppMock(*args, **kwargs)
    with patch.object(Connector, '_get_websocket', new=get_websocket):
        config = {
            'ws_url': 'server-url',
            'base_uri': 'api-url',
            'path_whitelist': ('/', ),
            'reconnect_delay_sec': 1,
            'auto_reconnect': False,
        }
        connector = Connector(
            logger=logger,
            sentry=MagicMock(),
            **config
        )
        connector.set_config(config)
        yield connector
        if connector.state != DISCONNECTED:
            connector.disconnect()
        wait_until(lambda : connector.state == DISCONNECTED, 10)
        assert connector.state == DISCONNECTED


dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    # uncomment to display more information on console set:
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
    'loggers': {
        'octoprint_karmen.test_connector': {
            'level': 'DEBUG',
        },
    }

})
