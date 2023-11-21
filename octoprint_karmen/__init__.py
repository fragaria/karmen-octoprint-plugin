# coding=utf-8
from __future__ import absolute_import

import flask
from websocket import WebSocketBadStatusException, WebSocketTimeoutException
from octoprint.settings import settings
import octoprint.plugin
from octoprint.util.version import is_octoprint_compatible
from .connector import Connector
from .utils import SentryWrapper, parse_path_whitelist, get_ip

class KarmenPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.ShutdownPlugin,
    octoprint.plugin.SimpleApiPlugin,
):

    ##~~ SettingsPlugin mixin

    def get_settings_defaults(self):
        self.sentry = SentryWrapper(self)
        return {
            "ws_server": "wss://cloud.karmen.tech/ws",
            "karmen_key": "",
            "path_whitelist": "/api/",
            "sentry_opt": "out",
        }

    def get_settings_restricted_paths(self):
        return {
            "admin": [["ws_server"], ["karmen_key"], ["path_whitelist"], ["sentry_opt"]],
        }

    def get_template_vars(self):
        host = 'localhost' if self.host == '::' else self.host
        key = self._settings.get(["karmen_key"])
        if (key and len(key) <= 4):
            key_redacted = key
        else:
            key_redacted = (key[:2] + "*" * (len(key) - 4) + key[-2:]) if key else None
        return {
            "is_octoprint_compatible": self.is_octoprint_compatible,
            "ws_server": self._settings.get(["ws_server"]),
            "path_whitelist": list(parse_path_whitelist(self._settings.get(["path_whitelist"]))),
            "api_port": self.port,
            "api_host": host,
            "karmen_key_redacted": key_redacted,
            "snapshot_url": settings().get(["webcam", "snapshot"]),
            "sentry_opt": settings().get(["sentry_opt"]),
        }

    def get_template_configs(self):
        return [dict(type="settings", custom_bindings=False)]

    ##~~ AssetPlugin mixin

    def get_assets(self):
        return {
            "js": ["js/karmen.js"],
            "css": ["css/karmen.css"],
            "less": ["less/karmen.less"],
        }

    ##~~ Softwareupdate hook

    def get_update_information(self):
        # Define the configuration for your plugin to use with the Software Update
        # Plugin here. See https://docs.octoprint.org/en/master/bundledplugins/softwareupdate.html
        # for details.
        return {
            "karmen": {
                "displayName": "Karmen Connector",
                "displayVersion": self._plugin_version,
                # version check: github repository
                "type": "github_release",
                "user": "fragaria",
                "repo": "karmen-octoprint-plugin",
                "current": self._plugin_version,
                "prerelease": True,
                # update method: pip
                "pip": "https://github.com/fragaria/karmen-octoprint-plugin/archive/{target_version}.zip",
                "stable_branch": {
                    "name": "Stable",
                    "branch": "main",
                    "comittish": ["main"],
                },
                "prerelease_branches": [
                    {
                        "name": "Release Candidate",
                        "branch": "rc",
                        "comittish": ["rc", "main"],
                    }
                ]
            }
        }

    def on_settings_save(self, data):
        "update settings and reconnect"
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._logger.info("Settings saved")
        self.ws_proxy_reconnect()


    def get_api_commands(self):
        return {}

    def on_api_get(self, request):
        'update status'
        if 'update_status' in request.args:
            self.send_status_message()
        return flask.jsonify(**self.get_status('with_ip_address' in request.args))

    def ws_proxy_reconnect(self):
        "reload settings and reconnect"
        self._logger.info("🍓 Karmen plugin reconnecting...")
        if not self._connector:
            self.ws_get_connector()
        else:
            self._connector.set_config(self.ws_get_connector_config())
        if self._connector:
            self._connector.reconnect()

    def ws_get_connector_config(self):
        "return connector or None if settings are not applicable"
        self.sentry.init_context()
        ws_server_url = self._settings.get(["ws_server"])
        key = self._settings.get(["karmen_key"])

        forward_to_url = f"{self.host}:{self.port}"
        ws_server_url = f"{ws_server_url}/{key}"
        if not key:
            raise ValueError("No Karmen device key provided.")
        if not self.is_octoprint_compatible:
            self._logger.warning("Incompatible octoprint.")
        return {
            'ws_url': ws_server_url,
            'base_uri': forward_to_url,
            'path_whitelist': parse_path_whitelist(self._settings.get(["path_whitelist"])),
        }

    def get_status(self, with_ip=False):
        "format status"
        if self._connector:
            error = self._connector.last_error
            advise = 'Try to restart Octoprint'
            advise_check_internet = '<strong>Check internet connection.</strong>'
            if isinstance(error, WebSocketBadStatusException) and error.status_code == 401:
                error = 'Unauthorized'
                advise = 'Check your device key in karmen plugin settings.'
            elif isinstance(error, ConnectionRefusedError):
                error = 'Connection refused'
                advise = advise_check_internet
            elif isinstance(error, WebSocketTimeoutException):
                error = f'No response from internet'
                advise = advise_check_internet
            elif error:
                error = f'<pre>{error}</pre>'
            response_payload = {
                'connectionStatus': self._connector.state,
                'error': error,
                'advise': advise if error else None,
            }
        else:
            response_payload = {"connectionStatus": "loading", "error": None, "advise": None}
        if with_ip:
            response_payload['ipAddress'] = get_ip()
        return response_payload

    def send_status_message(self):
        "publishes connection changes to frontend"
        self._plugin_manager.send_plugin_message(self._identifier, self.get_status())

    def ws_get_connector(self):
        "return connector or None if settings are not applicable"
        ws_server_url = self._settings.get(["ws_server"])
        key = self._settings.get(["karmen_key"])

        forward_to_url = f"{self.host}:{self.port}"
        ws_server_url = f"{ws_server_url}/{key}"
        if not key:
            self._logger.warning("No Karmen device key provided.")
            return
        if not self.is_octoprint_compatible:
            self._logger.warning("Incompatible octoprint.")
        try:
            connector_config = self.ws_get_connector_config()
        except ValueError as error:
            self._logger.error(error)
            self._connector = None
        else:
            self._connector = Connector(self._logger, self.sentry, **connector_config)
            self._connector.on_state_change = self.send_status_message
        return self._connector

    def on_startup(self, host, port):
        self.is_octoprint_compatible = is_octoprint_compatible(">1.8")
        self._connector = None
        self.host = host
        self.port = port

    def on_after_startup(self):
        self.send_status_message()
        self._logger.info("🍓 Karmen plugin is starting...")
        if self.ws_get_connector():
            self._connector.connect()

    def on_shutdown(self):
        self._logger.info("🍓 Karmen plugin shutdown...")
        if self._connector:
            self._connector.disconnect()

    def key(self):
        return self._settings.get(["karmen_key"])


__plugin_name__ = "Karmen Connector"
__plugin_pythoncompat__ = ">=3,<4"  # Only Python 3


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = KarmenPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }
