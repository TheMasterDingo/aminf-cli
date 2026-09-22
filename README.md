# aminf

Command-line control for **Angry Miao AM Infinity** mice — the **.97** and
the **.100** — over the same vendor HID channel the official web configurator
uses. No driver, no browser.

```
$ aminf status
--- .97
model          .97
connection     dongle
battery         94%
hub battery    100%  charging
profile        0
polling rate   4000 Hz
lift-off dist  0
sensor         motion-sync=True  angle-snap=False  ripple=False
mouse light    on  solid  #FF823C
dongle light   on  solid  #FF4C00 sat=75 bright=120 speed=12
dpi            stage 0 of 1
  * [0] 1800
--- .100
model          .100
connection     dongle
battery         63%
hub battery     99%
polling rate   4000 Hz
mouse light    on  #FF823C
dongle light   on  solid  #FFB955  bright=4/4
dpi            stage 0 of 1
  * [0] 1800         #FFFFFF
```

Written because the configurator is browser-only, which means the mouse can't
take part in desk automation — turning the lights off with everything else,
switching polling rate per game, putting battery in a status bar.

## Install

```
pip install hidapi
git clone https://github.com/TheMasterDingo/aminf-cli
cd aminf-cli
python aminf.py status
```

Single file, no packaging needed. Drop `aminf.py` wherever is convenient.

**Linux** needs udev access to the devices:

```
# /etc/udev/rules.d/70-aminf.rules
# AM Infinity .97
KERNEL=="hidraw*", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0703", MODE="0660", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0880", MODE="0660", TAG+="uaccess"
# AM Infinity .100
KERNEL=="hidraw*", ATTRS{idVendor}=="3151", ATTRS{idProduct}=="5007", MODE="0660", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="3151", ATTRS{idProduct}=="402a", MODE="0660", TAG+="uaccess"
```

Then `sudo udevadm control --reload && sudo udevadm trigger`.

**Windows** works out of the box. Close the web configurator tab (and AM
Master) first — they hold the device open and `aminf` will tell you so.

## Usage

Every connected mouse gets the command. With both dongles plugged in,
`aminf all off` darkens both. Use `--mouse 97` or `--mouse 100` to pick one.

```
aminf status                      # everything the device reports
aminf status --json               # same, machine-readable (a list, one per mouse)

aminf light off | on | toggle     # dongle lighting
aminf light status
aminf light color '#FF4C00'
aminf light brightness 120        # .97 only
aminf light speed 12              # .97 only
aminf light effect solid|breathing|flow|mosaic|aurora|night|arc   # .97 only

aminf mouse-light off | on | toggle
aminf mouse-light status
aminf mouse-light color '#FF823C' # .97 only

aminf all off                     # dongle + mouse in one go

aminf rate 4000                   # 125 250 500 1000 2000 4000 8000
aminf dpi                         # list stages
aminf dpi select 1
aminf dpi set 0 1600              # x = y
aminf dpi set 0 1600 1400         # independent
aminf lod 0                       # .97 only
aminf toggle motionsync on        # .97 only
aminf profile 1                   # .97 only
```

On a .100, the .97-only commands print a "skipped" notice and change nothing.

Global flags: `--mouse auto|97|100`, `--device auto|dongle|usb`, `--json`,
`--quiet`, `--dry-run`. Exit codes: `0` ok, `1` no device, `2` command failed.

## What works on which mouse

| | .97 | .100 |
|---|---|---|
| status | ✓ | ✓ |
| dongle light on / off / toggle | ✓ | ✓ |
| dongle colour | ✓ (converted to hue + saturation) | ✓ (exact RGB) |
| dongle brightness / speed / effect | ✓ | — |
| mouse light on / off / toggle | ✓ | ✓ |
| mouse light colour | ✓ | — |
| polling rate | ✓ | ✓ |
| DPI stages (list / set / select) | ✓ | ✓ |
| LOD, sensor toggles, profile, raw | ✓ | — |

The **.100** dongle takes any colour even though the web UI only offers
seven swatches — `aminf light color '#FFB955'` sets it exactly.

The **.97** dongle stores hue and saturation instead of RGB, so a hex colour
is converted and may not match exactly.

## How it works

The two mice speak completely different protocols. The script works out
which one is connected and uses the right one.

### .97

Commands ride HID feature report `0x14` on the vendor collection `0xFF13`:

```
[payload_len][device_id] [05][type][len_lo][len_hi][cmd_lo][cmd_hi][data...]
```

* `device_id` — `0x00` addresses whatever you're connected to, `0x80`
  addresses the mouse through the dongle's relay.
* `type` — `0x5a` command, `0x5b` reply, `0x5d` write acknowledgement.
* `len` — command id (2 bytes) plus data length.
* Payload is zero-padded to 61 bytes.

Replies arrive by polling the same feature report. The device answers with a
zero-length frame while busy, and can stack several responses into one frame,
so the reader walks the frames until the command id matches.

**Lighting is all-or-nothing.** There's no "set just the brightness" command.
The dongle takes one 13-byte block, the mouse takes three bytes, and you send
the whole thing every time. Every change here is therefore read-modify-write,
which is what the configurator does behind its switches too.

**The dongle stores hue, not RGB.** Hue is 0–1530 with saturation 1–100, so
hex colours are converted before sending (`hex_to_hue_sat`). The mouse does
store literal RGB. Speed is stored with 80 added to it.

### .100

Every packet is 64 bytes in a feature report:

```
[cmd][6 parameter bytes][checksum][...zero padding]
checksum = 255 - (sum of the first 7 bytes & 255)
```

* **Dongle lighting** is two short commands: an on/off switch (`135` read /
  `7` write) and the colour/effect (`136` read / `8` write, checksum over 8
  bytes). Option `0`–`6` picks a preset swatch; anything else uses the RGB
  bytes.
* **Mouse settings** — polling rate, mouse light and more — live in one
  64-byte parameter block (`211` read / `83` write). DPI has its own
  64-byte block (`212` read / `84` write): active stage, stage count, then
  X, Y and colour for each of 8 stages. Like the configurator,
  the script reads the whole block, changes only the bytes it needs, and
  writes the whole block back. It refuses to write if the read failed.
* **Over 2.4G**, commands for the mouse are relayed through the dongle with a
  short handshake: open the channel (`F6 05`), wait until the dongle's status
  (`F7`) says it's ready, set the length (`FE 40`), send, wait, and for reads
  fetch the answer (`FC`).
* The report id isn't known in advance, so the script finds it with the same
  read-only health check the configurator runs.

## Safety

**.97:** four command ids are blocked in `BLOCKED` and refused before
anything is sent:

| id | name | what it does |
|---|---|---|
| 12494 | `resetSettings` | wipes all configuration |
| 12395 | `clearBonded` | destroys the 2.4G pairing |
| 12394 | `setPairingUniaa` | pairing handshake |
| 4353 | `rebootPairingDevice` | drops the link |

They exist because **command ids are not safe to enumerate**. Reads, writes
and destructive operations are interleaved in one flat id space with nothing
in the bytes distinguishing them — a "read" of an id that happens to be
`clearBonded` invokes `clearBonded`. This was found the hard way; scanning a
256-id range cost a pairing and a full config. `aminf raw` will happily send
anything not on the block list, so treat it accordingly.

**.100:** the opposite approach — an allow-list. `M100_ALLOWED` holds the only
fourteen commands the script will ever send. Reset, clear-Bluetooth and every
boot / firmware-update command are simply not in it, and devices in
firmware-update mode are ignored. There's no `raw` for the .100.

Nothing here writes firmware. The worst realistic outcome is a setting you
have to change back.

## Verified vs. transcribed

Both protocols were read out of the configurator's JS bundle.

**.97** — cross-checked against WebHID captures. Observed on real hardware:
`getBattery`, `getDongleBattery`, `getProfile`, `getReportRate`,
`setReportRate`, `getDongleLight`, `setDongleLight`, `readNvData`, `saveRgb`,
`setRgbRealtime`, `setLightSettings`. The rest — DPI, LOD, sensor toggles,
sleep timers, rotation, key remapping — are transcribed from the bundle and
encoded correctly as far as the source shows, but haven't been exercised.

**.100** — run on real hardware over the 2.4G dongle: status, dongle light
on/off and colour, mouse light on/off, polling rate. DPI (list / set /
select) is transcribed from the bundle but hasn't been exercised yet.

Both mice have only been tested with the dongle connected. The USB-direct
paths are implemented (`--device usb`) but unconfirmed. Reports welcome.

## Not implemented

Key remapping and macros, on either mouse. The command ids and payload
shapes are in the bundle if someone wants them. Worth noting: the .97's
key-remap type table has no tap/hold distinction, so per-button tap-vs-hold
bindings aren't achievable in firmware as it stands — that one needs Angry
Miao.

## Licence

MIT. Not affiliated with or endorsed by Angry Miao. Use at your own risk.
