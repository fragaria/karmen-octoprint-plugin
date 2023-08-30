import logging
import pytest
from unittest.mock import MagicMock, patch
import time


from octoprint_karmen.connector import Connector, RepeatedTimer
from octoprint_karmen.utils.tests import WebSockAppMock, wait_until

logger = logging.getLogger(__name__)


def test_creates_websocket(connector: Connector):
    "Connector creates websocket app connection."
    connector.connect()
    ws = connector.ws
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
    connector.disconnect()
    wait_until(lambda :not connector.ws_thread.is_alive())
    assert not connector.ws_thread.is_alive()


def test_reconnect(connector: Connector):
    "reconnect and keep only one connection alive at all times"
    connector.connect()._m_close_delay = 0.1
    thread_a = connector.ws_thread
    # let's give a slight delay between disconnect and 'on_close' event
    connector.reconnect()
    wait_until(lambda :connector.ws_thread != thread_a)
    time.sleep(0.1)
    thread_b = connector.ws_thread
    assert thread_a != thread_b, "New thread started"
    wait_until(lambda :thread_b.is_alive())
    assert thread_b.is_alive(), "New thread has started"
    wait_until(lambda :not thread_a.is_alive())
    assert not thread_a.is_alive(), "Old thread is not alive"


def test_ping_pong_reconnects(connector: Connector):
    "running without ping-pong response triggers recconnect"
    connector._heartbeat_clock = RepeatedTimer(0.1, connector._on_timer_tick)
    connector.connect()
    with patch.object(connector, '_disconnect'):
        time.sleep(0.3)
        assert connector._disconnect.called
    connector.disconnect()


def test_request_forwarded(connector: Connector):
    "starting request calls message forwarder"
    ws: WebSockAppMock = connector.connect()
    with patch.object(connector, 'request_forwarder'):
        ws._m_fake_receive_message('headers', { 'method': 'GET', 'url': '/', 'headers': {} })
        time.sleep(0.1)
        assert connector.request_forwarder.handle_request.called


# ---- FIXTURES ----


@pytest.fixture
def connector():
    @classmethod
    def get_websocket(*args, **kwargs):
        return WebSockAppMock(*args, **kwargs)
    with patch.object(Connector, '_get_websocket', new=get_websocket):
        yield Connector(
            logger=logger,
            sentry=MagicMock(),
            ws_url='server-url',
            base_uri='api-url',
            path_whitelist=('/', ),
        )
