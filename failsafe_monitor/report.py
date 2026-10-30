"""
After-flight report generation (F-09).

Builds a Markdown or HTML summary from JSONL event logs: the monitor state
timeline, every violation, and every action with time-to-detect and time-to-act.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from .logging import Event, EventType, load_events


@dataclass
class ActionSummary:
    """One failsafe intervention reconstructed from the log."""

    rule_id: str
    action: str
    onset_s: float | None
    requested_s: float
    completed_s: float | None
    success: bool
    retry_count: int
    note: str = ""

    @property
    def time_to_detect_s(self) -> float | None:
        if self.onset_s is None:
            return None
        return self.requested_s - self.onset_s

    @property
    def time_to_act_s(self) -> float | None:
        if self.completed_s is None:
            return None
        start = self.onset_s if self.onset_s is not None else self.requested_s
        return self.completed_s - start


@dataclass
class FlightReport:
    """Structured content of an after-flight report."""

    title: str
    start_s: float
    end_s: float
    events: list[Event]
    actions: list[ActionSummary]

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def rel(self, ts: float) -> float:
        return ts - self.start_s

    @property
    def violations(self) -> list[Event]:
        return [e for e in self.events if e.event_type == EventType.VIOLATION]

    @property
    def warnings(self) -> list[Event]:
        return [e for e in self.events if e.event_type == EventType.WARNING]

    @property
    def state_changes(self) -> list[Event]:
        return [e for e in self.events if e.event_type == EventType.STATE_CHANGE]

    @property
    def verdict(self) -> str:
        if any(not a.success for a in self.actions):
            return "ACTION FAILED"
        if self.actions:
            return "INTERVENED: " + ", ".join(a.action.upper() for a in self.actions)
        if any(e.event_type == EventType.DISCONNECTED for e in self.events):
            return "BLIND PERIOD (no monitor action possible)"
        if self.violations or self.warnings:
            return "WARNINGS ONLY"
        return "NOMINAL"


def build_report(events: list[Event], title: str = "After-flight report") -> FlightReport:
    """Reconstruct actions and timings from a list of events."""
    if not events:
        return FlightReport(title=title, start_s=0.0, end_s=0.0, events=[], actions=[])

    actions: list[ActionSummary] = []
    pending: ActionSummary | None = None
    last_onset: dict[str, float] = {}

    for e in events:
        if e.event_type == EventType.VIOLATION and e.rule_id and "onset_s" in e.data:
            last_onset[e.rule_id] = e.data["onset_s"]
        elif e.event_type == EventType.ACTION_REQUESTED:
            pending = ActionSummary(
                rule_id=e.rule_id or "?",
                action=e.data.get("action", e.severity or "?"),
                onset_s=last_onset.get(e.rule_id or ""),
                requested_s=e.timestamp_s,
                completed_s=None,
                success=False,
                retry_count=0,
            )
            actions.append(pending)
        elif e.event_type in (EventType.ACTION_CONFIRMED, EventType.ACTION_FAILED) and pending:
            pending.success = bool(e.data.get("success", False))
            pending.completed_s = e.timestamp_s + float(e.data.get("latency_s", 0.0))
            pending.retry_count = int(e.data.get("retry_count", 0))
            pending.action = e.data.get("action", pending.action)
            pending.note = e.data.get("note", "") or ""
            if "onset_s" in e.data:
                pending.onset_s = e.data["onset_s"]
            pending = None

    actions = [a for a in actions if not a.note.startswith("Already in")]
    return FlightReport(
        title=title,
        start_s=events[0].timestamp_s,
        end_s=events[-1].timestamp_s,
        events=events,
        actions=actions,
    )


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def to_markdown(report: FlightReport) -> str:
    """Render the report as Markdown."""
    lines = [
        f"# {report.title}",
        "",
        f"**Verdict:** {report.verdict}  ",
        f"**Duration:** {report.duration_s:.1f} s · **Events:** {len(report.events)} · "
        f"**Violations:** {len(report.violations)} · **Actions:** {len(report.actions)}",
        "",
        "## Actions",
        "",
    ]
    if report.actions:
        lines += [
            "| Rule | Action | Onset (s) | Commanded (s) | Time to detect (s) | Time to act (s) | Result | Note |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for a in report.actions:
            onset = report.rel(a.onset_s) if a.onset_s is not None else None
            lines.append(
                f"| {a.rule_id} | {a.action.upper()} | {_fmt(onset)} | "
                f"{report.rel(a.requested_s):.2f} | {_fmt(a.time_to_detect_s)} | "
                f"{_fmt(a.time_to_act_s)} | {'confirmed' if a.success else 'FAILED'} | "
                f"{a.note} |"
            )
    else:
        lines.append("No failsafe action was commanded.")

    lines += ["", "## Monitor state timeline", ""]
    for e in report.state_changes:
        lines.append(f"- `{report.rel(e.timestamp_s):7.2f} s` {e.message}")
    if not report.state_changes:
        lines.append("- No state changes.")

    lines += ["", "## Violations and warnings", ""]
    notable = [e for e in report.events if e.event_type in (
        EventType.VIOLATION, EventType.WARNING, EventType.DISCONNECTED, EventType.ERROR)]
    if notable:
        lines += ["| Time (s) | Type | Rule | Severity | Message |", "|---|---|---|---|---|"]
        for e in notable:
            lines.append(
                f"| {report.rel(e.timestamp_s):.2f} | {e.event_type.value} | "
                f"{e.rule_id or '-'} | {e.severity or '-'} | {e.message} |"
            )
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


_CSS = """
body{font-family:Inter,system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:24px;
max-width:1000px;margin:auto;line-height:1.5}
h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin-top:28px;color:#8b949e;
text-transform:uppercase;letter-spacing:.05em}
.verdict{display:inline-block;padding:4px 10px;border-radius:6px;font-weight:600;background:#21262d}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid #30363d;
padding:6px 8px;text-align:left}th{color:#8b949e;font-weight:600}
code{font-family:ui-monospace,monospace;color:#58a6ff}.muted{color:#8b949e}
.overflow{overflow-x:auto}
"""


def to_html(report: FlightReport) -> str:
    """Render the report as a standalone HTML page."""
    esc = html.escape
    rows = "".join(
        f"<tr><td>{esc(a.rule_id)}</td><td><b>{esc(a.action.upper())}</b></td>"
        f"<td>{_fmt(report.rel(a.onset_s) if a.onset_s is not None else None)}</td>"
        f"<td>{report.rel(a.requested_s):.2f}</td><td>{_fmt(a.time_to_detect_s)}</td>"
        f"<td>{_fmt(a.time_to_act_s)}</td><td>{'confirmed' if a.success else 'FAILED'}</td>"
        f"<td>{esc(a.note)}</td></tr>"
        for a in report.actions
    )
    actions_html = (
        "<div class=overflow><table><tr><th>Rule</th><th>Action</th><th>Onset (s)</th>"
        "<th>Commanded (s)</th><th>Detect (s)</th><th>Act (s)</th><th>Result</th><th>Note</th></tr>"
        f"{rows}</table></div>"
        if report.actions else "<p class=muted>No failsafe action was commanded.</p>"
    )
    timeline = "".join(
        f"<li><code>{report.rel(e.timestamp_s):.2f} s</code> {esc(e.message)}</li>"
        for e in report.state_changes
    )
    notable = "".join(
        f"<tr><td>{report.rel(e.timestamp_s):.2f}</td><td>{esc(e.event_type.value)}</td>"
        f"<td>{esc(e.rule_id or '-')}</td><td>{esc(e.severity or '-')}</td>"
        f"<td>{esc(e.message)}</td></tr>"
        for e in report.events
        if e.event_type in (EventType.VIOLATION, EventType.WARNING, EventType.DISCONNECTED,
                            EventType.ERROR)
    )
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{esc(report.title)}</title><style>{_CSS}</style></head><body>
<h1>{esc(report.title)}</h1>
<p><span class=verdict>{esc(report.verdict)}</span>
<span class=muted>&nbsp; {report.duration_s:.1f} s · {len(report.events)} events ·
{len(report.violations)} violations · {len(report.actions)} actions</span></p>
<h2>Actions</h2>{actions_html}
<h2>Monitor state timeline</h2><ul>{timeline or '<li>No state changes.</li>'}</ul>
<h2>Violations and warnings</h2>
<div class=overflow><table><tr><th>Time (s)</th><th>Type</th><th>Rule</th><th>Severity</th>
<th>Message</th></tr>{notable}</table></div>
</body></html>
"""


def write_report(
    log_file: Path | str, out: Path | str | None = None, title: str | None = None
) -> Path:
    """Write a report for a JSONL log; the format follows the output suffix (.md or .html)."""
    log_file = Path(log_file)
    out = Path(out) if out else log_file.with_suffix(".report.md")
    report = build_report(load_events(log_file), title or f"After-flight report: {log_file.stem}")
    content = to_html(report) if out.suffix.lower() in (".html", ".htm") else to_markdown(report)
    out.write_text(content, encoding="utf-8")
    return out
