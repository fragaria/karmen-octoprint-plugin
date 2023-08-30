import traceback
import json
import logging
from typing import List
from collections import namedtuple
import time

import websocket

from octoprint_karmen.request_forwarder import BufferMessage, MessageType

logger = logging.getLogger(__name__)

FakeEvent = namedtuple('FakeEvent', 'name, args, kwargs')


class WebSockAppMock(websocket.WebSocketApp):

    def __init__(self, *args, **kwargs):
        self._m_sent = []  # sent messages
        self._m_received: List[FakeEvent] = []  # incomming messages
        self._m_errors = []  # exceptions encountered
        self._m_is_running = False  # wether run_forever loop is running
        self._m_run = None  # whether run_forever should keep running
        self._m_close_delay = 0
        super().__init__(*args, **kwargs)

    def send(self, data, opcode=websocket.ABNF.OPCODE_TEXT):
        self._m_sent.insert(0, data)


    def _m_fake_event(self, event, *args, **kwargs):
        "Simulate event executed in run_forever cycle"
        self._m_received.insert(0, FakeEvent(event, args, kwargs))

    def _m_fake_receive_message(self, event, data, channel='/test'):
        data = pack_message(event, data, channel)
        self._m_fake_event('message', data)

    def _close(self, status_code, msg=None, delay = 0):
        logger.debug(f'Closing in {delay}s')
        time.sleep(delay)
        self._m_fake_event('close', status_code, msg)
        self._m_run = False

    def close(self):
        "schedule to close as soon as possible"
        self._m_fake_event('_m__close', 200, 'Connection closed', self._m_close_delay)

    def run_forever(self, *args, **kwargs):
        self._m_run = True
        self._m_is_running = True
        logger.debug('starting')
        try:
            while True:
                logger.debug('>')
                while self._m_received:
                    event = self._m_received.pop()
                    if event.name.startswith('_m_'):  # run ws app method from within loop
                        getattr(self, event.name[3:])(*event.args, **event.kwargs)
                    elif hasattr(self, f'on_{event.name}'):  # pretend an event
                        getattr(self, f'on_{event.name}')(self, *event.args, **event.kwargs)
                time.sleep(0.1)
                if not self._m_run:
                    logger.debug('#')
                    break
        except Exception as error:
            try:
                print(f'\nException: {error}')
                print(''.join(traceback.format_exception(error)))
                self._m_errors.insert(0, error)
                self.on_error(self, error)
            except Exception as err:
                self._m_errors.insert(0, err)
                print(f'\nException from on_error: {err}')
                print(''.join(traceback.format_exception(err)))
            raise
        finally:
            logger.debug('Quitting')
            self._m_is_running = False


def pack_message(event, data, channel='test'):
    "packs message as if it incomes from websocket server"
    assert isinstance(data, dict)
    buf = BufferMessage.pack({
            'channel': channel.encode(),
            'event': event.encode(),
            'dataType': MessageType.OBJECT.value,
            'data': json.dumps(data).encode()
        })
    buf.seek(0)
    return buf.read()


def wait_until(condition, timeout=0.3, interval=0.01, *args):
    start = time.time()
    while not condition(*args) and time.time() - start < timeout:
        time.sleep(interval)


