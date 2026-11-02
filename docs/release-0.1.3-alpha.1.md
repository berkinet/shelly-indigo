Adds discovered Shelly channel selection to Indigo's New Device and Edit Device
dialogs. The previous alpha exposed read-only details without a selection menu.

- Switch and binary-input dialogs now have a type-filtered Shelly channel dropdown.
- Selecting a channel fills its ID, channel number, host, model and generation.
- A refresh button reloads the list while the dialog is open.
- Already-configured channels show their Indigo device name; saving duplicate
  bindings is rejected with a message identifying the existing device.
- Existing devices have their channels preselected and remain editable while
  offline or before startup discovery finishes.
- Automatic creation remains enabled by default. Uncheck Create Indigo devices
  in plugin preferences and restart for manual channel selection and creation.
  Existing devices continue receiving updates in either mode.
- The password label now clarifies that unprotected devices need no password and
  that this is a local device admin password, not the Shelly cloud account login.

Validation: automated tests exercise XML-to-Python callback wiring, filtered
lists, manual creation and updates, duplicate/invalid selection rejection,
offline editing and DHCP address changes. Source and release archive startup
checks and the existing WebSocket integration suite pass. The new dialog has not
yet been confirmed visually in the actual Indigo client.

Requires Indigo 2026.1. Supported channel types remain switches and binary inputs;
dimmers/lights, Gen1 and other component types are planned.
