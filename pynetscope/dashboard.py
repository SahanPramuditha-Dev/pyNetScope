"""Optional Rich dashboard rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def make_sparkline(values: list[float]) -> str:
    if not values:
        return ""
    min_val = min(values)
    max_val = max(values)
    rng = max_val - min_val
    chars = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    result = []
    for val in values:
        if rng == 0:
            idx = 0
        else:
            idx = int((val - min_val) / rng * (len(chars) - 1))
        result.append(chars[idx])
    return "".join(result)


def build_dashboard(snapshot: dict[str, Any], selected_index: int | None = None) -> Any:
    try:
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.table import Table
    except ImportError:
        return None

    table = Table(expand=True)
    table.add_column("Endpoint", overflow="fold")
    table.add_column("Count", justify="right")
    table.add_column("Rate/s", justify="right")
    table.add_column("P50", justify="right")
    table.add_column("P95", justify="right")
    table.add_column("P99", justify="right")
    table.add_column("Success", justify="right")
    table.add_column("Health", justify="right")
    table.add_column("Latency History (Sparkline)", justify="center")

    records = snapshot.get("records", [])
    endpoints = sorted(list(snapshot["metrics"].keys()))

    for idx, endpoint in enumerate(endpoints):
        metrics = snapshot["metrics"][endpoint]
        health = snapshot["health"][endpoint]
        style = "green"
        if health.state == "Degrading":
            style = "yellow"
        elif health.state == "Critical":
            style = "red"

        # Get last 15 latency records for this endpoint
        endpoint_records = [r for r in records if r.endpoint == endpoint]
        endpoint_records = sorted(endpoint_records, key=lambda r: r.timestamp_ms)
        last_latencies = [r.latency_ms for r in endpoint_records[-15:]]
        sparkline = make_sparkline(last_latencies)

        is_selected = (selected_index is not None and idx == selected_index)
        row_style = "bold cyan" if is_selected else None
        endpoint_display = f"> {endpoint}" if is_selected else f"  {endpoint}"

        table.add_row(
            endpoint_display,
            str(metrics.count),
            f"{metrics.request_rate:.2f}",
            f"{metrics.p50_ms:.1f} ms",
            f"{metrics.p95_ms:.1f} ms",
            f"{metrics.p99_ms:.1f} ms",
            f"{metrics.success_ratio:.0%}",
            f"[{style}]{health.state} {health.score}[/]",
            sparkline,
            style=row_style,
        )

    alerts = snapshot.get("alerts", [])
    alert_lines = []
    for alert in alerts[-6:]:
        severity_style = "red" if alert.severity == "critical" else "yellow"
        alert_lines.append(
            f"[{severity_style}][{alert.severity.upper()}][/] "
            f"[cyan]{alert.endpoint}[/]: {alert.message}"
        )

    if not alert_lines:
        alert_lines.append("[dim]No alerts generated yet.[/]")

    alerts_panel = Panel(
        "\n".join(alert_lines),
        title="[bold red]Alert Stream[/]",
        border_style="red" if any(a.severity == "critical" for a in alerts) else "dim",
    )

    if selected_index is not None and 0 <= selected_index < len(endpoints):
        selected_endpoint = endpoints[selected_index]
        endpoint_records = [r for r in records if r.endpoint == selected_endpoint]
        endpoint_records = sorted(endpoint_records, key=lambda r: r.timestamp_ms)
        if endpoint_records:
            last_record = endpoint_records[-1]
            detail_lines = [
                f"[bold]Method/URL:[/] {last_record.method} {last_record.url}",
                (
                    f"[bold]Status Code:[/] {last_record.status_code} | "
                    f"[bold]Latency:[/] {last_record.latency_ms:.2f} ms | "
                    f"[bold]Trace ID:[/] {last_record.trace_id or 'N/A'}"
                ),
            ]
            if last_record.request_headers:
                detail_lines.append(f"[bold]Request Headers:[/] {last_record.request_headers}")
            if last_record.response_headers:
                detail_lines.append(f"[bold]Response Headers:[/] {last_record.response_headers}")
            
            req_body = last_record.metadata.get("request_body")
            resp_body = last_record.metadata.get("response_body")
            if req_body:
                detail_lines.append(f"[bold]Request Body:[/] {req_body}")
            if resp_body:
                detail_lines.append(f"[bold]Response Body:[/] {resp_body}")
            
            other_meta = {
                k: v for k, v in last_record.metadata.items()
                if k not in ("request_body", "response_body", "session")
            }
            if other_meta:
                detail_lines.append(f"[bold]Metadata:[/] {other_meta}")
            
            detail_text = "\n".join(detail_lines)
        else:
            detail_text = "[dim]No records captured for this endpoint yet.[/]"
        
        detail_panel = Panel(
            detail_text,
            title=f"[bold cyan]Inspection: {selected_endpoint}[/]",
            border_style="cyan",
        )
    else:
        detail_panel = Panel(
            "[dim]No endpoint selected. Use Up/Down or j/k to select.[/]",
            title="[bold cyan]Inspection[/]",
            border_style="cyan",
        )

    layout = Layout()
    layout.split_column(
        Layout(Panel(table, title="[bold green]pyNetScope Endpoint Health[/]", border_style="green"), name="main"),
        Layout(name="bottom", size=12),
    )
    layout["bottom"].split_row(
        Layout(alerts_panel, name="alerts"),
        Layout(detail_panel, name="details"),
    )
    return layout


def render_dashboard(snapshot: dict[str, Any], selected_index: int | None = None) -> bool:
    """Render a Rich dashboard once.

    Returns False when Rich is not installed.
    """

    layout = build_dashboard(snapshot, selected_index)
    if layout is None:
        return False
    from rich.console import Console

    Console().print(layout)
    return True


@dataclass
class LiveDashboard:
    refresh_per_second: float = 4.0

    def __post_init__(self) -> None:
        try:
            from rich.live import Live
        except ImportError:
            self._live = None
            return
        self._live = Live(
            build_dashboard({"metrics": {}, "health": {}, "records": [], "alerts": []}),
            refresh_per_second=self.refresh_per_second,
        )
        self.selected_index = 0
        self.running = True
        self._endpoints_count = 0
        import threading
        self.thread = threading.Thread(target=self._key_listener, daemon=True)
        self.thread.start()

    @property
    def available(self) -> bool:
        return self._live is not None

    def __enter__(self) -> LiveDashboard:
        if self._live:
            self._live.__enter__()
        return self

    def update(self, snapshot: dict[str, Any]) -> bool:
        if not self._live:
            return False
        endpoints = sorted(list(snapshot.get("metrics", {}).keys()))
        self._endpoints_count = len(endpoints)
        if self._endpoints_count > 0:
            self.selected_index = max(0, min(self.selected_index, self._endpoints_count - 1))
        self._live.update(build_dashboard(snapshot, selected_index=self.selected_index))
        return True

    def _move_selection(self, delta: int) -> None:
        if self._endpoints_count > 0:
            self.selected_index = max(0, min(self.selected_index + delta, self._endpoints_count - 1))

    def _key_listener(self) -> None:
        import sys
        import time
        if not sys.stdin.isatty():
            return

        is_windows = sys.platform.startswith("win")
        
        if is_windows:
            try:
                import msvcrt
                while self.running:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b'\x00', b'\xe0'):
                            ch2 = msvcrt.getch()
                            if ch2 == b'H':  # Up
                                self._move_selection(-1)
                            elif ch2 == b'P':  # Down
                                self._move_selection(1)
                        elif ch in (b'w', b'k', b'W', b'K'):
                            self._move_selection(-1)
                        elif ch in (b's', b'j', b'S', b'J'):
                            self._move_selection(1)
                    else:
                        time.sleep(0.05)
            except Exception:
                pass
        else:
            try:
                import select
                import termios  # type: ignore
                import tty  # type: ignore
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)  # type: ignore
                try:
                    tty.setraw(fd)  # type: ignore
                    while self.running:
                        r, _, _ = select.select([sys.stdin], [], [], 0.1)
                        if r:
                            uch = sys.stdin.read(1)
                            if uch == '\x1b':
                                r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                                if r2:
                                    uch2 = sys.stdin.read(1)
                                    if uch2 == '[':
                                        r3, _, _ = select.select([sys.stdin], [], [], 0.05)
                                        if r3:
                                            uch3 = sys.stdin.read(1)
                                            if uch3 == 'A':  # Up
                                                self._move_selection(-1)
                                            elif uch3 == 'B':  # Down
                                                self._move_selection(1)
                            elif uch in ('w', 'k', 'W', 'K'):
                                self._move_selection(-1)
                            elif uch in ('s', 'j', 'S', 'J'):
                                self._move_selection(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)  # type: ignore
            except Exception:
                pass

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.running = False
        if self._live:
            self._live.__exit__(exc_type, exc, traceback)
