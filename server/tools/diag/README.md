# Ultrasonic diagnostics (decision D9, plan M8)

Run on the car as `c4`, from the release's `server/` directory (`cd ~/car-scratch/current/server`). Nothing here
moves the car.

| Script | Needs the server stopped | What it shows |
|---|---|---|
| `edgeping.py a [s] [load] [switch_ms]` | yes (it owns GPIO5) | The real `EdgeUltrasonic` pinging: blind-window percentiles, results, discards, diagnostics, CPU. |
| `edgeping.py b [tries]` | yes | Whether the module triggers from the internal pull-up alone (edges kept armed, the line never driven). |
| `irqlat.py [n] [load]` | no (unused GPIO6, pull toggles only) | Kernel edge timestamps against known edge times; the shortest pulse whose edge kinds stay right. |
| `edgespike.py <n> <out.jsonl>` | yes | The first spike: libgpiod edges against polling on the same pings (needs python3-libgpiod). |
| `sigwatch.py [s]` | no (read-only `/dev/gpiomem`) | A passive observer of GPIO5: every high pulse, polled at about 2 us on one core (its own limit). |
| `gpio_abi.c` | (workstation) | The uAPI v2 layouts from `linux/gpio.h`; see its header for the Docker command. |

Stop and start the server with `systemctl --user stop car-scratch` / `systemctl --user start car-scratch`.
