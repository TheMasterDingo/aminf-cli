# aminf

Command-line control for **Angry Miao AM Infinity** mice — the **.97**, the
**.100** and the **.100R2** — over the same vendor HID channel the official
web configurator uses. No driver, no browser.

```
$ aminf status
model          .100R2
connection     dongle
battery         58%
hub battery    100%  charging
profile        1
polling rate   8000 Hz
lift-off dist  1
sensor         motion-sync=True  angle-snap=False  ripple=False
mouse light    on  solid  #FFFFFF
dongle light   off  solid  #FF5900 sat=64 bright=203 speed=12

$ aminf dpi
active stage   4 of 8
dpi button     on
stage 1        400           #FFFFFF
stage 2        800           #FF8000
stage 3        1200          #FFFF00
stage 4      * 1600          #00FF00
...

$ aminf remap forward ctrl+shift+f13
forward -> ctrl+shift+F13
```

Written because the configurator is browser-only, which means the mouse can't
take part in desk automation — turning the lights off with everything else,
switching polling rate or DPI per game, putting battery in a status bar,
giving a button a key the configurator's picker doesn't offer, or saving a
whole profile to a file and putting it back.

## Install

```
pip install hidapi
git clone https://github.com/TheMasterDingo/aminf-cli
cd aminf-cli
python aminf.py status
```

That is all it needs. Two other ways to have it:

* `pip install .` in the cloned folder gives you an `aminf` command.
* `aminf.pyz` from the releases page is the whole tool in one file:
  `python aminf.pyz status`. (Build it yourself with
  `python tools/build_pyz.py`.) `hidapi` is still installed separately.

Since 3.0 the code is a small package (`aminf/`) rather than one file, so
copying `aminf.py` alone no longer works — copy the folder, or use
`aminf.pyz`.

Python 3.9 or newer. The hardware runs below used Python 3.13 on Windows;
compatibility with 3.9 is checked by syntax only.

**Linux** needs udev access to the devices:

```
# /etc/udev/rules.d/70-aminf.rules
# AM Infinity .97
KERNEL=="hidraw*", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0703", MODE="0660", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0880", MODE="0660", TAG+="uaccess"
# AM Infinity .100R2
KERNEL=="hidraw*", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0704", MODE="0660", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0881", MODE="0660", TAG+="uaccess"
# AM Infinity .100
KERNEL=="hidraw*", ATTRS{idVendor}=="3151", ATTRS{idProduct}=="5007", MODE="0660", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="3151", ATTRS{idProduct}=="402a", MODE="0660", TAG+="uaccess"
```

Then `sudo udevadm control --reload && sudo udevadm trigger`.

**Windows** works out of the box. Close the web configurator tab (and AM
Master) first — they hold the device open and `aminf` will tell you so.

## Usage

The script finds whichever mouse is connected on its own. With several
dongles plugged in, every connected mouse gets the command — `aminf all off`
darkens them all. Use `--mouse 97`, `--mouse 100` or `--mouse 100r2` to pick
one.

```
aminf status                      # what the device reports; fast, for status bars
aminf status --full               # also DPI, sleep, debounce and buttons
aminf status --json               # machine-readable (a list, one per mouse)

aminf light off | on | toggle     # dongle lighting
aminf light status
aminf light color '#FF4C00'
aminf light brightness 120        # .97/.100R2: 1-255   .100: 0-4
aminf light speed 12
aminf light effect breathing

aminf mouse-light off | on | toggle
aminf mouse-light status
aminf mouse-light color '#FF823C' # .100: add --charged for the "charged" colour

aminf all off                     # dongle + mouse in one go

aminf rate 4000                   # 125 250 500 1000 2000 4000 8000

aminf dpi                         # the eight stages, which is active, the DPI button
aminf dpi set 3 1600              # stage 3 -> 1600 DPI (stages count from 1)
aminf dpi set 3 1600 --y 800 --color '#00FF00'
aminf dpi stages 4                # how many stages the DPI button cycles through
aminf dpi use 2                   # make stage 2 the active one
aminf dpi button off

aminf remap                       # what every button sends
aminf remap forward F24           # any button -> a key
aminf remap back ctrl+shift+a     # ... with modifiers: ctrl shift alt win
aminf remap wheel-down media:mute # ... a multimedia key
aminf remap middle dpi-cycle      # ... dpi-up, dpi-down, dpi-cycle
aminf remap bottom profile-switch # .100R2: the button underneath
aminf remap forward default       # back to the button's own function
aminf remap back disabled

aminf lod 1                       # lift-off distance; .97/.100: 0-2, .100R2: 0-4
aminf toggle motionsync on        # also: anglesnap ripple fpsmode profilekey
aminf angle 5                     # sensor rotation, -90..90; `aminf angle off`
aminf sleep                       # idle time before the mouse sleeps
aminf sleep 2.4g 300              # seconds, or `never`; also `sleep bt 60`
aminf debounce                    # button debounce
aminf debounce 4                  # all three radios; or `debounce wired|2.4g|bt 4`
aminf profile                     # the active onboard profile
aminf profile 1                   # switch to it

aminf export my-mouse.json        # the whole active profile to a file
aminf import my-mouse.json        # make the mouse match the file
aminf import my-mouse.json --only dpi,buttons --dry-run

aminf raw 12489                   # .97/.100R2: send one *read* command, print the reply
```

Global flags: `--mouse auto|97|100|100r2`, `--device auto|dongle|usb`,
`--json`, `--quiet`, `--dry-run`. Exit codes: `0` ok, `1` no device, `2`
command failed.

`--dry-run` prints the packets instead of sending them. On the .100, writes
that need a read first say which bytes of the block would change.

A command a mouse doesn't have prints a "skipped" notice for that mouse and
changes nothing. A mouse that is asleep behind its dongle is reported once as
"off or out of range"; move it and try again.

### Buttons

`left right middle forward back wheel-up wheel-down`, and `bottom` on the
.100R2. An action is one of:

| | |
|---|---|
| a key | `a`, `f13`, `space`, `enter`, `arrow-left`, or any HID usage `0x04`–`0xE7` |
| a key with modifiers | `ctrl+shift+a`, `win+f13` (the .100 takes at most two) |
| a multimedia key | `media:play stop next prev mute vol-up vol-down player calculator email computer refresh search home brightness-up brightness-down` |
| another button's job | `left right middle forward back wheel-up wheel-down` |
| DPI | `dpi-up dpi-down dpi-cycle` |
| the .100R2's layer switch | `profile-switch` |
| nothing | `disabled` |
| its own function | `default` |

The configurator's key picker stops at F12, but the firmwares accept the full
range, so F13–F24 work from here — handy for keys nothing else on the system
uses. A change that would leave no button on left click is refused. Buttons
bound to a macro or rapid fire are shown as `opaque:…` and never overwritten
unless you name that button yourself.

### Export and import

`aminf export file.json` saves the active profile: sensor, power, DPI,
buttons and both lights, as readable JSON. `aminf import file.json` checks
the whole file first, then writes only what differs from the mouse, section
by section. A section that fails is reported and the others are still
applied; the exit code is `2`.

* Both act on the **active profile** and never switch profile themselves. To
  work on another one, `aminf profile n` first.
* With several mice connected they ask for `--mouse`.
* `import` also reads the web configurator's own export for the .97 and
  .100R2 ("AM Infinity .97 Profile.json"). Macros in such a file are skipped.
