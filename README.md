# PocketParity

Run frame, input, and memory assertions against a Game Boy cartridge. The same script runs on PyBoy and Mesen. PocketParity records JSON measurements, screenshots, and a contact sheet.

## Install and try it

Install Node.js 22 or later, Python 3.13, and [GBDK 4.5.0](https://github.com/gbdk-2020/gbdk-2020/releases/tag/4.5.0) for the bundled source example. Put GBDK's `bin` directory on your PATH. On Windows PowerShell:

```powershell
git clone https://github.com/thebeefasaurusrex/pocket-parity.git
cd pocket-parity
npm install --global .
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install -r src/requirements.txt
lcc -o example/timing.gb example/timing.c
pocket-parity run example/timing.gb example/timing.txt
```

On macOS or Linux, use `python3.13 -m venv .venv` and `.venv/bin/python3 -m pip install -r src/requirements.txt` for the PyBoy backend. Windows is the verified release environment. Run without installing the CLI with `node bin/pocket-parity.mjs`. If you invoke the CLI outside the checkout, set `POCKET_PARITY_PYTHON` to the absolute path of the virtual environment's Python executable.

To compare with the Windows Mesen backend, obtain [Mesen 2.1.1](https://github.com/SourMesen/Mesen2/releases/tag/2.1.1) separately and set its executable path:

```powershell
$env:POCKET_PARITY_MESEN = (Resolve-Path 'path/to/Mesen.exe').Path
pocket-parity run example/timing.gb example/timing.txt --backend mesen
$env:POCKET_PARITY_LCC = (Get-Command lcc).Source
npm test
```

The test suite always checks CLI input validation. It runs the dual emulator integration when the GBDK compiler and Mesen executable are configured, and expects the Python environment above. The Mesen mailbox needs an ASCII temporary directory. The input cartridge is read only. Mesen works on a temporary copy with isolated settings.

PocketParity accepts `.gb` and `.gbc` inputs. The example is original homebrew source and builds locally. Bring your own legally usable cartridge image. Emulators and cartridge binaries are installed or built separately.

`pocket-parity show <evidence-folder>` rechecks the ROM, script, worker, environment and output hashes. Use `--cwd <project>` to resolve input paths and store evidence in that project. Use `--full` for individual events. Exit codes are 0 for pass, 1 for an assertion failure, 2 for setup or script errors, and 3 for stale evidence.

Evidence appears under `.pocket-parity/`. Stamps and logs may include local paths and cartridge fingerprints. Keep this directory out of version control and inspect evidence before sharing.

## Script reference

Start scripts with `boot`. Frames count from startup at 0. Use an explicit warmup or an `until` condition before comparing absolute timings across emulators. Addresses accept decimal or hexadecimal.

| Command | Behavior |
| --- | --- |
| `wait 60` | Advance 60 frames with neutral input. |
| `hold RIGHT 30` | Advance 30 frames with Right held. The next step specifies its own input. |
| `press A 4` | Advance four frames with A held, then one frame with neutral input. The default button duration is one frame. |
| `mark start 0xC100 6` | Record the frame and a RAM snapshot. Omit both address and size to capture the system's main work RAM range. |
| `read 0xC100 6` | Read six bytes from CPU memory. |
| `until ram[0xC102] != 0 max 600` | Check now, then after each neutral frame up to the cap. |
| `expect ram[0xC102] == 1` | Fail with the script line and observed byte if the condition fails. |
| `measure elapsed from start to end expect 60` | Count frames between two marks. The ending mark defaults to now. The expectation is optional. |
| `diff ram start..now` | List changed addresses and bytes against another matching mark or live memory. |
| `watch 0xC101 for 120` | Advance 120 neutral frames and log the frame and new value every time the byte changes. |
| `log finished` | Add a named event at the current frame. |
| `oam 0 1` | Read raw OAM entries for one sprite starting at index 0. Omit the range to read the whole table. |
| `tiles 0x9800 2 2` | Read a rectangle of raw background tile entries. The Game Boy default starts at the active LCDC background map. |
| `snap title` | Save a PNG and add it to the contact sheet. |
| `save s1` and `load s1` | Save and restore emulator state and the script's frame counter within a run. |

For this test cartridge, `press START 4` resets the counter and releases Start for one frame. The first mark therefore starts at cartridge tick 1. The first counter increment is 59 frames after that mark, subsequent increments are 60 apart, and the phase changes after 2,099 frames from the mark (2,100 ticks from reset).

## License

Original project code is licensed under [Apache 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
