#!/usr/bin/env python
"""
Test script for LiveKit server service.
Tests service lifecycle: start, health check, stop.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.livekit_server import LiveKitServerService


async def test_livekit_service():
    print("=== LiveKit Server Service Test ===\n")

    # Create service instance
    config = {
        "display_name": "LiveKit Server"
    }

    service = LiveKitServerService(name="livekit-test", config=config)

    print("1. Initial status:")
    status = service.get_status()
    print(f"   State: {status.state}")
    print(f"   PID: {status.pid}")
    print(f"   Display name: {status.display_name}")
    print()

    # Test starting the service
    print("2. Starting LiveKit server...")
    print("   Command: livekit-server --dev")
    try:
        await service.start()
        print("   ✓ Service started successfully")

        status = service.get_status()
        print(f"   State: {status.state}")
        print(f"   PID: {status.pid}")
        print()
    except Exception as e:
        print(f"   ✗ Failed to start service: {e}")
        return

    # Test health check
    print("3. Testing health check...")
    try:
        healthy = await service.check_health()
        print(f"   Health check result: {'✓ Healthy' if healthy else '✗ Unhealthy'}")
        print()
    except Exception as e:
        print(f"   ✗ Health check failed: {e}")
        print()

    # Let it run for a few seconds
    print("4. Letting service run for 5 seconds...")
    await asyncio.sleep(5)

    # Check health again
    healthy = await service.check_health()
    print(f"   Still running: {'✓ Yes' if healthy else '✗ No'}")

    status = service.get_status()
    if status.uptime_seconds:
        print(f"   Uptime: {status.uptime_seconds:.1f} seconds")
    print()

    # Test log file
    print("5. Checking log file...")
    log_path = service.get_log_path()
    print(f"   Log path: {log_path}")

    if Path(log_path).exists():
        print("   ✓ Log file exists")

        # Show last few lines
        with open(log_path, 'r') as f:
            lines = f.readlines()
            if lines:
                print(f"   Last 5 lines of log:")
                for line in lines[-5:]:
                    print(f"     {line.rstrip()}")
    else:
        print("   ✗ Log file not found")
    print()

    # Test stopping the service
    print("6. Stopping LiveKit server...")
    try:
        await service.stop()
        print("   ✓ Service stopped successfully")

        status = service.get_status()
        print(f"   State: {status.state}")
        print(f"   PID: {status.pid}")
        print()
    except Exception as e:
        print(f"   ✗ Failed to stop service: {e}")
        print()

    # Verify it's stopped
    print("7. Verifying service is stopped...")
    healthy = await service.check_health()
    print(f"   Health check after stop: {'✗ Still running!' if healthy else '✓ Stopped'}")
    print()

    print("=== Test Complete ===")


async def test_restart():
    """Test the restart functionality."""
    print("\n=== Testing Restart Functionality ===\n")

    config = {
        "display_name": "LiveKit Server"
    }

    service = LiveKitServerService(name="livekit-test", config=config)

    print("1. Starting service...")
    await service.start()
    print(f"   PID: {service.get_status().pid}")

    await asyncio.sleep(2)

    print("\n2. Restarting service...")
    old_pid = service.get_status().pid
    await service.restart()
    new_pid = service.get_status().pid

    print(f"   Old PID: {old_pid}")
    print(f"   New PID: {new_pid}")
    print(f"   {'✓ PID changed' if old_pid != new_pid else '✗ Same PID'}")

    print("\n3. Cleaning up...")
    await service.stop()
    print("   ✓ Stopped")

    print("\n=== Restart Test Complete ===")


async def main():
    """Run all tests."""
    try:
        # Basic lifecycle test
        await test_livekit_service()

        # Restart test
        await test_restart()

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
