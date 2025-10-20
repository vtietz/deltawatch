#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Event-driven directory watcher using OS filesystem events (watchdog).
No scanning needed - the OS notifies us instantly when files/directories change!

Windows: Uses ReadDirectoryChangesW API
Linux: Uses inotify
macOS: Uses FSEvents
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict, deque
from typing import Dict, Set, Deque, Optional, List

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout


def human_bytes(n: int) -> str:
    """Convert bytes to human readable format"""
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    f = float(n)
    for u in units:
        if f < step:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= step
    return f"{f:.1f} EB"


def get_dir_size(path: str) -> int:
    """Get directory size (files only, not recursive)"""
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except (PermissionError, FileNotFoundError, OSError):
                    continue
    except (PermissionError, FileNotFoundError, OSError):
        pass
    return total


class DirectoryChangeTracker(FileSystemEventHandler):
    """Tracks filesystem changes and aggregates them by directory with size tracking"""

    def __init__(
        self,
        console: Console,
        max_history: int = 1000,
        exclude_patterns: Optional[List[str]] = None,
    ):
        super().__init__()
        self.console = console
        self.max_history = max_history
        self.exclude_patterns = exclude_patterns or []

        # Track changes by directory
        self.dir_changes: Dict[str, int] = defaultdict(int)  # dir -> change count
        self.dir_last_change: Dict[str, datetime] = {}  # dir -> last change time
        self.dir_sizes: Dict[str, int] = {}  # dir -> current size
        self.dir_initial_sizes: Dict[str, int] = {}  # dir -> size when first seen
        self.dir_size_deltas: Dict[str, int] = {}  # dir -> cumulative size change

        # Track individual file sizes to calculate proper deltas
        self.file_sizes: Dict[str, int] = {}  # file_path -> last known size

        # Recent events queue
        self.recent_events: Deque[tuple] = deque(
            maxlen=max_history
        )  # (time, type, path, size_delta)

        # Statistics
        self.total_events = 0
        self.event_counts = defaultdict(int)  # event_type -> count
        self.excluded_events = 0  # Count of excluded events
        self.start_time = datetime.now()

    def _is_excluded(self, path: str) -> bool:
        """Check if path matches any exclude pattern"""
        import fnmatch

        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(path.lower(), pattern.lower()):
                return True
        return False

    def _record_change(self, event_type: str, path: str):
        """Record a filesystem change and calculate size delta"""
        now = datetime.now()

        # Ensure path is string
        path = str(path) if isinstance(path, bytes) else path

        # Check if excluded
        if self._is_excluded(path):
            self.excluded_events += 1
            return

        # Determine the directory and file
        is_dir = os.path.isdir(path)
        if is_dir:
            directory = path
            file_path = None
        else:
            directory = os.path.dirname(path)
            file_path = path

        # Calculate size delta
        size_delta = 0
        if file_path:
            try:
                if event_type == "created":
                    # New file - full size is the delta
                    if os.path.exists(file_path):
                        new_size = os.path.getsize(file_path)
                        size_delta = new_size
                        self.file_sizes[file_path] = new_size

                elif event_type == "modified":
                    # Modified file - calculate actual difference
                    if os.path.exists(file_path):
                        new_size = os.path.getsize(file_path)
                        old_size = self.file_sizes.get(
                            file_path, new_size
                        )  # If unknown, assume no change
                        size_delta = new_size - old_size
                        self.file_sizes[file_path] = new_size

                elif event_type == "deleted":
                    # Deleted file - negative delta
                    old_size = self.file_sizes.get(file_path, 0)
                    size_delta = -old_size
                    if file_path in self.file_sizes:
                        del self.file_sizes[file_path]

                elif event_type == "moved":
                    # File moved away - treat as deletion
                    old_size = self.file_sizes.get(file_path, 0)
                    size_delta = -old_size
                    if file_path in self.file_sizes:
                        del self.file_sizes[file_path]

                elif event_type == "moved_to":
                    # File moved here - treat as creation
                    if os.path.exists(file_path):
                        new_size = os.path.getsize(file_path)
                        size_delta = new_size
                        self.file_sizes[file_path] = new_size

            except (PermissionError, FileNotFoundError, OSError):
                # If we can't access the file, ignore size delta
                size_delta = 0

        # Update statistics
        self.total_events += 1
        self.event_counts[event_type] += 1
        self.dir_changes[directory] += 1
        self.dir_last_change[directory] = now

        # Track cumulative size delta for this directory
        if directory not in self.dir_size_deltas:
            self.dir_size_deltas[directory] = 0
        self.dir_size_deltas[directory] += size_delta

        # Update directory size
        if os.path.isdir(directory):
            try:
                current_size = get_dir_size(directory)
                if directory not in self.dir_initial_sizes:
                    self.dir_initial_sizes[directory] = current_size
                self.dir_sizes[directory] = current_size
            except:
                pass

        # Add to recent events with size delta
        self.recent_events.append((now, event_type, path, size_delta))

    def on_created(self, event: FileSystemEvent):
        self._record_change("created", str(event.src_path))

    def on_deleted(self, event: FileSystemEvent):
        self._record_change("deleted", str(event.src_path))

    def on_modified(self, event: FileSystemEvent):
        self._record_change("modified", str(event.src_path))

    def on_moved(self, event: FileSystemEvent):
        self._record_change("moved", str(event.src_path))
        if hasattr(event, "dest_path"):
            self._record_change("moved_to", str(event.dest_path))

    def get_changed_dirs(self, since_minutes: Optional[int] = None) -> list:
        """Get directories that changed, sorted by absolute size delta (biggest changes first)"""
        if since_minutes is None:
            # Return all, sorted by absolute size delta
            items = [
                (
                    d,
                    self.dir_changes[d],
                    self.dir_last_change[d],
                    self.dir_sizes.get(d, 0),
                    self.dir_size_deltas.get(d, 0),
                )
                for d in self.dir_changes.keys()
            ]
            # Sort by absolute value of size delta (biggest changes on top)
            items.sort(key=lambda x: abs(x[4]), reverse=True)
            return items

        # Filter by time window
        cutoff = datetime.now() - timedelta(minutes=since_minutes)
        items = [
            (
                d,
                self.dir_changes[d],
                self.dir_last_change[d],
                self.dir_sizes.get(d, 0),
                self.dir_size_deltas.get(d, 0),
            )
            for d in self.dir_changes.keys()
            if self.dir_last_change[d] >= cutoff
        ]
        # Sort by absolute value of size delta
        items.sort(key=lambda x: abs(x[4]), reverse=True)
        return items

    def get_recent_events(self, count: int = 20) -> list:
        """Get the most recent events"""
        return list(self.recent_events)[-count:]