* On the .100 only button mappings, DPI and polling rate are stored per
  profile; everything else is one setting for the whole mouse (this is the
  configurator's own notice).

## What works on which mouse

| | .97 | .100R2 | .100 |
|---|---|---|---|
| status, battery, profile | ✓ | ✓ | ✓ |
| dongle light on / off / colour / brightness / speed / effect | ✓ | ✓ | ✓ |
| mouse light on / off | ✓ | ✓ | ✓ (its charge light) |
| mouse light colour | ✓ | ✓ unverified | ✓ charging and charged colours |
| polling rate | ✓ | ✓ | ✓ |
| DPI stages, stage count, active stage, DPI button, stage colours | ✓ | ✓ | ✓ |
| every button → key, media key, mouse function, DPI action, disabled | ✓ | ✓ | ✓ |
| bottom button, `profile-switch` action | — | ✓ | — |
| lift-off distance | 0–2 | 0–4 | 0–2 |
| motion sync, angle snap, ripple, FPS mode, profile hotkey | ✓ | ✓ | ✓ |
| sensor angle | ✓ | ✓ | ✓ |
| sleep times | ✓ | ✓ | ✓ plus `deep_sleep` in export/import |
| debounce | ✓ | ✓ | ✓ |
| hardware debounce | — | shown, read-only | — |
| profile switch | 3 profiles | 3 profiles | 2 profiles |
| export / import | ✓ | ✓ | ✓ |
| `raw` (reads only) | ✓ | ✓ | — |

**DPI goes up to 26000**, in steps of 50, on all three. The configurator's
code allows 30000 on the .97 and .100R2 and the mice even ship with 30000 in
their last stage, but the firmware refuses any write above 26000. `aminf`
tells you so before sending anything. Once a stage that held 30000 has been
changed, 30000 cannot be written back.

The **.100** dongle takes any colour even though the web UI only offers
seven swatches. The **.97 / .100R2** dongle stores hue and saturation instead
of RGB, so a hex colour is converted and may not match exactly.

## How it works

Two protocols. The .97 and the .100R2 speak the same one; the .100 speaks a
completely different one. The script works out which mouse is connected and
uses the right code. Whatever the protocol, each mouse offers the same six
*sections* — sensor, power, DPI, buttons, mouse light, dongle light — and
every command, export and import is written against those.

**Every write is read back.** A change is reported as done only after the
mouse returns the new value. If it doesn't, the command fails with what the
mouse reports instead.

**Writes change only what was asked.** Where the firmware takes a whole block
(lighting, the .100's parameter and DPI blocks), the block is read, the bytes
in question are changed, and the rest goes back exactly as it was read —
including values the configurator's own sliders could not produce.

### .97 and .100R2

Commands ride HID feature report `0x14` on the vendor collection `0xFF13`:

```
[payload_len][device_id] [05][type][len_lo][len_hi][cmd_lo][cmd_hi][data...]
```

* `device_id` — `0x00` addresses whatever you're connected to, `0x80`
  addresses the mouse through the dongle's relay.
* `type` — `0x5a` command, `0x5b` reply, `0x5d` write acknowledgement.
* `len` — command id (2 bytes) plus data length.
* Payload is zero-padded to 61 bytes.

Replies arrive by polling the same feature report. A report can hold nothing,
one reply, several stacked replies, a reply left over from an earlier
command, or part of a long one, so the reader joins the payloads and cuts
whole frames out of the stream until the command id matches.

The .100R2 differs in a few places: an eighth button underneath, lift-off
distance 0–4, read-only hardware debounce, sleep times kept in a
non-volatile record when it is on the cable, and a profile switch that is
sent to both the receiver and the mouse.

### .100

Every packet is 64 bytes in a feature report:

```
[cmd][6 parameter bytes][checksum][...data]
checksum = 255 - (sum of the first 7 bytes & 255)
```

* **Mouse settings** live in a 64-byte parameter block (`211` read / `83`
  write) and the **DPI stages** in another (`212` read / `84` write, per
  profile). Both are read whole, patched and written back whole, and the
  script refuses to write if it could not read all 64 bytes.
* **Key map** is per profile: `208 [profile] [0]` returns four bytes per
  button; a write is `80 [profile] [button] 0 0 0 0 [checksum]` followed by
  the four bytes.
* **Dongle lighting** is an on/off switch (`135` / `7`) and the
  colour/effect packet (`136` / `8`).
* **Over 2.4G**, commands for the mouse are relayed through the dongle with a
  short handshake: open the channel (`F6 05`), wait until the dongle's status
  (`F7`) says it's ready, set the length (`FE 40`), send, wait, and for reads
  fetch the answer (`FC`).
* A busy .100 now and then drops a write that it accepts when repeated. A
  write whose read-back shows the old value is sent once more, as the
  configurator does for its key-map writes; if that is dropped too, the
  command fails.

## Safety

All three mice are behind **allow-lists**: the script holds a fixed list of
commands it may send, and anything else is refused before it reaches the
device. Factory reset, clearing the 2.4G pairing, the pairing handshake,
boot modes and every firmware-update command are not on any list, and
devices in firmware-update mode are ignored. Writes to non-volatile records
are pinned to the few records the tool owns.

This matters because **command ids are not safe to enumerate**. Reads, writes
and destructive operations share one flat id space with nothing in the bytes
distinguishing them — a "read" of an id that happens to be `clearBonded`
invokes `clearBonded`. An earlier version found this the hard way; scanning a
256-id range cost a pairing and a full config. `aminf raw` therefore only
sends ids known to be reads.

Nothing here writes firmware. The worst realistic outcome is a setting you
have to change back — with one exception worth knowing: a DPI stage that
ships at 30000 cannot be set back to 30000 once changed (see above).

## Verified vs. transcribed

Both protocols were read out of the configurator's JS bundle. For 3.0.0 every
write below was then run on real hardware with `tools/hwtest.py`, which
saves the profile, changes one setting at a time, lets the driver confirm it
by read-back, restores the profile and compares. The raw results are in
`docs/hardware/`.

| | .97 dongle | .97 cable | .100R2 dongle | .100R2 cable | .100 dongle | .100 cable |
|---|---|---|---|---|---|---|
| profile switch | ✓ | ✓ | ✓ | ✓ | ✓ | not run |
| polling rate, lift-off, sensor toggles, angle | ✓ | ✓ | ✓ | ✓ | ✓ | not run |
| sleep times, debounce | ✓ | ✓ | ✓ | ✓ | ✓ | not run |
| DPI value, stage count, active stage, button, colour | ✓ | ✓ | ✓ | ✓ | ✓ | not run |
| button remap (key and media key) | ✓ | ✓ | ✓ | ✓ | ✓ | not run |
| mouse light on / off | ✓ | ✓ | ✓ | ✓ | ✓ | not run |
| mouse light colour | ✓ | ✓ | not run | not run | ✓ | not run |
| dongle light on / off, colour, brightness, speed, effect | ✓ | n/a | ✓ | n/a | ✓ | n/a |
| export, then import to restore | ✓ | ✓ | ✓ | ✓ | ✓ | not run |

Not run, and why:

* **.100 on its cable** — not tested yet. The code path is the same as over
  the dongle minus the relay.
* **.100R2 mouse light colour** — `aminf` writes the solid colour the way
  the .97 does. That has not been compared with what the .100R2's own
  configurator page sends, so it is left out of the hardware run.
* Remap actions other than a key and a media key (mouse functions, DPI
  actions, `disabled`, `profile-switch`), and modifiers, are encoded as the
  bundle encodes them and checked by unit tests, not on hardware.
* Importing the web configurator's .97 export is tested against two real
  files, in the unit tests only.

Things the hardware taught that the bundle does not show:

* All three mice refuse DPI above 26000.
* The .100R2 receiver does not answer a read of its own active profile, so a
  profile switch is confirmed by the mouse's answer only.
* The owner's .97 also answers the .100R2-only reads (hardware debounce,
  button 8). `aminf` does not use them on the .97, because the .97's own
  configurator page never does.

## Not implemented

**Macros and rapid fire**, on any mouse. Existing ones are shown and left
alone. **2.4G pairing, factory reset and firmware update** are deliberately
out of reach. **Importing the .100 configurator's own export file** is
recognised and refused with a message.

## Development

```
python -m venv .venv
.venv/bin/pip install hidapi pytest        # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest
```

The tests run without a mouse: the byte-level code is checked against
replies captured from real mice (`tests/fixtures/`), and the drivers run
against simulated mice seeded with those captures.

* `tools/capture.py <97|100r2|100>` records a mouse's replies to the
  configurator's read commands. Read-only.
* `tools/hwtest.py --mouse <97|100r2|100>` is the hardware run described
  above. It changes settings and puts them back; it never touches left or
  right click, and leaves a DPI stage alone if the mouse won't take its own
  value back.
* `tools/split_bundle.py` makes the configurator's minified bundle readable.
  The bundle itself is Angry Miao's code and is not in this repository.

## Changes

**3.0.0** — AM Infinity .100R2 support. On all three mice: DPI (it is back,
per profile and verified), remapping of every button, sleep times, debounce,
sensor angle, FPS mode, profile hotkey, profile switching, `export` /
`import`, `status --full`. The .100 gains lift-off, sensor toggles, dongle
brightness / speed / effect and charge-light colours. Every write is read
back, lighting included. `raw` only sends reads; all three mice are behind
allow-lists. A sleeping mouse is reported once instead of timing out line by
line. The code is now a package with tests; `aminf.py` still starts it.

Changed from 2.2.0: `remap --json` gives plain action strings (`"forward"`
instead of `"forward (default)"`); the "no device" message is reworded;
`--dry-run` with nothing named previews for whichever mouse is plugged in.

**2.2.0** — side-button remap (`aminf remap`) on both mice, F13–F24 included.
DPI removed. .100: protocol checked byte for byte against the configurator
and tested on hardware.

**2.0.0** — AM Infinity .100 support.

## Licence

MIT. Not affiliated with or endorsed by Angry Miao. Use at your own risk.
