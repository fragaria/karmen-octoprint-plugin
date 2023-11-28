import logging
import sentry_sdk
import traceback
from contextlib import contextmanager
from sentry_sdk.integrations.threading import ThreadingIntegration
from sentry_sdk.integrations.logging import LoggingIntegration


class SentryWrapper:

    def __init__(self, plugin):

        def before_send(event, hint):
            if not self.enabled():
                return None
            if 'exc_info' in hint:
                exc_type, exc_value, tb = hint['exc_info']
                # exclude exceptions which does not contain /octoprint_karmen/
                # in list of source files in traceback
                if any(filter(lambda frame: '/octoprint_karmen/' in frame[0], traceback.StackSummary.extract(traceback.walk_tb(tb), limit=1000))):
                    return event
            return None

        self.plugin = plugin
        sentry_sdk.init(
            dsn='https://8bb9b2b583f24687af86177f96ef6077@fraga-sentry2.f1.f-app.it/18',
            default_integrations=False,
            integrations=[
                ThreadingIntegration(propagate_hub=True),
                LoggingIntegration(
                    level=logging.INFO,
                    event_level=None
                ),
            ],
            before_send=before_send,
            send_default_pii=True,
            release=plugin._plugin_version,
        )

    def enabled(self):
        return self.plugin._settings.get(["sentry_opt"]) != 'out'


@contextmanager
def capture_exception(reraise=True, on_error=None):
    try:
        yield
    except Exception as error:
        if on_error:
            on_error(error)
        sentry_sdk.capture_exception(error)
        if reraise:
            raise
