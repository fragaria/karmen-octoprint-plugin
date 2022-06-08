# coding=utf-8
from __future__ import absolute_import

from octoprint.settings import settings
import octoprint.plugin
from .websocket_proxy import Connector


class KarmenPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.ShutdownPlugin,
):

    ##~~ SettingsPlugin mixin

    def get_settings_defaults(self):
        return {
            "ws_server": "wss://cloud.karmen.tech/ws",
            "karmen_key": "",
        }

    def get_settings_restricted_paths(self):
        return {
            "admin": [],
            "user": [],
            "never": [["ws_server"], ["karmen_key"]],
        }

    def get_template_vars(self):
        return {
            "ws_server": self._settings.get(["ws_server"]),
            "karmen_key": self._settings.get(["karmen_key"]),
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
                # update method: pip
                "pip": "https://github.com/fragaria/karmen-octoprint-plugin/archive/{target_version}.zip",
            }
        }

    def on_settings_save(self, data):
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._logger.info("Settings saved")
        self.ws_proxy_reconnect()

    def ws_proxy_connect(self):
        ws_server_url = self._settings.get(["ws_server"])
        key = self._settings.get(["karmen_key"])

        api_port = settings().get(["server", "port"])
        api_url = f"localhost:{api_port}"
        url = f"{ws_server_url}/{key}"
        if key == "":
            self._logger.info("No Karmen device key provided; Not connecting.")
            return
        self.con = Connector(url, api_url, self._logger)
        self.con.connect()

    def ws_proxy_reconnect(self):
        self._logger.info("🍓 Karmen plugin reconnecting...")
        self.con.disconnect()
        self.ws_proxy_connect()

    def on_after_startup(self):
        self._logger.info("🍓 Karmen plugin is starting...")
        self.ws_proxy_connect()

    def on_shutdown(self):
        self._logger.info("🍓 Karmen plugin shutdown...")
        self.con.disconnect()


__plugin_name__ = "Karmen Connector"
__plugin_pythoncompat__ = ">=3,<4"  # Only Python 3


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = KarmenPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }
