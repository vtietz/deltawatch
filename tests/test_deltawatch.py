#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test suite for deltawatch.py
"""

import os
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from watchdog.events import (
    DirCreatedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from deltawatch import DirectoryChangeTracker, get_dir_size, human_bytes


class TestHumanBytes:
    """Test the human_bytes function"""

    def test_bytes(self):
        assert human_bytes(0) == "0 B"
        assert human_bytes(100) == "100 B"
        assert human_bytes(1023) == "1023 B"

    def test_kilobytes(self):
        assert human_bytes(1024) == "1.0 KB"
        assert human_bytes(1536) == "1.5 KB"
        assert human_bytes(2048) == "2.0 KB"

    def test_megabytes(self):
        assert human_bytes(1024 * 1024) == "1.0 MB"
        assert human_bytes(1024 * 1024 * 1.5) == "1.5 MB"

    def test_gigabytes(self):
        assert human_bytes(1024 * 1024 * 1024) == "1.0 GB"
        assert human_bytes(1024 * 1024 * 1024 * 2.5) == "2.5 GB"

    def test_large_numbers(self):
        result = human_bytes(1024**4)  # 1 TB
        assert "TB" in result

        result = human_bytes(1024**5)  # 1 PB
        assert "PB" in result


class TestGetDirSize:
    """Test the get_dir_size function"""

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            size = get_dir_size(tmpdir)
            assert size == 0

    def test_directory_with_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            file1 = os.path.join(tmpdir, "test1.txt")
            file2 = os.path.join(tmpdir, "test2.txt")

            with open(file1, "w") as f:
                f.write("a" * 100)  # 100 bytes

            with open(file2, "w") as f:
                f.write("b" * 200)  # 200 bytes

            size = get_dir_size(tmpdir)
            assert size == 300

    def test_directory_with_subdirectories(self):
        """Test that get_dir_size only counts files in the immediate directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create file in main directory
            file1 = os.path.join(tmpdir, "main.txt")
            with open(file1, "w") as f:
                f.write("a" * 100)

            # Create subdirectory with file
            subdir = os.path.join(tmpdir, "subdir")
            os.makedirs(subdir)
            file2 = os.path.join(subdir, "sub.txt")
            with open(file2, "w") as f:
                f.write("b" * 200)

            # Should only count main.txt, not sub.txt
            size = get_dir_size(tmpdir)
            assert size == 100

    def test_nonexistent_directory(self):
        """Test handling of non-existent directory"""
        size = get_dir_size("/nonexistent/path/that/does/not/exist")
        assert size == 0


