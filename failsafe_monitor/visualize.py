"""
Visualisation of simulated flights: dashboard screenshots and MP4/GIF videos.

Requires matplotlib (``pip install failsafe-monitor[viz]``) and, for video,
an ``ffmpeg`` binary on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Polygon  # noqa: E402

from .models import MonitorState  # noqa: E402
from .simulator import SimResult  # noqa: E402

WIDTH_PX, HEIGHT_PX, DPI = 1280, 720, 100

BG = "#0d1117"
PANEL = "#161b22"
GRID = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
GREEN = "#3fb950"
AMBER = "#d29922"
ORANGE = "#f0883e"
RED = "#f85149"
PURPLE = "#a371f7"
BLUE = "#58a6ff"

STATE_COLORS = {
    MonitorState.CONNECTING: MUTED,
    MonitorState.MONITORING: GREEN,
    MonitorState.WARNING: AMBER,
    MonitorState.RTL: ORANGE,
    MonitorState.LAND: RED,
    MonitorState.BLIND: PURPLE,
    MonitorState.DONE: MUTED,
}

EVENT_COLORS = {
    "info": MUTED,
    "mode": BLUE,
    "warning": AMBER,
    "violation": ORANGE,
    "action": RED,
    "alert": PURPLE,
}

plt.rcParams.update(
    {
        "font.family": ["Inter", "DejaVu Sans"],
        "font.size": 9,
        "text.color": TEXT,
        "axes.facecolor": PANEL,
        "axes.edgecolor": GRID,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "figure.facecolor": BG,
    }
)


def _style_axes(ax: plt.Axes, title: str) -> None:
    ax.set_title(title, loc="left", color=TEXT, fontsize=10, fontweight="bold", pad=6)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.7)
    for spine in ax.spines.values():
        spine.set_color(GRID)


class Dashboard:
    """A reusable figure that renders any tick of a SimResult."""

    def __init__(self, result: SimResult) -> None:
        self.result = result
        self.fig = plt.figure(figsize=(WIDTH_PX / DPI, HEIGHT_PX / DPI), dpi=DPI)
        self._build()

    # -- layout ---------------------------------------------------------
    def _build(self) -> None:
        r = self.result
        s = r.scenario
        fig = self.fig
        frames = r.frames
        self.t = np.array([f.t for f in frames])
        self.n = np.array([f.north for f in frames])
        self.e = np.array([f.east for f in frames])
        self.alt = np.array([f.alt for f in frames])
        self.bat = np.array([f.battery_pct for f in frames])
        t_end = max(self.t[-1], 1.0)

        fig.text(0.015, 0.955, "Drone Failsafe Runtime Monitor", fontsize=16, fontweight="bold")
        fig.text(0.015, 0.922, "closed-loop simulation  ·  rule engine + action executor  ·  "
                 f"rules: {r.config.vehicle_id}", color=MUTED, fontsize=9)
        fig.text(0.985, 0.955, f"{s.id}  ·  {s.name}", ha="right", fontsize=15, fontweight="bold",
                 color=BLUE)
        fig.text(0.985, 0.922, f"Expected: {s.expected}", ha="right", color=MUTED, fontsize=9)

        # Map
        ax = fig.add_axes((0.045, 0.06, 0.46, 0.81))
        _style_axes(ax, "Top-down view (metres from home)")
        ax.set_xlabel("East (m)")
        ax.set_ylabel("North (m)")
        if r.fence_local:
            pts = [(e, n) for n, e in r.fence_local]
            ax.add_patch(Polygon(pts, closed=True, facecolor=GREEN, alpha=0.07, edgecolor=GREEN,
                                 linewidth=1.8, label="Geofence"))
        if r.warn_fence_local:
            pts = [(e, n) for n, e in r.warn_fence_local]
            ax.add_patch(Polygon(pts, closed=True, fill=False, edgecolor=AMBER, linewidth=1.0,
                                 linestyle="--", label="Warning buffer"))
        wps = [(0.0, 0.0)] + [(e, n) for n, e, _ in s.waypoints]
        ax.plot([p[0] for p in wps], [p[1] for p in wps], ":", color=MUTED, linewidth=1.0,
                marker="o", markersize=3, label="Planned route")
        ax.plot(0, 0, marker="s", markersize=9, color=BLUE, linestyle="none")
        ax.annotate("HOME", (0, 0), (6, -12), textcoords="offset points", color=BLUE, fontsize=8)

        xs = list(self.e) + [p[0] for p in wps] + [p[1] for p in r.fence_local]
        ys = list(self.n) + [p[1] for p in wps] + [p[0] for p in r.fence_local]
        seen_n = [f.seen_north for f in frames if f.seen_north is not None]
        ys += seen_n
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        half = max(max(xs) - min(xs), (max(ys) - min(ys)) * 1.1) / 2 + 18
        ax.set_xlim(cx - half, cx + half)
        ax.set_ylim(cy - half * 1.1, cy + half * 1.1)
        ax.set_aspect("equal", adjustable="box")

        pts = np.column_stack([self.e, self.n])
        self.segments = np.stack([pts[:-1], pts[1:]], axis=1)
        self.seg_colors = [STATE_COLORS[f.monitor_state] for f in frames[1:]]
        self.trail = LineCollection([], linewidths=2.4, capstyle="round")
        ax.add_collection(self.trail)
        (self.drone_halo,) = ax.plot([], [], "o", markersize=20, alpha=0.25)
        (self.drone,) = ax.plot([], [], marker="X", markersize=11, color=TEXT,
                                markeredgecolor=BG, linestyle="none")
        (self.ghost,) = ax.plot([], [], marker="o", markersize=11, markerfacecolor="none",
                                markeredgecolor=RED, markeredgewidth=2, linestyle="none")
        self.ghost_label = ax.text(0, 0, "", color=RED, fontsize=8, fontweight="bold")
        self.drone_label = ax.text(0, 0, "", color=TEXT, fontsize=8)
        handles, _ = ax.get_legend_handles_labels()
        for st in (MonitorState.MONITORING, MonitorState.WARNING, MonitorState.RTL,
                   MonitorState.LAND, MonitorState.BLIND):
            handles.append(Line2D([], [], color=STATE_COLORS[st], linewidth=2.4,
                                  label=f"Trail: {st.value.upper()}"))
        leg = ax.legend(handles=handles, loc="lower left", fontsize=7, facecolor=PANEL,
                        edgecolor=GRID, labelcolor=MUTED, ncol=2)
        leg.set_zorder(1)
        self.map_ax = ax

        # Altitude
        ax = fig.add_axes((0.565, 0.66, 0.415, 0.21))
        _style_axes(ax, "Altitude (m, relative)")
        ax.set_xlim(0, t_end)
        ax.set_ylim(0, max(70.0, float(self.alt.max()) + 8))
        if r.altitude_ceiling_m:
            ax.axhline(r.altitude_ceiling_m, color=RED, linewidth=1.2)
            ax.text(t_end, r.altitude_ceiling_m + 1.5, f"ceiling {r.altitude_ceiling_m:.0f} m ",
                    ha="right", color=RED, fontsize=7.5)
        if r.altitude_warn_m:
            ax.axhline(r.altitude_warn_m, color=AMBER, linewidth=0.9, linestyle="--")
        (self.alt_line,) = ax.plot([], [], color=BLUE, linewidth=1.8)
        ax.tick_params(labelbottom=False)
        self.alt_ax = ax

        # Battery
        ax = fig.add_axes((0.565, 0.445, 0.415, 0.17))
        _style_axes(ax, "Battery (%)")
        ax.set_xlim(0, t_end)
        ax.set_ylim(0, 100)
        for _rule_id, pct, action in r.battery_thresholds:
            color = RED if action == "land" else ORANGE
            ax.axhline(pct, color=color, linewidth=1.0, linestyle="--")
            ax.text(t_end, pct + 2, f"{action.upper()} {pct:.0f}% ", ha="right", color=color,
                    fontsize=7.5)
        (self.bat_line,) = ax.plot([], [], color=GREEN, linewidth=1.8)
        ax.set_xlabel("Simulation time (s)", fontsize=8)
        self.bat_ax = ax

        self.action_lines = []
        for act in r.actions:
            color = RED if act.action == "land" else ORANGE
            lines = [a.axvline(act.act_t, color=color, linewidth=1.2, alpha=0.9, visible=False)
                     for a in (self.alt_ax, self.bat_ax)]
            self.action_lines.append((act.act_t, lines))

        # Status panel
        ax = fig.add_axes((0.565, 0.025, 0.415, 0.335))
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.02",
                                    facecolor=PANEL, edgecolor=GRID, transform=ax.transAxes))
        self.badge = FancyBboxPatch((0.025, 0.73), 0.30, 0.21,
                                    boxstyle="round,pad=0,rounding_size=0.03", facecolor=GREEN,
                                    edgecolor="none")
        ax.add_patch(self.badge)
        ax.text(0.04, 0.905, "MONITOR", color=BG, fontsize=7, fontweight="bold", va="center")
        self.badge_text = ax.text(0.175, 0.80, "", color=BG, fontsize=15, fontweight="bold",
                                  ha="center", va="center")
        self.status_text = ax.text(0.36, 0.935, "", color=TEXT, fontsize=8.5, va="top",
                                   family="DejaVu Sans Mono", linespacing=1.45)
        self.rule_texts = []
        enabled = [rule for rule in r.config.rules if rule.enabled]
        for i, rule in enumerate(enabled):
            col, row = i % 2, i // 2
            x, y = 0.03 + col * 0.49, 0.62 - row * 0.085
            dot = ax.text(x, y, "●", color=GREEN, fontsize=9, va="center")
            ax.text(x + 0.035, y, rule.id, color=MUTED, fontsize=7.8, va="center",
                    family="DejaVu Sans Mono")
            self.rule_texts.append((rule.id, dot))
        y0 = 0.62 - ((len(enabled) + 1) // 2) * 0.085 - 0.02
        ax.plot([0.03, 0.97], [y0 + 0.03, y0 + 0.03], color=GRID, linewidth=0.8)
        self.event_texts = [
            ax.text(0.03, y0 - i * 0.068, "", fontsize=7.6, va="top", family="DejaVu Sans Mono")
            for i in range(max(1, int((y0 - 0.02) / 0.068)))
        ]

    # -- per-frame update -------------------------------------------------
    def update(self, i: int) -> None:
        r = self.result
        f = r.frames[i]
        state_color = STATE_COLORS[f.monitor_state]

        self.trail.set_segments(self.segments[:i])
        self.trail.set_color(self.seg_colors[:i])
        self.drone.set_data([f.east], [f.north])
        self.drone_halo.set_data([f.east], [f.north])
        self.drone_halo.set_color(state_color)
        self.drone_label.set_position((f.east + 5, f.north + 4))
        self.drone_label.set_text(f"{f.alt:.0f} m")

        ghost = None
        if f.seen_north is not None and f.seen_east is not None:
            offset = np.hypot(f.seen_north - f.north, f.seen_east - f.east)
            if not f.link_up:
                ghost = "last known (BLIND)"
            elif f.position_age_s > 0.5:
                ghost = f"frozen fix ({f.position_age_s:.1f}s old)"
            elif offset > 3:
                ghost = "GPS spike"
        if ghost:
            self.ghost.set_data([f.seen_east], [f.seen_north])
            self.ghost_label.set_position((f.seen_east + 5, f.seen_north - 8))
            self.ghost_label.set_text(f"monitor sees: {ghost}")
        else:
            self.ghost.set_data([], [])
            self.ghost_label.set_text("")

        self.alt_line.set_data(self.t[: i + 1], self.alt[: i + 1])
        self.bat_line.set_data(self.t[: i + 1], self.bat[: i + 1])
        bat_color = RED if f.battery_pct < 12 else ORANGE if f.battery_pct < 25 else GREEN
        self.bat_line.set_color(bat_color)
        for t_act, lines in self.action_lines:
            for line in lines:
                line.set_visible(f.t >= t_act)

        self.badge.set_facecolor(state_color)
        self.badge_text.set_text(f.monitor_state.value.upper())
        link = "UP" if f.link_up else "DOWN"
        self.status_text.set_text(
            f"t = {f.t:6.1f} s     armed: {'yes' if f.armed else 'no'}\n"
            f"mode: {f.flight_mode.value.upper():<17} link: {link}\n"
            f"alt {f.alt:5.1f} m   batt {f.battery_pct:5.1f} %   pos age {f.position_age_s:4.1f} s"
        )
        for rule_id, dot in self.rule_texts:
            if rule_id in f.violations:
                dot.set_color(RED)
            elif rule_id in f.warnings:
                dot.set_color(AMBER)
            else:
                dot.set_color(GREEN)

        recent = [ev for ev in r.events if ev.t <= f.t][-len(self.event_texts):]
        padded = list(reversed(recent)) + [None] * (len(self.event_texts) - len(recent))
        for text, ev in zip(self.event_texts, padded, strict=True):
            if ev is None:
                text.set_text("")
                continue
            msg = ev.message if len(ev.message) <= 60 else ev.message[:57] + "..."
            text.set_text(f"{ev.t:6.1f}s  {msg}")
            text.set_color(EVENT_COLORS.get(ev.kind, MUTED))

    def rgba(self) -> bytes:
        self.fig.canvas.draw()
        return bytes(self.fig.canvas.buffer_rgba())

    def save(self, path: Path | str) -> None:
        self.fig.savefig(path, dpi=DPI, facecolor=BG)

    def close(self) -> None:
        plt.close(self.fig)


def title_card(lines: Sequence[tuple[str, float, str]], subtitle: str = "") -> Figure:
    """A plain card with (text, fontsize, color) lines, centred."""
    fig = plt.figure(figsize=(WIDTH_PX / DPI, HEIGHT_PX / DPI), dpi=DPI)
    y = 0.5 + 0.045 * (len(lines) - 1)
    for text, size, color in lines:
        fig.text(0.5, y, text, ha="center", va="center", fontsize=size, color=color,
                 fontweight="bold" if size >= 18 else "normal")
        y -= 0.04 + size / 300
    if subtitle:
        fig.text(0.5, 0.07, subtitle, ha="center", color=MUTED, fontsize=9)
    return fig


def _fig_rgba(fig: Figure) -> bytes:
    fig.canvas.draw()
    return bytes(fig.canvas.buffer_rgba())


class VideoWriter:
    """Streams raw RGBA frames into ffmpeg (H.264 MP4)."""

    def __init__(self, path: Path | str, fps: int = 30) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg not found on PATH; install it to render videos")
        self.fps = fps
        self._proc = subprocess.Popen(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
             "-s", f"{WIDTH_PX}x{HEIGHT_PX}", "-r", str(fps), "-i", "-",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24", "-preset", "medium",
             "-movflags", "+faststart", str(path)],
            stdin=subprocess.PIPE,
        )

    def write(self, rgba: bytes, repeat: int = 1) -> None:
        assert self._proc.stdin is not None
        for _ in range(repeat):
            self._proc.stdin.write(rgba)

    def close(self) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.close()
        if self._proc.wait() != 0:
            raise RuntimeError("ffmpeg failed while encoding video")


def scenario_card(result: SimResult) -> Figure:
    s = result.scenario
    return title_card(
        [
            (f"Scenario {s.id}", 14, BLUE),
            (s.name, 28, TEXT),
            (s.description, 11, MUTED),
            (f"Expected: {s.expected}", 11, AMBER),
        ],
        subtitle="Simulated with the real RuleEngine and ActionExecutor  ·  rules/example.yaml",
    )


def write_scenario(
    writer: VideoWriter, result: SimResult, with_card: bool = True, hold_s: float = 2.0
) -> None:
    """Append one scenario (title card, flight, final hold) to a video."""
    fps = writer.fps
    if with_card:
        card = scenario_card(result)
        writer.write(_fig_rgba(card), repeat=int(2.5 * fps))
        plt.close(card)

    dash = Dashboard(result)
    dt = result.frames[1].t - result.frames[0].t
    step = result.scenario.playback_speed / fps / dt
    pos = 0.0
    last = len(result.frames) - 1
    while True:
        i = min(int(round(pos)), last)
        dash.update(i)
        writer.write(dash.rgba())
        if i >= last:
            break
        pos += step
    writer.write(dash.rgba(), repeat=int(hold_s * fps))
    dash.close()


def render_video(result: SimResult, path: Path | str, fps: int = 30) -> Path:
    """Render a single scenario to MP4."""
    writer = VideoWriter(path, fps)
    write_scenario(writer, result)
    writer.close()
    return Path(path)


def intro_card(n_scenarios: int) -> Figure:
    return title_card(
        [
            ("Drone Failsafe Runtime Monitor", 30, TEXT),
            ("An independent safety layer for PX4 drones", 14, MUTED),
            ("telemetry  →  YAML safety rules  →  debounce / escalation  →  RTL / LAND", 12, BLUE),
            (f"{n_scenarios} simulated scenarios: geofence · altitude · battery · stale "
             "telemetry · link loss · GPS spike · unconfirmed command", 10, MUTED),
        ],
        subtitle="Educational simulation only - not certified flight software",
    )


def render_card(fig: Figure, path: Path | str, seconds: float, fps: int = 30) -> Path:
    """Render a static card as a short MP4 clip."""
    writer = VideoWriter(path, fps)
    writer.write(_fig_rgba(fig), repeat=int(seconds * fps))
    writer.close()
    plt.close(fig)
    return Path(path)


def concat_videos(clips: Sequence[Path | str], path: Path | str) -> Path:
    """Join MP4 clips that share the same encoding settings, without re-encoding."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH")
    path = Path(path)
    listing = path.with_suffix(".txt")
    listing.write_text("".join(f"file '{Path(c).resolve()}'\n" for c in clips))
    try:
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(path)],
                       check=True)
    finally:
        listing.unlink(missing_ok=True)
    return path