def create_display(tracker: DirectoryChangeTracker, args) -> Panel:
    """Create the display panel"""

    # Header with statistics
    now = datetime.now()
    runtime = now - tracker.start_time
    runtime_str = f"{int(runtime.total_seconds())}s"

    # Time window display
    if args.minutes is None:
        window_str = "since start"
    else:
        window_str = f"last {args.minutes} min"

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold cyan")
    header.add_column()

    header.add_row("Status:", "[green]Watching for changes...[/green]")
    header.add_row("Runtime:", runtime_str)
    header.add_row("Total Events:", str(tracker.total_events))
    if tracker.excluded_events > 0:
        header.add_row("Excluded Events:", f"[dim]{tracker.excluded_events}[/dim]")
    header.add_row("Time Window:", window_str)

    # Event type breakdown
    if tracker.event_counts:
        event_summary = ", ".join(
            [f"{k}: {v}" for k, v in tracker.event_counts.items()]
        )
        header.add_row("Event Types:", event_summary)

    # Recent events table (only if --show-events flag is used)
    recent_table = None
    if args.show_events:
        recent_table = Table(
            title=f"🔥 Recent Events (last {args.event_count})",
            show_header=True,
            header_style="bold magenta",
        )
        recent_table.add_column("Time", style="dim", width=8)
        recent_table.add_column("Type", width=10)
        recent_table.add_column("Size Δ", justify="right", width=10)
        recent_table.add_column("Path", overflow="fold")

        for event_time, event_type, path, size_delta in tracker.get_recent_events(
            args.event_count
        ):
            time_str = event_time.strftime("%H:%M:%S")

            # Color code by event type
            if event_type == "created":
                type_str = "[green]created[/green]"
            elif event_type == "deleted":
                type_str = "[red]deleted[/red]"
            elif event_type == "modified":
                type_str = "[yellow]modified[/yellow]"
            elif event_type in ("moved", "moved_to"):
                type_str = "[blue]moved[/blue]"
            else:
                type_str = event_type

            # Format size delta
            if size_delta > 0:
                delta_str = f"[green]+{human_bytes(size_delta)}[/green]"
            elif size_delta < 0:
                delta_str = f"[red]{human_bytes(size_delta)}[/red]"
            else:
                delta_str = "[dim]-[/dim]"

            recent_table.add_row(time_str, type_str, delta_str, path)

    # Changed directories table
    changed_dirs = tracker.get_changed_dirs(args.minutes)[: args.top]

    # Title for directories table
    if args.minutes is None:
        dirs_title = f"📁 Top {args.top} Directories by Size Change (since start)"
    else:
        dirs_title = (
            f"📁 Top {args.top} Directories by Size Change (last {args.minutes} min)"
        )

    dirs_table = Table(title=dirs_title, show_header=True, header_style="bold cyan")
    dirs_table.add_column("Size Δ", justify="right", style="bold yellow", width=12)
    dirs_table.add_column("Events", justify="right", style="dim", width=7)
    dirs_table.add_column("Current Size", justify="right", style="cyan", width=12)
    dirs_table.add_column("Last Change", style="dim", width=8)
    dirs_table.add_column("Directory", overflow="fold")

    for directory, count, last_change, current_size, size_delta in changed_dirs:
        ago = now - last_change
        if ago.total_seconds() < 60:
            ago_str = f"{int(ago.total_seconds())}s"
        else:
            ago_str = f"{int(ago.total_seconds() / 60)}m"

        # Format size delta with color
        if size_delta > 0:
            delta_str = f"[green]+{human_bytes(size_delta)}[/green]"
        elif size_delta < 0:
            delta_str = f"[red]{human_bytes(abs(size_delta))}[/red]"
        else:
            delta_str = "[dim]0 B[/dim]"

        dirs_table.add_row(
            delta_str,
            str(count),
            human_bytes(current_size) if current_size > 0 else "-",
            ago_str,
            directory,
        )

    # Combine everything
    layout = Table.grid(padding=(1, 0))
    layout.add_row(header)
    if recent_table:  # Only show if --show-events was used
        layout.add_row(recent_table)
    layout.add_row(dirs_table)

    return Panel(layout, title="[bold]Delta Watch[/bold]", border_style="blue")


