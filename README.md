# OctoPrint-Karmen

Securely integrate your OctoPrint with Karmen Cloud in just a few clicks and start managing your prints from anywhere around the globe.

This plugin allows you to integrate your OctoPrint instance with Karmen Cloud—a cloud-based
solution for realtime control and monitoring of your prints.
It will grant you with these new superpowers:

- Securely view & manage your printers realtime from any place around the globe.
- Collaborate with others by using Karmen Cloud workspaces. Share your printers in a secure way. Extremely useful for home, office, schools and many other collaborative environments.
- Display a live video stream of your printers using just a web browser.
- Record timelapse videos easily without any further config.
- View full print history of each of your printers, audit individual prints and their outcomes.
- Never get lost among your print files again: Karmen provides you with a personal Gcode repository with an advanced labeling system.

This plugin will link your OctoPrint instance to the Karmen Cloud using a secure WebSocket channel protected by strong encryption. It will also provide it with elevated permissions so that it will be able to control your printer remotely.
Karmen Cloud will never use your OctoPrint data for anything besides extra features described above. It won't allow other Karmen Cloud users to access your OctoPrint or any related data unless you give them an explicit permission.

## Setup

Install via the bundled [Plugin Manager](https://docs.octoprint.org/en/master/bundledplugins/pluginmanager.html)
or manually using this zip file:

    https://github.com/fragaria/karmen-octoprint-plugin/archive/master.zip

## Configuration

For configuration check [documentation](https://docs.karmen.tech/#/karmen-octo-plugin).

## Secure communication

Security is a key concern to us. Rest assured, your device public address will not be exposed when using this plugin. Rather, communication between Karmen and your Octoprint installation is made using a secure WebSocket channel protected by HTTPS protocol which is used by major organizations like eshops, banks and others.

Karmen also uses key exchange to ensure it speaks to the expected device on the other end. Key set is established during the initial setup.

## Privacy policy

We guarantee that information about you or about your printers will never be sold or even shared to any third party.

### Third party services

Karmen shares basic information with following parties in order to improve our services and website user experience in general:

- Google Analytics - tracks our website visits and provides statistics
- Stripe - payment provider
- Facebook pixel - website visit statistics and info about traffic sources
- Sentry - helps us analyze, track & report errors

### Deleting personal data

Should you decide to delete your Karmen account, all you need to do is to write a deletion request to karmen@karmen.tech. Our admins will delete all information about you, your account, your print files, print history as well as timelapse videos, printers and their configuration.

### Deleting general data

Should you decide to delete files stored under your user account, you can either do so using Karmen Cloud web app or by sending us an inquiry to karmen@karmen.tech

## Confirm you understand what will happen

By connecting your Octoprint installation to Karmen Cloud, you confirm you understand that Karmen Cloud will acquire full control over your device.

## API key security

- Never share your keys with ANYONE.
- Please follow our guidelines and do not use the primary Octoprint key when setting up the Karmen plugin. Rather, create a special key for that purpose as recommended in our tutorial. The full procedure including the recommended way of creating your API key can be found at <https://docs.karmen.tech/#/karmen-octo-plugin>

## Contacts & support

We’ll gladly answer all your questions or comments. Please get in touch at <karmen@karmen.tech>. Thank you for your interest and support!

## Release

To release new version please read: <https://github.com/cp2004/OctoPrint-Knowledge/blob/main/release-channels.md>

- Merge changes to `rc` branch for release candidate and to `main` branch for stable release.

- Change version number in `setup.py`. Use `1.2.3rc1` format for rc version, `1.2.3` for stable version.

- When pushed go to github and create new release
  - For release canditate create new tag - same as version number from previous step.
  - Select right branch (`main`/`rc`)
  - Write version to Release title field.
  - Add some release description.
  - For RC check `This is a pre-release` checkbox.
  - And click `Publish release`.
  - Done. New version should be available in octoprint updates soon.
