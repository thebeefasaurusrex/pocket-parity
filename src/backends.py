import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from importlib.metadata import version

from PIL import Image


class PyBoyBackend:
    def __init__(self, rom, work):
        from pyboy import PyBoy

        self.engine_version = "PyBoy " + version("pyboy")

        self.core = PyBoy(io.BytesIO(rom.read_bytes()), window="null", sound_emulated=False, log_level="ERROR")
        self.core.set_emulation_speed(0)
        self.button = None

    def close(self):
        self.core.stop(save=False)

    def step(self, frames, button=None):
        if button != self.button:
            if self.button is not None:
                self.core.button_release(self.button)
            if button is not None:
                self.core.button_press(button)
            self.button = button
        self.core.tick(frames, render=True, sound=False)

    def read(self, start, size, region="cpu"):
        if region != "cpu":
            raise ValueError("PyBoy supports Game Boy CPU memory")
        return bytes(self.core.memory[start:start + size])

    def snapshot(self, path):
        self.core.screen.image.save(path)

    def save(self, name):
        data = io.BytesIO()
        self.core.save_state(data)
        return data.getvalue(), self.button

    def load(self, state):
        data, self.button = state
        self.core.load_state(io.BytesIO(data))


class MesenBackend:
    engine_version = "Mesen 2.1.1"

    def __init__(self, rom, work):
        from tempfile import TemporaryDirectory

        self.system = {".gb": "gb", ".gbc": "gb", ".nes": "nes", ".sfc": "snes", ".smc": "snes"}[rom.suffix.lower()]
        cpu, memory, last = {
            "gb": ("gameboy", "gameboyMemory", 0xffff),
            "nes": ("nes", "nesMemory", 0xffff),
            "snes": ("snes", "snesMemory", 0xffffff),
        }[self.system]
        executable = Path(os.environ["POCKET_PARITY_MESEN"]).resolve(strict=True)
        self.temporary = TemporaryDirectory(prefix="mesen-")
        self.path = Path(self.temporary.name)
        config = json.dumps(self.path.as_posix(), ensure_ascii=False)
        if not self.path.as_posix().isascii():
            # Mesen's Windows Lua file functions use the active code page.
            raise ValueError("Use an ASCII TEMP path for the Mesen mailbox")
        wrapper = self.path / "romlab.lua"
        wrapper.write_text(
            f"ROMLAB = {{ directory = {config}, cpu = '{cpu}', memory = '{memory}', lastAddress = {last} }}\n"
            + Path(__file__).with_name("mesen.lua").read_text(encoding="utf8"),
            encoding="utf8",
        )
        # An isolated copy keeps any emulator save files away from the original ROM.
        # The portable app folder also isolates batteries, debugger settings and logs.
        portable = self.path / "Mesen.exe"
        try:
            os.link(executable, portable)
        except OSError:
            shutil.copyfile(executable, portable)
        (self.path / "settings.json").write_text("{}", encoding="ascii")
        working_rom = self.path / ("cartridge" + rom.suffix.lower())
        shutil.copyfile(rom, working_rom)
        flags = [
            "--testRunner", "--timeout=180", "--enableStdout",
            "--Debug.ScriptWindow.AllowIoOsAccess=true", "--Debug.ScriptWindow.AllowNetworkAccess=false",
            "--Debug.ScriptWindow.ScriptTimeout=20", "--Gameboy.Controller.Type=GameboyController",
            "--Gameboy.RamPowerOnState=AllZeros", "--Gameboy.BlendFrames=false",
            "--Snes.Port1.Type=SnesController", "--Nes.Port1.Type=NesController",
        ]
        self.log = (work / "mesen.log").open("wb")
        try:
            self.process = subprocess.Popen(
                [str(portable), *flags, str(wrapper), str(working_rom)],
                cwd=self.path,
                stdout=self.log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            self.log.close()
            self.temporary.cleanup()
            raise

    def command(self, request):
        temporary = self.path / "request.tmp"
        reply = self.path / "reply"
        deadline = time.monotonic() + 12

        def retry(action):
            while True:
                try:
                    return action()
                except (FileNotFoundError, PermissionError) as exc:
                    if self.process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Mesen stopped responding; see mesen.log") from exc
                    time.sleep(0.001)

        retry(lambda: temporary.write_bytes(request.encode("ascii")))
        retry(lambda: temporary.replace(self.path / "request"))
        status, _, body = retry(reply.read_bytes).partition(b"\n")
        retry(reply.unlink)
        if status != b"OK":
            raise RuntimeError(body.decode(errors="replace"))
        return body

    def close(self):
        try:
            if self.process.poll() is None:
                self.command("exit")
                self.process.wait(timeout=2)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.log.close()
            self.temporary.cleanup()

    def step(self, frames, button=None):
        self.command(f"step {frames} {button or ''}")

    def read(self, start, size, region="cpu"):
        return self.command(f"read {region} {start} {size}")

    def snapshot(self, path):
        screenshot = self.command("snap")
        with Image.open(io.BytesIO(screenshot)) as image:
            image.save(path)

    def save(self, name):
        self.command(f"save {name}")
        return name

    def load(self, state):
        self.command(f"load {state}")
