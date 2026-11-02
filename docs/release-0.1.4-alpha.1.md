Adds physical momentary-button monitoring, including the 1PM Mini Gen4.

Use the separate Indigo input device (input:0): it becomes ON on button press
and OFF on release. The switch:0 device continues representing the relay output.
The plugin does not change Shelly input/relay settings or issue relay commands
while monitoring the button.

Button-mode inputs are discovered using read-only Input.GetConfig calls because
Shelly reports their polled state as null. The plugin now processes btn_down and
btn_up notifications. Input dialogs use the name Shelly Input and include both
binary and button channels. Automatic/manual creation preferences still apply.

The input state is unknown after startup or a connection loss until a physical
press or release is received. The stateKnown field is false and Indigo shows
Waiting for input press/release. Null polling does not overwrite a known held
state; there is no fabricated release timer. Button state depends on receiving
both transitions, which cannot be reconstructed by polling if an event is lost.

Validation: read-only queries confirmed button mode and recognition on the
owner's actual 1PM Mini Gen4. Automated tests cover press/release transitions,
quick press+release in one frame, interleaved startup events, stale-event rejection,
null polling, unknown state after disconnect/reconnect, and unchanged relay state.
Source and release bundles pass isolated startup checks. Physical button presses
through the installed Indigo plugin still need confirmation.

Requires Indigo 2026.1. Install the attached plugin package to replace the
previous alpha; select or monitor the Shelly Input device rather than its relay.
