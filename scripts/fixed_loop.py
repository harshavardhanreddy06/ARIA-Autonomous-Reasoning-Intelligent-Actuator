"""
scripts/fixed_loop.py
======================
Terminal-only demonstration of the real, closed EnergyPlus <-> LLM control
loop, for the PoC video's "loop in action" segment — same code path as
main.py (real EnergyPlus physics, real qwen2.5:3b calls), narrated to the
terminal so the causality is visible on screen: live sensor data leaving
EnergyPlus, going to the LLM, and the LLM's decision being written back to
the actuators before the next tick.

Skips silently through the overnight unoccupied hours (near-instant — no
LLM calls made) and starts the narrated demo once real occupied-zone data
appears, since an occupied scenario is a stronger demonstration than an
unoccupied "nothing changed" cycle.

Uses an isolated output directory and an in-memory database — this never
touches the real aria.db or output/aria from the completed 7-day run.

Run: python3 scripts/fixed_loop.py [num_ticks]   (default: 2 ticks)
"""
import logging
import os
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from agent.aria_agent import AriaAgent
from agent.fallback_handler import FallbackHandler
from agent.tool_registry import ToolRegistry
from carbon.grid_intensity import get_carbon_intensity
from config.env_loader import load_settings
from data.database import Database
from simulation.energyplus_env import EnergyPlusEnv

SEP = "=" * 70
NUM_TICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 2
MAX_SEARCH_TICKS = 200  # generous ceiling while searching for occupancy


def stream_print(text, delay=0.018):
    """The real response already arrived instantly (Ollama's chat API isn't
    streamed here) — this hardcodes a typewriter effect on the way OUT so a
    reviewer watching the recording can actually read it, rather than the
    whole result dumping to the terminal in one frame."""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


class ThrottledStdout:
    """EnergyPlus's C-level startup output (sizing, warmup) is genuinely
    computed in well under a second — printing it all at once, not a
    buffering artifact. For a screen recording where a reviewer needs to
    actually read it, this redirects the process's real stdout through a
    pipe and re-emits it with pacing, then restores stdout exactly as it
    was.

    Reads byte-by-byte (not line-by-line) and only sleeps at newlines —
    that way a burst of many lines arriving at once (EnergyPlus's startup
    dump) still gets paced per line, but text that's ALREADY being written
    slowly by the caller (see stream_print, used for the tick narration)
    passes through with its own pacing intact instead of being buffered
    until a full line is available and then flushed instantly.
    """

    def __init__(self, delay=0.09):
        self.delay = delay

    def __enter__(self):
        sys.stdout.flush()
        self._saved_fd = os.dup(1)
        self._read_fd, write_fd = os.pipe()
        os.dup2(write_fd, 1)
        os.close(write_fd)
        self._out = os.fdopen(self._saved_fd, "w")
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return self

    def _pump(self):
        try:
            while True:
                chunk = os.read(self._read_fd, 4096)
                if not chunk:
                    break
                for ch in chunk.decode(errors="replace"):
                    self._out.write(ch)
                    self._out.flush()
                    if ch == "\n":
                        time.sleep(self.delay)
        except (OSError, ValueError):
            pass

    def __exit__(self, *exc):
        sys.stdout.flush()
        # Restore fd 1 FIRST — this closes the pipe's write end (fd 1 was
        # its last reference), which lets the reader thread's read() loop
        # hit EOF and finish on its own. Only close our descriptors AFTER
        # the thread has fully joined — closing them earlier races the
        # thread while it's still mid-write and silently drops output.
        os.dup2(self._saved_fd, 1)
        self._thread.join(timeout=10)
        try:
            self._out.close()
        except OSError:
            pass
        try:
            os.close(self._read_fd)
        except OSError:
            pass