def render_demo(results: Iterable[SimResult], path: Path | str, fps: int = 30) -> Path:
    """Render an intro card, every scenario and a summary card into one MP4."""
    results = list(results)
    writer = VideoWriter(path, fps)
    intro = intro_card(len(results))
    writer.write(_fig_rgba(intro), repeat=int(3.5 * fps))
    plt.close(intro)
    for result in results:
        write_scenario(writer, result)
    summary = summary_card(results)
    writer.write(_fig_rgba(summary), repeat=int(5 * fps))
    plt.close(summary)
    writer.close()
    return Path(path)


def summary_card(results: Sequence[SimResult]) -> Figure:
    """A table of every scenario and what the monitor did."""
    fig = plt.figure(figsize=(WIDTH_PX / DPI, HEIGHT_PX / DPI), dpi=DPI)
    fig.text(0.5, 0.92, "Scenario results", ha="center", fontsize=22, fontweight="bold")
    headers = ["ID", "Scenario", "Monitor response", "Detect (s)", "Final"]
    xs = [0.06, 0.13, 0.42, 0.78, 0.88]
    y = 0.83
    for x, h in zip(xs, headers, strict=True):
        fig.text(x, y, h, color=MUTED, fontsize=10, fontweight="bold")
    for r in results:
        y -= 0.075
        if r.actions:
            response = " → ".join(
                f"{a.action.upper()} ({a.rule_id})" for a in r.actions
            )
            detect = f"{r.actions[0].time_to_detect_s:.1f}"
        else:
            kinds = {e.kind for e in r.events}
            response = (
                "BLIND logged, PX4 failsafe RTL" if "alert" in kinds
                else "Warnings only" if "warning" in kinds
                else "No action (nominal)"
            )
            detect = "-"
        if len(response) > 46:
            response = response[:43] + "..."
        final = r.frames[-1]
        color = {"none": GREEN, "warn": AMBER, "rtl": ORANGE, "land": RED}[r.max_severity]
        row = [r.scenario.id, r.scenario.name, response, detect,
               "landed" if not final.armed else final.flight_mode.value]
        for x, val in zip(xs, row, strict=True):
            fig.text(x, y, val, fontsize=10, color=color if x == xs[2] else TEXT)
    return fig