class TestDirectoryChangeTracker:
    """Test the DirectoryChangeTracker class"""

    @pytest.fixture
    def console(self):
        """Provide a console instance for tests"""
        return Console()

    @pytest.fixture
    def tracker(self, console):
        """Provide a fresh tracker instance for each test"""
        return DirectoryChangeTracker(console, max_history=100)

    @pytest.fixture
    def temp_dir(self):
        """Provide a temporary directory for tests"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_initialization(self, tracker):
        """Test tracker initializes correctly"""
        assert tracker.total_events == 0
        assert len(tracker.dir_changes) == 0
        assert len(tracker.recent_events) == 0
        assert isinstance(tracker.event_counts, defaultdict)

    def test_file_creation_tracking(self, tracker, temp_dir):
        """Test tracking file creation events"""
        test_file = os.path.join(temp_dir, "test.txt")

        # Create actual file for size calculation
        with open(test_file, "w") as f:
            f.write("test content")

        # Simulate file creation event
        event = FileCreatedEvent(test_file)
        tracker.on_created(event)

        assert tracker.total_events == 1
        assert tracker.event_counts["created"] == 1
        assert temp_dir in tracker.dir_changes
        assert tracker.dir_changes[temp_dir] == 1

    def test_file_deletion_tracking(self, tracker, temp_dir):
        """Test tracking file deletion events"""
        test_file = os.path.join(temp_dir, "test.txt")

        # First create and track the file
        with open(test_file, "w") as f:
            f.write("test content" * 10)

        event = FileCreatedEvent(test_file)
        tracker.on_created(event)

        # Now delete it
        os.remove(test_file)
        event = FileDeletedEvent(test_file)
        tracker.on_deleted(event)

        assert tracker.total_events == 2
        assert tracker.event_counts["deleted"] == 1
        assert tracker.dir_changes[temp_dir] == 2

    def test_file_modification_tracking(self, tracker, temp_dir):
        """Test tracking file modification events"""
        test_file = os.path.join(temp_dir, "test.txt")

        # Create file
        with open(test_file, "w") as f:
            f.write("initial")

        event = FileCreatedEvent(test_file)
        tracker.on_created(event)

        # Modify file
        with open(test_file, "w") as f:
            f.write("modified content")

        event = FileModifiedEvent(test_file)
        tracker.on_modified(event)

        assert tracker.total_events == 2
        assert tracker.event_counts["modified"] == 1

    def test_file_move_tracking(self, tracker, temp_dir):
        """Test tracking file move events"""
        src_file = os.path.join(temp_dir, "source.txt")
        dest_file = os.path.join(temp_dir, "destination.txt")

        # Create source file
        with open(src_file, "w") as f:
            f.write("content")

        # Simulate move event
        event = FileMovedEvent(src_file, dest_file)
        tracker.on_moved(event)

        # Should record both moved and moved_to events
        assert tracker.total_events == 2
        assert tracker.event_counts["moved"] == 1
        assert tracker.event_counts["moved_to"] == 1

    def test_directory_creation_tracking(self, tracker, temp_dir):
        """Test tracking directory creation"""
        new_dir = os.path.join(temp_dir, "newdir")
        os.makedirs(new_dir)

        event = DirCreatedEvent(new_dir)
        tracker.on_created(event)

        assert tracker.total_events == 1
        assert new_dir in tracker.dir_changes

    def test_exclusion_patterns(self, console, temp_dir):
        """Test that exclusion patterns work correctly"""
        tracker = DirectoryChangeTracker(console, exclude_patterns=["*.tmp", "*cache*"])

        # Create files - one should be excluded
        test_file = os.path.join(temp_dir, "test.txt")
        tmp_file = os.path.join(temp_dir, "temp.tmp")

        with open(test_file, "w") as f:
            f.write("normal")
        with open(tmp_file, "w") as f:
            f.write("temporary")

        # Track both
        tracker.on_created(FileCreatedEvent(test_file))
        tracker.on_created(FileCreatedEvent(tmp_file))

        assert tracker.total_events == 1  # Only test.txt should be counted
        assert tracker.excluded_events == 1  # temp.tmp should be excluded

    def test_get_changed_dirs(self, tracker, temp_dir):
        """Test retrieving changed directories"""
        # Create events in the directory
        test_file = os.path.join(temp_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("content")

        tracker.on_created(FileCreatedEvent(test_file))

        changed = tracker.get_changed_dirs()
        assert len(changed) == 1
        assert changed[0][0] == temp_dir
        assert changed[0][1] == 1  # event count

    def test_get_changed_dirs_with_time_filter(self, tracker, temp_dir):
        """Test time-based filtering of changed directories"""
        test_file = os.path.join(temp_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("content")

        # Create event
        tracker.on_created(FileCreatedEvent(test_file))

        # Should be visible with 1 minute window
        changed = tracker.get_changed_dirs(since_minutes=1)
        assert len(changed) == 1

        # Manually adjust the timestamp to be old
        tracker.dir_last_change[temp_dir] = datetime.now() - timedelta(minutes=10)

        # Should not be visible with 1 minute window
        changed = tracker.get_changed_dirs(since_minutes=1)
        assert len(changed) == 0

    def test_recent_events_queue(self, tracker, temp_dir):
        """Test that recent events are stored correctly"""
        # Create multiple events
        for i in range(5):
            test_file = os.path.join(temp_dir, f"test{i}.txt")
            with open(test_file, "w") as f:
                f.write(f"content {i}")
            tracker.on_created(FileCreatedEvent(test_file))

        recent = tracker.get_recent_events(count=3)
        assert len(recent) == 3

        # Check structure: (time, type, path, size_delta)
        assert len(recent[0]) == 4
        assert recent[0][1] == "created"

    def test_max_history_limit(self, console):
        """Test that event history respects max_history limit"""
        tracker = DirectoryChangeTracker(console, max_history=5)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create more events than max_history
            for i in range(10):
                test_file = os.path.join(tmpdir, f"test{i}.txt")
                with open(test_file, "w") as f:
                    f.write(f"content {i}")
                tracker.on_created(FileCreatedEvent(test_file))

            # Only last 5 should be kept
            assert len(tracker.recent_events) == 5

    def test_size_delta_calculation_create(self, tracker, temp_dir):
        """Test that size deltas are calculated correctly for file creation"""
        test_file = os.path.join(temp_dir, "test.txt")
        content = "a" * 1000  # 1000 bytes

        with open(test_file, "w") as f:
            f.write(content)

        tracker.on_created(FileCreatedEvent(test_file))

        # Check that size delta was recorded
        assert temp_dir in tracker.dir_size_deltas
        assert tracker.dir_size_deltas[temp_dir] == 1000

    def test_size_delta_calculation_modify(self, tracker, temp_dir):
        """Test size deltas for file modifications"""
        test_file = os.path.join(temp_dir, "test.txt")

        # Create initial file
        with open(test_file, "w") as f:
            f.write("a" * 100)
        tracker.on_created(FileCreatedEvent(test_file))

        # Modify to larger size
        with open(test_file, "w") as f:
            f.write("b" * 200)
        tracker.on_modified(FileModifiedEvent(test_file))

        # Delta should be 100 (initial) + 100 (increase) = 200
        assert tracker.dir_size_deltas[temp_dir] == 200

    def test_size_delta_calculation_delete(self, tracker, temp_dir):
        """Test size deltas for file deletion"""
        test_file = os.path.join(temp_dir, "test.txt")

        # Create file
        with open(test_file, "w") as f:
            f.write("a" * 500)
        tracker.on_created(FileCreatedEvent(test_file))

        # Delete file
        os.remove(test_file)
        tracker.on_deleted(FileDeletedEvent(test_file))

        # Delta should be 500 (create) - 500 (delete) = 0
        assert tracker.dir_size_deltas[temp_dir] == 0


class TestCLIArgumentParsing:
    """Test CLI argument parsing (would require refactoring main() to be testable)"""

    def test_default_values(self):
        """Test that default argument values are reasonable"""
        # This is a placeholder for CLI tests
        # In a real scenario, you'd refactor main() to separate argument parsing
        # from execution, making it easier to test
        assert True  # Placeholder

    def test_path_handling(self):
        """Test that paths are handled correctly"""
        # Another placeholder for when CLI is refactored for testability
        assert True  # Placeholder


class TestIntegration:
    """Integration tests that test the whole system"""

    @pytest.mark.timeout(10)
    def test_watch_and_detect_changes(self):
        """Test that the watcher can detect real filesystem changes"""
        from watchdog.observers import Observer

        console = Console()
        tracker = DirectoryChangeTracker(console)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Resolve symlinks (important for macOS where /var -> /private/var)
            tmpdir_real = os.path.realpath(tmpdir)

            observer = Observer()
            observer.schedule(tracker, tmpdir, recursive=False)
            observer.start()

            try:
                # Give observer time to start
                time.sleep(0.5)

                # Create a file
                test_file = os.path.join(tmpdir, "integration_test.txt")
                with open(test_file, "w") as f:
                    f.write("integration test content")

                # Wait for event to be processed
                time.sleep(1)

                # Verify event was tracked
                assert tracker.total_events > 0

                # Check if either the original path or the real path is in dir_changes
                # (handles macOS symlink case: /var -> /private/var)
                assert (
                    tmpdir in tracker.dir_changes or tmpdir_real in tracker.dir_changes
                ), f"Neither {tmpdir} nor {tmpdir_real} found in {list(tracker.dir_changes.keys())}"

            finally:
                observer.stop()
                observer.join(timeout=2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