def make_on_timestep(agent, database, settings, env, narrate_ticks, throttle):
    progress = {"occupied_tick_found": False, "skip_count": 0}

    def on_timestep(timestep, snapshot):
        # From the very first tick onward, stop adding reader-thread delay —
        # stream_print already paces the narration itself, and letting this
        # reader's own per-newline sleep keep running here just accumulates
        # lag across every line of the tick's output, which is what caused
        # EnergyPlus's finalization burst to visually reorder ahead of the
        # last tick's tail end. Only the initial startup burst (before any
        # tick has fired) needs this reader's pacing.
        throttle.delay = 0.0

        any_occupied = any(z["occ_fraction"] > 0 for z in snapshot["zones"])

        if not progress["occupied_tick_found"]:
            if not any_occupied:
                progress["skip_count"] += 1
                if progress["skip_count"] == 1:
                    print("\n(fast-forwarding through unoccupied hours to reach real occupied data...)")
                if progress["skip_count"] % 15 == 0:
                    print(
                        f"  ...tick {timestep}, "
                        f"{snapshot['sim_month']:02d}/{snapshot['sim_day']:02d} "
                        f"{snapshot['sim_hour']:02d}:{snapshot['sim_minute']:02d} — still unoccupied"
                    )
                return
            progress["occupied_tick_found"] = True
            env.max_ticks = timestep + narrate_ticks - 1
            print(f"(skipped {progress['skip_count']} unoccupied ticks — occupancy begins at tick {timestep})\n")

        print(f"\n{SEP}")
        print(f"REAL TICK {timestep}  —  live from EnergyPlus")
        print(SEP)
        print(
            f"Time: {snapshot['sim_month']:02d}/{snapshot['sim_day']:02d} "
            f"{snapshot['sim_hour']:02d}:{snapshot['sim_minute']:02d}   "
            f"Outdoor: {snapshot['outdoor_temp']:.1f}C   "
            f"Building demand: {snapshot['total_demand_kw']:.2f} kW"
        )
        for z in snapshot["zones"]:
            occ = "OCCUPIED  " if z["occ_fraction"] > 0 else "unoccupied"
            print(
                f"  Zone {z['id']} [{occ}]  Temp={z['temp_c']:5.1f}C  "
                f"CoolSP={z['cool_sp']:.1f}  HeatSP={z['heat_sp']:.1f}"
            )

        carbon = get_carbon_intensity(snapshot["sim_hour"], settings)
        print(f"  Grid carbon: {carbon['current_gco2_kwh']} gCO2/kWh  [{carbon['strategy']}]")

        print("\n  -> sending live sensor data to qwen2.5:3b ...")
        t0 = time.time()
        setpoints = agent.run_decision_cycle(timestep, snapshot, carbon)
        elapsed = time.time() - t0
        print(f"  <- LLM responded in {elapsed:.1f}s")

        print("\n  TOOL CALLS FROM LLM:")
        if not agent._tool_calls_made:
            stream_print("    (no changes needed this cycle)")
        reasoning = None
        for call in agent._tool_calls_made:
            stream_print(f"    -> {call['name']}({call['args']})", delay=0.008)
            if call["name"] == "log_decision":
                reasoning = call["args"].get("reasoning")

        if reasoning:
            print("\n  AI REASONING:")
            stream_print(f'    "{reasoning}"', delay=0.022)

        print(f"\n  CONTROL ACTIONS WRITTEN BACK TO ENERGYPLUS: {setpoints}")
        print(SEP)

        database.insert_timestep_snapshot("demo", timestep, snapshot)

    return on_timestep


def main():
    import tempfile

    # Force line-buffered stdout regardless of TTY vs pipe — EnergyPlus's
    # C-level output writes directly to the OS file descriptor, bypassing
    # Python's own stdout buffer entirely. If Python's buffer is holding
    # anything un-flushed when that happens, the C-level write lands first
    # and visually reorders ahead of it.
    sys.stdout.reconfigure(line_buffering=True)

    # Library logging (handle validation, ollama's httpx client) goes to
    # stderr, unbuffered and unaffected by the stdout throttle below — left
    # at INFO level it interleaves unpredictably with the narrated output.
    # WARNING+ still shows, so a real failure is never hidden.
    logging.getLogger().setLevel(logging.WARNING)

    settings = load_settings()
    settings["energyplus"]["output_dir"] = tempfile.mkdtemp(prefix="aria_live_demo_")

    database = Database(":memory:")
    fallback = FallbackHandler()

    print(SEP)
    print(f"ARIA — Live Loop Demo  ({NUM_TICKS} real occupied tick(s), real EnergyPlus + real LLM)")
    print(SEP)

    env = EnergyPlusEnv(settings=settings, print_snapshots=False, max_ticks=MAX_SEARCH_TICKS)
    tool_registry = ToolRegistry(env.state_manager, database)
    agent = AriaAgent(tool_registry, fallback, settings)
    throttle = ThrottledStdout(delay=0.09)
    env.on_timestep = make_on_timestep(agent, database, settings, env, NUM_TICKS, throttle)

    with throttle:
        exit_code = env.run()

    print(f"\nDone. Exit code: {exit_code}. Real ticks completed: {env._tick_count}")
    print("(This demo run is isolated — nothing written to aria.db or output/aria.)")


if __name__ == "__main__":
    main()
