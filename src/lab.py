from argparse import ArgumentParser
from collections import Counter
import hashlib
import json
import operator
from pathlib import Path
import re
import shlex
import sys
import traceback

from PIL import Image, ImageDraw

from backends import MesenBackend, PyBoyBackend

CONDITION = re.compile(r"ram\[(0x[0-9a-fA-F]+|[0-9]+)\]\s*(==|!=|<=|>=|<|>)\s*(0x[0-9a-fA-F]+|[0-9]+)")
COMPARE = {"==": operator.eq, "!=": operator.ne, "<": operator.lt,
           "<=": operator.le, ">": operator.gt, ">=": operator.ge}
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
SYSTEMS = {
    ".gb": "gb", ".gbc": "gb",
}
RAM = {"gb": (0xc000, 0x2000), "nes": (0, 0x800), "snes": (0x7e0000, 0x20000)}
OAM = {"gb": ("cpu", 0xfe00, 40), "nes": ("nesSpriteRam", 0, 64), "snes": ("snesSpriteRam", 0, 128)}
ADDRESS_MAX = {"gb": 0xffff, "nes": 0xffff, "snes": 0xffffff}
BUTTONS = {"up", "down", "left", "right", "a", "b", "start", "select", "x", "y", "l", "r"}


class CheckFailed(Exception):
    pass


def integer(text, low, high, name):
    value = int(text, 16 if text.lower().startswith("0x") else 10)
    if not low <= value <= high:
        raise ValueError(f"{name} must be in {low}..{high}")
    return value


def label(text):
    if not NAME.fullmatch(text):
        raise ValueError(f"Invalid label {text!r}")
    return text


def arity(args, minimum, maximum=None):
    maximum = minimum if maximum is None else maximum
    if not minimum <= len(args) <= maximum:
        expected = str(minimum) if minimum == maximum else f"{minimum}..{maximum}"
        raise ValueError(f"Expected {expected} argument(s), got {len(args)}")


def address(value):
    return f"0x{value:04X}"


