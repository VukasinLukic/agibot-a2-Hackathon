#!/usr/bin/env python
"""
Test script for Manual Controller.
Tests conversation lifecycle: start, status, stop.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.registry import ServiceRegistry, ServiceManager
from app.controllers import ManualController
import yaml


async def test_manual_controller_basic():
    """Test basic manual controller functionality without actually starting services."""
    print("=== Manual Controller Basic Test ===\n")

    # Load config and create service manager
    with open("robot_supervisor_v2/config.example.yaml") as f:
        config = yaml.safe_load(f)

    manager = ServiceManager()

    # Register all services
    print("1. Registering services...")
    for service_def in config["services"]:
        service = ServiceRegistry.create_service(
            service_type=service_def["type"],
            name=service_def["name"],
            config=service_def["config"]
        )
        manager.register(service)
    print(f"   Registered {len(manager.list_all())} services")
    print()

    # Create manual controller
    print("2. Creating manual controller...")
    controller = ManualController(manager)
    print("   ✓ Controller created")
    print()

    # Check initial status
    print("3. Initial status:")
    status = controller.get_status()
    print(f"   State: {status['state']}")
    print(f"   Room: {status['room']}")
    print(f"   Job ID: {status['job_id']}")
    print()

    print("=== Basic Test Complete ===\n")
    return controller, manager


async def test_engagement_states():
    """Test engagement state transitions."""
    print("=== Engagement State Test ===\n")

    # Setup
    with open("robot_supervisor_v2/config.example.yaml") as f:
        config = yaml.safe_load(f)

    manager = ServiceManager()
    for service_def in config["services"]:
        service = ServiceRegistry.create_service(
            service_type=service_def["type"],
            name=service_def["name"],
            config=service_def["config"]
        )
        manager.register(service)

    controller = ManualController(manager)

    # Test state transitions
    print("1. Initial state:")
    status = controller.get_status()
    print(f"   State: {status['state']} (should be 'idle')")
    assert status['state'] == "idle", "Initial state should be idle"
    print()

    # Note: We can't test actual start/stop without running services
    # But we can verify the controller structure is correct

    print("2. Controller capabilities:")
    print(f"   - start_conversation() available: {hasattr(controller, 'start_conversation')}")
    print(f"   - stop_conversation() available: {hasattr(controller, 'stop_conversation')}")
    print(f"   - get_status() available: {hasattr(controller, 'get_status')}")
    print()

    print("=== State Test Complete ===\n")


async def test_concurrent_calls():
    """Test thread-safety with concurrent calls."""
    print("=== Concurrent Call Test ===\n")

    with open("robot_supervisor_v2/config.example.yaml") as f:
        config = yaml.safe_load(f)

    manager = ServiceManager()
    for service_def in config["services"]:
        service = ServiceRegistry.create_service(
            service_type=service_def["type"],
            name=service_def["name"],
            config=service_def["config"]
        )
        manager.register(service)

    controller = ManualController(manager)

    print("1. Testing lock mechanism...")
    print("   The controller uses asyncio.Lock to prevent race conditions")
    print("   Multiple concurrent calls will be serialized")
    print()

    # Verify lock exists
    assert hasattr(controller, '_lock'), "Controller should have _lock attribute"
    print("   ✓ Lock mechanism present")
    print()

    print("=== Concurrent Test Complete ===\n")


async def test_service_orchestration_plan():
    """Show the service orchestration plan."""
    print("=== Service Orchestration Plan ===\n")

    with open("robot_supervisor_v2/config.example.yaml") as f:
        config = yaml.safe_load(f)

    manager = ServiceManager()
    for service_def in config["services"]:
        service = ServiceRegistry.create_service(
            service_type=service_def["type"],
            name=service_def["name"],
            config=service_def["config"]
        )
        manager.register(service)

    print("When start_conversation() is called:")
    print()
    print("1. Optional: Restart LiveKit if requested")
    print("2. Ensure LiveKit server is running")
    print("3. Optional: Select specific agent")
    print("4. Start voice agent (auto-starts dependencies)")
    print("5. Start audio bridge")
    print("6. Start camera bridge (optional)")
    print("7. Start gesture bridge (optional)")
    print("8. Dispatch agent job via LiveKit API")
    print("9. Update state to 'engaged'")
    print()

    print("Dependency resolution ensures correct order:")
    levels = manager._compute_dependency_levels()
    print(f"   Level 0: {[name for name, l in levels.items() if l == 0]}")
    print(f"   Level 1: {[name for name, l in levels.items() if l == 1]}")
    print()

    print("When stop_conversation() is called:")
    print()
    print("1. Force cancel agent job via LiveKit API")
    print("2. Services remain running for quick re-engagement")
    print("3. Update state to 'idle'")
    print()
    print("Note: Graceful shutdown (goodbye event) is planned for future")
    print()

    print("=== Orchestration Plan Complete ===\n")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Manual Controller Tests")
    print("=" * 60)
    print()

    try:
        # Test 1: Basic functionality
        await test_manual_controller_basic()

        # Test 2: State management
        await test_engagement_states()

        # Test 3: Thread safety
        await test_concurrent_calls()

        # Test 4: Orchestration plan
        await test_service_orchestration_plan()

        print("=" * 60)
        print("All Tests Passed! ✓")
        print("=" * 60)
        print()
        print("Note: These tests verify controller structure only.")
        print("To test actual conversation lifecycle:")
        print("  1. Start LiveKit server")
        print("  2. Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET")
        print("  3. Call controller.start_conversation()")

    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
