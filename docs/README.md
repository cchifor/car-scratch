# Car Scratch documentation

Start with [architecture.md](architecture.md) for the big picture, then [components.md](components.md) for the
part you are working on. The protocol and the contracts are the specifications: change them only together with
every consumer.

| I want to... | Read | What it is |
|---|---|---|
| understand how the editor, the car server and the hardware fit together | [architecture.md](architecture.md) | System context, components, a drive end to end, concurrency on the car, safety layers, control arbitration, deployment, a directory map (with Mermaid diagrams) |
| find the files, interfaces, configuration, tests and gotchas of one part | [components.md](components.md) | One section per component, server and editor |
| send or handle a WebSocket message or a REST call | [protocol.md](protocol.md) | The wire protocol v1: transport, session lifecycle, arbitration, commands, telemetry, REST |
| know the safety semantics, the HAL, the world format, `CarLink` or the blocks | [contracts.md](contracts.md) | Internal contracts shared by the server, the simulator and the editor |
| run the car safely, or explain to someone why it stopped | [safety.md](safety.md) | Safety guide for the people driving the car |
| measure and set steering trim, speed and coast tables | [calibration.md](calibration.md) | Calibration fields, how to measure them, the values measured on this car |
| run the first tests on the real car | [on-car-checklist.md](on-car-checklist.md) | Staged checklist (dry run, wheels up, floor, examples, iPad, two devices) with result tables |
| use Car Scratch on an iPad | [ipad.md](ipad.md) | Opening it, Home Screen, layout, Save/Open on the car, what happens when the iPad locks |
| watch every sensor live or drive from a terminal | [carctl.md](carctl.md) | `carctl`: terminal dashboard, keyboard driving, one-shot commands, on the car or a workstation |
| use the car without a router (iPad or laptop straight to the car) | [wifi.md](wifi.md) | The car's fallback WiFi hotspot: when it starts, how to join, modes, safe testing, recovery |
| make the car's Raspberry Pi headless and boot faster | [pi-tune.md](pi-tune.md) | `pi-tune.sh` (services, boot config) and `pi-slim.sh` (removing the desktop), with measurements |
| get children started | [kids-guide.md](kids-guide.md) | The most important blocks, safety rules and three small challenges |
| see why something was decided | [decisions.md](decisions.md) | Decision log of the design reviews (historical record) |
| see what was planned originally | [plan.md](plan.md) | The implementation plan of 2026-11-20 (historical record) |
| check which upstream editor files were changed (AGPL-3.0) | [modified-upstream-files.md](modified-upstream-files.md) | Files under `editor/` added, modified or deleted relative to the imported upstream snapshot |
| find the screenshots used in the repository README | [screenshots/](screenshots) | Desktop and iPad screenshots |

[plan.md](plan.md) and [decisions.md](decisions.md) are historical records: they are not updated when the code
changes. Where they disagree with the code, [protocol.md](protocol.md) or [contracts.md](contracts.md), the code
and those specifications win.
