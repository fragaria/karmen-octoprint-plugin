/*
 * View model for OctoPrint-Karmen
 *
 * Author: Pavel Vojacek
 * License: MIT
 */
$(function () {
    function KarmenViewModel(parameters) {
        var self = this;
        let performCheck = function () {
            OctoPrint.system.getInfo().then((info) => {
                function versionCompare(v1, v2, options) {
                    var lexicographical = options && options.lexicographical,
                        zeroExtend = options && options.zeroExtend,
                        v1parts = v1.split('.'),
                        v2parts = v2.split('.');

                    function isValidPart(x) {
                        return (lexicographical ? /^\d+[A-Za-z]*$/ : /^\d+$/).test(x);
                    }

                    if (!v1parts.every(isValidPart) || !v2parts.every(isValidPart)) {
                        return NaN;
                    }

                    if (zeroExtend) {
                        while (v1parts.length < v2parts.length) v1parts.push("0");
                        while (v2parts.length < v1parts.length) v2parts.push("0");
                    }

                    if (!lexicographical) {
                        v1parts = v1parts.map(Number);
                        v2parts = v2parts.map(Number);
                    }

                    for (var i = 0; i < v1parts.length; ++i) {
                        if (v2parts.length == i) {
                            return 1;
                        }

                        if (v1parts[i] == v2parts[i]) {
                            continue;
                        }
                        else if (v1parts[i] > v2parts[i]) {
                            return 1;
                        }
                        else {
                            return -1;
                        }
                    }

                    if (v1parts.length != v2parts.length) {
                        return -1;
                    }

                    return 0;
                }

                let version = info.systeminfo["octoprint.version"]
                if (versionCompare(version, "1.8") < 0) {
                    self.notify = new PNotify({
                        text:`This version of Karmen Connector is not supported in Octoprint version ${version}. 
                        Please upgrade to Octoprint 1.8+ or use legacy plugin. 
                        Check plugin settings for details.`,
                        title: "Karmen Connector",
                        type: "error",
                        delay: 10000,
                    });
                }
            });
        }

        self.onUserLoggedIn = function () {
            performCheck();
        }
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: KarmenViewModel,
        dependencies: ["settingsViewModel", "loginStateViewModel"],
        elements: [ /* ... */]
    });
});
