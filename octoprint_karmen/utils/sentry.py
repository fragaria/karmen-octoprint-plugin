import logging
import sentry_sdk
from sentry_sdk.integrations.threading import ThreadingIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
import requests

class SentryWrapper:

    def __init__(self, plugin):
        def before_send(event, hint):
            if 'exc_info' in hint:
                exc_type, exc_value, tb = hint['exc_info']
                if isinstance(exc_value, requests.exceptions.RequestException):
                    event['fingerprint'] = ['database-unavailable']
            return event

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

    def init_context(self):
        if self.enabled():
            sentry_sdk.set_user({'id': self.plugin.key()})

    def captureException(self, *args, **kwargs):
        if self.enabled():
            sentry_sdk.capture_exception(*args, **kwargs)

    def captureMessage(self, *args, **kwargs):
        if self.enabled():
            sentry_sdk.capture_message(*args, **kwargs)
