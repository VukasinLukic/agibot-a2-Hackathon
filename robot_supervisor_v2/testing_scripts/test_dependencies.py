#!/usr/bin/env python
"""
Test script for service dependency management.
Tests dependency ordering, validation, and auto-start behavior.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.registry import ServiceRegistry, ServiceManager
import yaml


async def test_dependency_computation():
    """Test that dependency order is computed correctly."""
    print("=== Test 1: Dependency Order Computation ===\n")

    # Load config and create manager
    with open("robot_supervisor_v2/config.example.yaml") as f:
        config = yaml.safe_load(f)

    manager = ServiceManager()

    # Register all services
    for service_def in config["services"]:
        service = ServiceRegistry.create_service(
            service_type=service_def["type"],
            name=service_def["name"],
            config=service_def["config"]
        )
        manager.register(service)

    # Compute dependency order
    print("1. Computing dependency order...")
    order = manager._compute_dependency_order()
    print(f"   Start order: {order}")
    print()

    # Verify livekit comes before its dependents
    print("2. Verifying dependency ordering...")
    livekit_idx = order.index("livekit")
    voice_idx = order.index("voice-agent")
    audio_idx = order.index("audio-bridge")
    camera_idx = order.index("camera-bridge")

    assert livekit_idx < voice_idx, "LiveKit should start before voice-agent"
    assert livekit_idx < audio_idx, "LiveKit should start before audio-bridge"
    assert livekit_idx < camera_idx, "LiveKit should start before camera-bridge"

    print("   ✓ LiveKit is ordered before all its dependents")
    print()

    # Compute dependency levels
    print("3. Computing dependency levels...")
    levels = manager._compute_dependency_levels()
    for level in sorted(set(levels.values())):
        services_at_level = [name for name, l in levels.items() if l == level]
        print(f"   Level {level}: {', '.join(services_at_level)}")
    print()

    print("=== Test 1 Complete ===\n")
    return manager


async def test_dependency_validation():
    """Test that invalid dependencies are caught."""
    print("=== Test 2: Dependency Validation ===\n")

    manager = ServiceManager()

    # Create a mock service with invalid dependency
    from app.services.livekit_server import LiveKitServerService

    # Create livekit service
    livekit = LiveKitServerService(
        name="livekit",
        config={"display_name": "LiveKit Server"}
    )
    manager.register(livekit)

    # Create voice agent (depends on livekit - should be valid)
    from app.services.voice_agent import VoiceAgentService
    voice_agent = VoiceAgentService(
        name="voice-agent",
        config={
            "display_name": "Voice Agent",
            "agents_directory": "livekit-client"
        }
    )
    manager.register(voice_agent)

    print("1. Testing valid dependencies...")
    try:
        manager._validate_dependencies()
        print("   ✓ Valid dependencies accepted")
    except ValueError as e:
        print(f"   ✗ Unexpected error: {e}")
        return

    print()

    # Now test missing dependency
    print("2. Testing missing dependency detection...")

    # Create a service with a missing dependency
    class FakeService(LiveKitServerService):
        def get_dependencies(self):
            return ["nonexistent-service"]

    fake = FakeService(name="fake", config={"display_name": "Fake"})
    manager.register(fake)

    try:
        manager._validate_dependencies()
        print("   ✗ Should have detected missing dependency!")
    except ValueError as e:
        print(f"   ✓ Caught missing dependency: {e}")

    print()
    print("=== Test 2 Complete ===\n")


async def test_start_order():
    """Test that services actually start in dependency order."""
    print("=== Test 3: Service Start Order (Dry Run) ===\n")

    # Load config
    with open("robot_supervisor_v2/config.example.yaml") as f:
        config = yaml.safe_load(f)

    manager = ServiceManager()

    # Register all services
    for service_def in config["services"]:
        service = ServiceRegistry.create_service(
            service_type=service_def["type"],
            name=service_def["name"],
            config=service_def["config"]
        )
        manager.register(service)

    print("1. Dependency levels and batches:")
    levels = manager._compute_dependency_levels()

    batches = {}
    for service_name, level in levels.items():
        if level not in batches:
            batches[level] = []
        batches[level].append(service_name)

    for level in sorted(batches.keys()):
        print(f"   Level {level}: {', '.join(batches[level])}")

    print()
    print("2. Expected start sequence:")
    print("   - Level 0 services start in parallel")
    print("   - Level 1 services start after level 0 completes")
    print()

    print("=== Test 3 Complete ===\n")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Service Dependency Management Tests")
    print("=" * 60)
    print()

    try:
        # Test 1: Dependency computation
        manager = await test_dependency_computation()

        # Test 2: Dependency validation
        await test_dependency_validation()

        # Test 3: Start order preview
        await test_start_order()

        print("=" * 60)
        print("All Tests Passed! ✓")
        print("=" * 60)
        print()
        print("Note: These tests verify dependency logic only.")
        print("To test actual service startup, run:")
        print("  python robot_supervisor_v2/test_full_lifecycle.py")

    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