def save_snapshot(result: SimResult, t: float, path: Path | str) -> Path:
    """Save the dashboard at simulation time t as a PNG."""
    dash = Dashboard(result)
    idx = min(range(len(result.frames)), key=lambda k: abs(result.frames[k].t - t))
    dash.update(idx)
    dash.save(path)
    dash.close()
    return Path(path)


def key_moments(result: SimResult) -> list[tuple[str, float]]:
    """Interesting times to screenshot: the first action, or the first fault/warning."""
    moments: list[tuple[str, float]] = []
    if result.actions:
        moments.append(("action", result.actions[0].act_t + 1.5))
    else:
        for ev in result.events:
            if ev.kind in ("warning", "alert", "violation"):
                # For link loss, wait until PX4's own failsafe has visibly engaged.
                moments.append(("event", ev.t + (10.0 if ev.kind == "alert" else 3.0)))
                break
        f = result.scenario.faults
        if not moments and f.gps_spike_at_s is not None:
            moments.append(("spike", f.gps_spike_at_s))
    moments.append(("final", result.frames[-1].t))
    return moments


def save_overview(results: Sequence[SimResult], path: Path | str) -> Path:
    """Grid of final trajectories for all scenarios."""
    n = len(results)
    cols = 3
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(12.8, 4.0 * rows), dpi=DPI)
    fig.suptitle("Simulated flight paths, coloured by monitor state", fontsize=16,
                 fontweight="bold", color=TEXT, y=0.995)
    for ax, r in zip(np.ravel(axes)[:n], results, strict=True):
        _style_axes(ax, f"{r.scenario.id}  {r.scenario.name}")
        if r.fence_local:
            ax.add_patch(Polygon([(e, n_) for n_, e in r.fence_local], closed=True,
                                 facecolor=GREEN, alpha=0.07, edgecolor=GREEN, linewidth=1.4))
        if r.warn_fence_local:
            ax.add_patch(Polygon([(e, n_) for n_, e in r.warn_fence_local], closed=True,
                                 fill=False, edgecolor=AMBER, linestyle="--", linewidth=0.8))
        pts = np.array([[f.east, f.north] for f in r.frames])
        segs = np.stack([pts[:-1], pts[1:]], axis=1)
        ax.add_collection(LineCollection(
            segs, colors=[STATE_COLORS[f.monitor_state] for f in r.frames[1:]], linewidths=2))
        ax.plot(0, 0, "s", color=BLUE, markersize=6)
        for spike in [f for f in r.frames if f.seen_north is not None
                      and np.hypot(f.seen_north - f.north, f.seen_east - f.east) > 3
                      and f.link_up and f.position_age_s < 0.5]:
            ax.plot(spike.seen_east, spike.seen_north, "o", markerfacecolor="none",
                    markeredgecolor=RED, markersize=8)
        ax.set_xlim(-130, 130)
        ax.set_ylim(-80, 80)
        ax.set_aspect("equal")
        ax.tick_params(labelsize=7)
        label = " → ".join(a.action.upper() for a in r.actions) or (
            "warn only" if r.max_severity == "warn" else "no monitor action")
        ax.text(0.98, 0.04, label, transform=ax.transAxes, ha="right", fontsize=9,
                fontweight="bold",
                color={"none": GREEN, "warn": AMBER, "rtl": ORANGE, "land": RED}[r.max_severity])
    for ax in np.ravel(axes)[n:]:
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return Path(path)


def video_to_gif(
    video: Path | str, gif: Path | str, width: int = 720, fps: int = 10,
    start_s: float = 0.0, duration_s: float | None = None,
) -> Path:
    """Convert (part of) an MP4 into a palette-optimised GIF."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH")
    trim = ["-ss", str(start_s)] + (["-t", str(duration_s)] if duration_s else [])
    vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", *trim, "-i", str(video),
                    "-vf", vf, str(gif)], check=True)
    return Path(gif)
