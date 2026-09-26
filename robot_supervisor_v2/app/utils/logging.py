"""
Logging utilities for Robot Supervisor V2.
"""

import shutil
from pathlib import Path
from datetime import datetime


def archive_logs(logs_dir: Path) -> Path | None:
    """
    Archive current logs to logs/archive/{timestamp}/ directory.

    Args:
        logs_dir: Path to the logs directory

    Returns:
        Path to the archive directory, or None if no logs to archive
    """
    logs_dir = Path(logs_dir)

    if not logs_dir.exists():
        return None

    # Get all log files
    log_files = list(logs_dir.glob("*.log"))

    if not log_files:
        return None

    # Create archive directory with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = logs_dir / "archive" / timestamp
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Move each log file to archive
    archived_count = 0
    for log_file in log_files:
        try:
            dest = archive_dir / log_file.name
            shutil.move(str(log_file), str(dest))
            archived_count += 1
        except Exception as e:
            print(f"  ⚠ Failed to archive {log_file.name}: {e}")

    if archived_count > 0:
        return archive_dir
    else:
        # Remove empty archive directory
        archive_dir.rmdir()
        return None