def main():
    parser = argparse.ArgumentParser(
        description="Event-driven directory watcher - uses OS filesystem events, no scanning needed!"
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Directory to watch (Default: current directory)",
    )
    parser.add_argument(
        "-m",
        "--minutes",
        type=int,
        default=None,
        help="Time window for filtering in minutes (Default: None = show all since start)",
    )
    parser.add_argument(
        "-r",
        "--refresh",
        type=float,
        default=1.0,
        help="Display refresh interval in seconds (Default: 1.0)",
    )
    parser.add_argument(
        "-t",
        "--top",
        type=int,
        default=10,
        help="Number of top directories to show (Default: 10)",
    )
    parser.add_argument(
        "--show-events",
        action="store_true",
        help="Show recent events table (hidden by default)",
    )
    parser.add_argument(
        "--event-count",
        type=int,
        default=20,
        help="Number of recent events to show when --show-events is used (Default: 20)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Watch subdirectories recursively (can be resource-intensive for large trees)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude directories matching this pattern (can be used multiple times). "
        "Example: --exclude '*Docker*' --exclude '*WSL*'",
    )
    parser.add_argument(
        "--max-history",
        type=int,
        default=1000,
        help="Maximum number of events to keep in history (Default: 1000)",
    )

    args = parser.parse_args()

    # Windows fix: Handle trailing backslash in quoted paths
    root_arg = args.root.strip().strip('"').strip("'")
    root = os.path.abspath(root_arg)

    if not os.path.isdir(root):
        print(f"Error: '{root}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    console = Console()

    console.print(
        Panel.fit(
            f"[bold cyan]Event-Driven Directory Watch Started[/bold cyan]\n\n"
            f"Watching: [yellow]{root}[/yellow]\n"
            f"Recursive: [yellow]{args.recursive}[/yellow]\n"
            f"Time Window: [yellow]{f'{args.minutes} minutes' if args.minutes else 'All (since start)'}[/yellow]\n"
            f"Refresh Rate: [yellow]{args.refresh}s[/yellow]\n"
            f"Show Events: [yellow]{'Yes' if args.show_events else 'No (use --show-events to enable)'}[/yellow]\n"
            f"Exclusions: [yellow]{len(args.exclude)} pattern(s)[/yellow]\n\n"
            f"[dim]Press Ctrl+C to stop[/dim]",
            border_style="green",
        )
    )

    # Create tracker and observer
    tracker = DirectoryChangeTracker(console, args.max_history, args.exclude)
    observer = Observer()
    observer.schedule(tracker, root, recursive=args.recursive)
    observer.start()

    console.print("[green]✓ Filesystem watcher started - waiting for events...[/green]")
    if args.exclude:
        console.print(f"[dim]Excluding: {', '.join(args.exclude)}[/dim]")
    console.print()

    try:
        with Live(
            console=console, refresh_per_second=1 / args.refresh, screen=False
        ) as live:
            while True:
                display = create_display(tracker, args)
                live.update(display)
                time.sleep(args.refresh)

    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping watcher...[/yellow]")
        observer.stop()
        observer.join()

        console.print(
            Panel.fit(
                f"[bold green]Watch Session Summary[/bold green]\n\n"
                f"Total Events: [cyan]{tracker.total_events}[/cyan]\n"
                f"Excluded Events: [dim]{tracker.excluded_events}[/dim]\n"
                f"Directories Changed: [cyan]{len(tracker.dir_changes)}[/cyan]\n"
                f"Runtime: [cyan]{(datetime.now() - tracker.start_time).total_seconds():.1f}s[/cyan]",
                border_style="cyan",
            )
        )


if __name__ == "__main__":
    main()