class Lab:
    def __init__(self, backend, system, output):
        self.backend = backend
        self.system = system
        self.output = output
        self.frame = 0
        self.marks = {}
        self.states = {}
        self.events = []
        self.shots = []
        self.booted = False

    def addr(self, text):
        return integer(text, 0, ADDRESS_MAX[self.system], "address")

    def frames(self, text):
        return integer(text, 1, 1_000_000, "frames")

    def step(self, frames, button=None):
        self.backend.step(frames, button)
        self.frame += frames

    def event(self, command, **data):
        self.events.append({"command": command, "frame": self.frame, **data})

    def condition(self, expression):
        match = CONDITION.fullmatch(expression)
        if not match:
            raise ValueError("Expected condition like ram[0xC000] != 0")
        loc, comparison, target = match.groups()
        return self.addr(loc), comparison, integer(target, 0, 255, "byte")

    def read(self, location, count):
        if location + count - 1 > ADDRESS_MAX[self.system]:
            raise ValueError("RAM range crosses the end of the address space")
        return self.backend.read(location, count)

    def execute(self, command, args):
        if not self.booted and command != "boot":
            raise ValueError("Start the script with boot")
        action = getattr(self, "cmd_" + command, None)
        if not action:
            raise ValueError(f"Unknown command {command!r}")
        action(args)

    def cmd_boot(self, args):
        arity(args, 0)
        if self.booted:
            raise ValueError("boot is only allowed at the start")
        self.booted = True

    def cmd_wait(self, args):
        arity(args, 1)
        self.step(self.frames(args[0]))

    def input(self, args):
        arity(args, 1, 2)
        button = args[0].lower()
        if button not in BUTTONS or (self.system != "snes" and button in {"x", "y", "l", "r"}):
            raise ValueError(f"Unknown {self.system} button {button!r}")
        frames = self.frames(args[1]) if len(args) == 2 else 1
        self.step(frames, button)

    def cmd_hold(self, args):
        self.input(args)

    def cmd_press(self, args):
        self.input(args)
        self.step(1)

    def cmd_mark(self, args):
        arity(args, 1, 3)
        if len(args) == 2:
            raise ValueError("Provide both RAM start and size, or neither")
        name = label(args[0])
        start, size = RAM[self.system]
        if len(args) == 3:
            start = self.addr(args[1])
            size = integer(args[2], 1, 0x20000, "RAM size")
        if name in self.marks:
            raise ValueError(f"Duplicate mark {name!r}")
        if name == "now":
            raise ValueError("now is reserved")
        self.marks[name] = (self.frame, start, self.read(start, size))
        self.event("mark", name=name, start=address(start), size=size)

    def cmd_read(self, args):
        arity(args, 1, 2)
        loc = self.addr(args[0])
        count = integer(args[1], 1, 0x20000, "RAM size") if len(args) == 2 else 1
        self.event("read", start=address(loc), bytes=list(self.read(loc, count)))

    def cmd_until(self, args):
        if len(args) < 3 or args[-2] != "max":
            raise ValueError("Use until <condition> max <frames>")
        loc, compare, target = self.condition(" ".join(args[:-2]))
        start = self.frame
        limit = self.frames(args[-1])
        for elapsed in range(limit + 1):
            value = self.read(loc, 1)[0]
            if COMPARE[compare](value, target):
                self.event("until", address=address(loc), comparison=compare, target=target,
                           value=value, elapsed=self.frame - start)
                return
            if elapsed < limit:
                self.step(1)
        raise CheckFailed(f"{address(loc)} {compare} {target} did not hold within {limit} frames (last value {value})")

    def cmd_expect(self, args):
        loc, compare, target = self.condition(" ".join(args))
        value = self.read(loc, 1)[0]
        if not COMPARE[compare](value, target):
            raise CheckFailed(f"Expected {address(loc)} {compare} {target}, got {value} at frame {self.frame}")
        self.event("expect", address=address(loc), comparison=compare, target=target, value=value)

    def cmd_measure(self, args):
        arity(args, 3, 7)
        name, keyword, start = args[:3]
        if keyword != "from" or start not in self.marks:
            raise ValueError("Use measure <name> from <mark> [to <mark>] [expect <frames>]")
        end = self.frame
        tail = args[3:]
        if len(tail) >= 2 and tail[0] == "to":
            if tail[1] != "now":
                if tail[1] not in self.marks:
                    raise ValueError(f"Unknown mark {tail[1]!r}")
                end = self.marks[tail[1]][0]
            tail = tail[2:]
        frames = end - self.marks[start][0]
        if frames < 0:
            raise ValueError("Measurement ends before it starts")
        if tail:
            if len(tail) != 2 or tail[0] != "expect":
                raise ValueError("Use expect <frames> after the start and end marks")
            expected = integer(tail[1], 0, 1_000_000, "expected frames")
            if frames != expected:
                raise CheckFailed(f"Expected {label(name)} to take {expected} frames, got {frames}")
        self.event("measure", name=label(name), start=self.marks[start][0], end=end, frames=frames)

    def cmd_diff(self, args):
        arity(args, 2)
        names = args[1].split("..")
        if args[0] != "ram" or len(names) != 2 or names[0] not in self.marks:
            raise ValueError("Use diff ram <mark>..<mark|now>")
        _, start, before = self.marks[names[0]]
        if names[1] == "now":
            after = self.read(start, len(before))
        else:
            if names[1] not in self.marks:
                raise ValueError(f"Unknown mark {names[1]!r}")
            _, end_start, after = self.marks[names[1]]
            if start != end_start or len(before) != len(after):
                raise ValueError("Both marks must cover the same RAM range")
        changes = [[address(start + i), a, b] for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.event("diff", marks=names, changes=changes)

    def cmd_snap(self, args):
        arity(args, 1)
        name = label(args[0])
        if name in self.shots:
            raise ValueError(f"Duplicate snapshot {name!r}")
        self.backend.snapshot(self.output / f"snap-{name}.png")
        self.shots.append(name)
        self.event("snap", name=name, file=f"snap-{name}.png")

    def cmd_save(self, args):
        arity(args, 1)
        name = label(args[0])
        self.states[name] = (self.frame, self.backend.save(name))
        self.event("save", name=name)

    def cmd_load(self, args):
        arity(args, 1)
        name = label(args[0])
        if name not in self.states:
            raise ValueError(f"Unknown state {name!r}")
        self.frame, state = self.states[name]
        self.backend.load(state)
        self.event("load", name=name)

    def cmd_log(self, args):
        arity(args, 1)
        self.event("log", name=label(args[0]))

    def cmd_watch(self, args):
        arity(args, 3)
        if args[1] != "for":
            raise ValueError("Use watch <address> for <frames>")
        loc = self.addr(args[0])
        initial = previous = self.read(loc, 1)[0]
        changes = []
        for _ in range(self.frames(args[2])):
            self.step(1)
            value = self.read(loc, 1)[0]
            if value != previous:
                changes.append([self.frame, value])
                previous = value
        self.event("watch", address=address(loc), initial=initial, changes=changes)

    def cmd_oam(self, args):
        arity(args, 0, 2)
        region, start, total = OAM[self.system]
        index = integer(args[0], 0, total - 1, "sprite index") if args else 0
        count = integer(args[1], 1, total - index, "sprite count") if len(args) == 2 else (1 if args else total)
        data = self.backend.read(start + index * 4, count * 4, region)
        sprites = [list(data[i:i + 4]) for i in range(0, len(data), 4)]
        extra = {}
        if self.system == "snes":
            extra["highBits"] = list(self.backend.read(512, 32, region))
        self.event("oam", start=index, sprites=sprites, **extra)

    def cmd_tiles(self, args):
        arity(args, 0, 3)
        if args and len(args) != 3:
            raise ValueError("Use tiles [start width height]")
        if self.system == "gb":
            lcdc = self.read(0xff40, 1)[0]
            region, start, stride, tile_bytes, height_limit = "cpu", 0x9c00 if lcdc & 8 else 0x9800, 32, 1, 32
        elif self.system == "nes":
            region, start, stride, tile_bytes, height_limit = "nesPpuMemory", 0x2000, 32, 1, 30
        else:
            if not args:
                raise ValueError("SNES tiles requires the VRAM byte start and grid dimensions")
            region, start, stride, tile_bytes, height_limit = "snesVideoRam", 0, 32, 2, 32
        if args:
            start = self.addr(args[0])
            width = integer(args[1], 1, stride, "tile width")
            height = integer(args[2], 1, height_limit, "tile height")
        else:
            width, height = stride, height_limit
        rows = []
        for row in range(height):
            data = self.backend.read(start + row * stride * tile_bytes, width * tile_bytes, region)
            rows.append([int.from_bytes(data[i:i + tile_bytes], "little") for i in range(0, len(data), tile_bytes)])
        self.event("tiles", start=address(start), rows=rows)

    def contact_sheet(self):
        columns = min(4, max(1, len(self.shots)))
        cell_width, cell_height = 248, 205
        rows = max(1, (len(self.shots) + columns - 1) // columns)
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#161a23")
        draw = ImageDraw.Draw(sheet)
        if not self.shots:
            draw.text((10, 10), "No snapshots requested", fill="white")
        for i, name in enumerate(self.shots):
            x, y = (i % columns) * cell_width, (i // columns) * cell_height
            with Image.open(self.output / f"snap-{name}.png") as image:
                image.thumbnail((cell_width - 16, cell_height - 32), Image.Resampling.NEAREST)
                sheet.paste(image, (x + (cell_width - image.width) // 2, y + 8))
            draw.text((x + 8, y + cell_height - 20), name, fill="white")
        sheet.save(self.output / "contact-sheet.png")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    rom = args.rom.resolve(strict=True)
    script = args.script.resolve(strict=True)
    output = args.output
    system = SYSTEMS.get(rom.suffix.lower())
    if not system or args.backend == "pyboy" and system != "gb":
        raise ValueError(f"{args.backend} cannot load {rom.suffix or 'this file type'}")
    engine = PyBoyBackend if args.backend == "pyboy" else MesenBackend
    backend = engine(rom, output)
    lab = Lab(backend, system, output)
    status, code, error = "PASS", 0, None
    line_number = 1
    try:
        for line_number, line in enumerate(script.read_text(encoding="utf-8-sig").splitlines(), 1):
            tokens = shlex.split(line, comments=True)
            if tokens:
                lab.execute(tokens[0], tokens[1:])
        if not lab.booted:
            raise ValueError("Script is empty")
    except CheckFailed as exc:
        status, code, error = "FAIL", 1, f"line {line_number}: {exc}"
    except Exception as exc:
        status, code, error = "ERROR", 2, f"line {line_number}: {exc}"
        traceback.print_exc()
    finally:
        backend.close()
    lab.contact_sheet()
    data = {
        "schema": 1, "status": status, "system": system, "backend": args.backend,
        "engineVersion": backend.engine_version, "romSha256": digest(rom), "scriptSha256": digest(script),
        "frame": lab.frame, "events": lab.events,
    }
    if error:
        data["error"] = error
    (output / "measure.json").write_bytes((json.dumps(data, indent=2) + "\n").encode())
    print(f"{status} PocketParity | {backend.engine_version} | frame {lab.frame}")
    if error:
        print(error)
    measures = [event for event in lab.events if event["command"] == "measure"]
    for event in (measures if args.full else measures[:8]):
        print(f'{event["name"]}: {event["frames"]} frames')
    counts = Counter(event["command"] for event in lab.events)
    print(", ".join(f"{count} {name}" for name, count in sorted(counts.items())) or "No results")
    return code


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("script", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--backend", choices=("pyboy", "mesen"), required=True)
    parser.add_argument("--full", action="store_true")
    options = parser.parse_args()
    try:
        sys.exit(run(options))
    except Exception as exc:
        print(f"ERROR PocketParity: {exc}")
        traceback.print_exc()
        sys.exit(2)
