# coding=utf-8
from __future__ import absolute_import

from octoprint.settings import settings
import octoprint.plugin
from octoprint.util.version import is_octoprint_compatible
from .websocket_proxy import Connector
from .utils import SentryWrapper

class KarmenPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.ShutdownPlugin,
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
            "path_whitelist": list(filter(None, self._settings.get(["path_whitelist"]).split(";"))),
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
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._logger.info("Settings saved")
        self.sentry.init_context()
        if self.con:
            self.con.disconnect()
        self.con.reconnect()

    def ws_proxy_connect(self):
        ws_server_url = self._settings.get(["ws_server"])
        key = self._settings.get(["karmen_key"])

        api_url = f"{self.host}:{self.port}"
        url = f"{ws_server_url}/{key}"
        if not key:
            self._logger.warning("No Karmen device key provided.")
            return
        if not self.is_octoprint_compatible:
            self._logger.warning("Incompatible octoprint.")
        self.con = Connector(url, api_url, self._logger, self._settings.get(["path_whitelist"]), self.sentry)
        self.con.connect()

    def ws_proxy_reconnect(self):
        self._logger.info("🍓 Karmen plugin reconnecting...")
        if self.con:
            self.con.disconnect()
        self.ws_proxy_connect()

    def on_startup(self, host, port):
        self.is_octoprint_compatible = is_octoprint_compatible(">1.8")
        self.con = None
        self.host = host
        self.port = port

    def on_after_startup(self):
        self._logger.info("🍓 Karmen plugin is starting...")
        if self._settings.get(["sentry_opt"]) == "in":
            self._logger.info("Karmen sentry is ENABLED")
            self.sentry.init_context()
        self.ws_proxy_connect()

    def on_shutdown(self):
        self._logger.info("🍓 Karmen plugin shutdown...")
        if self.con:
            self.con.disconnect()

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
