#!/usr/bin/env python
"""
Minimal services test script.
Starts only: LiveKit server, Voice Agent, and Audio Bridge.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.registry import ServiceRegistry, ServiceManager
from app.utils.logging import archive_logs


async def main():
    """Start minimal services for testing."""
    print("=" * 60)
    print("Minimal Services Test")
    print("=" * 60)
    print()

    # Create service manager
    manager = ServiceManager()
    services_started = False

    # Register LiveKit server
    print("1. Registering LiveKit Server...")
    livekit = ServiceRegistry.create_service(
        service_type="livekit-server",
        name="livekit",
        config={
            "display_name": "LiveKit Server"
        }
    )
    manager.register(livekit)
    print("   ✓ Registered")

    # Register Voice Agent
    print("2. Registering Voice Agent...")
    voice_agent = ServiceRegistry.create_service(
        service_type="voice-agent",
        name="voice-agent",
        config={
            "display_name": "Voice Agent",
            "agents_directory": "livekit-client",
            "selected_agent": "agent_main"  # Module name without .py
        }
    )
    manager.register(voice_agent)
    print("   ✓ Registered")



    # Register Audio Bridge
    print("3. Registering Audio Bridge...")
    audio_bridge = ServiceRegistry.create_service(
        service_type="audio-bridge",
        name="audio-bridge",
        config={
            "display_name": "Audio Bridge",
            "script": "robot_services/audio/audio_bridge.py",
            "default_microphone": None,  # Auto-detect
            "default_speakers": None      # Auto-detect
        }
    )
    manager.register(audio_bridge)
    print("   ✓ Registered")

    # Register Gesture Bridge (OPTIONAL - won't block if it fails)
    print("4. Registering Gesture Bridge (optional)...")
    gesture_bridge = ServiceRegistry.create_service(
        service_type="gesture-bridge",
        name="gesture-bridge",
        config={
            "display_name": "Gesture Bridge",
            "script": "robot_services/gestures/gesture_api.py",
            "port": 8090,
            "optional": True  # This makes it non-blocking
        }
    )
    manager.register(gesture_bridge)
    print("   ✓ Registered as optional")
    print()

    # Show dependency order
    print("5. Computing dependency order...")
    order = manager._compute_dependency_order()
    print(f"   Start order: {order}")
    print()

    # Test device discovery
    print("6. Testing audio device discovery...")
    try:
        input_devices = audio_bridge.get_input_devices()
        output_devices = audio_bridge.get_output_devices()

        print(f"   Found {len(input_devices)} input device(s):")
        for dev in input_devices:
            default = " (DEFAULT)" if dev.get('is_default') else ""
            print(f"     [{dev['index']}] {dev['name']}{default}")

        print(f"   Found {len(output_devices)} output device(s):")
        for dev in output_devices:
            default = " (DEFAULT)" if dev.get('is_default') else ""
            print(f"     [{dev['index']}] {dev['name']}{default}")
    except Exception as e:
        print(f"   ✗ Device discovery failed: {e}")
        import traceback
        traceback.print_exc()
    print()

    # Start services individually in dependency order
    print("7. Starting services individually...")

    # Start LiveKit Server
    try:
        print("   Starting LiveKit Server...")
        await manager.start_service("livekit", start_dependencies=False)
        services_started = True
        print("   ✓ LiveKit Server started")
    except Exception as e:
        print(f"   ✗ Failed to start LiveKit Server: {e}")
        import traceback
        traceback.print_exc()
        await manager.stop_all()
        return
    
    await asyncio.sleep(5)

    # Start Voice Agent
    try:
        print("   Starting Voice Agent...")
        await manager.start_service("voice-agent", start_dependencies=False)
        print("   ✓ Voice Agent started")
    except Exception as e:
        print(f"   ✗ Failed to start Voice Agent: {e}")
        import traceback
        traceback.print_exc()
        await manager.stop_all()
        return
    
    await asyncio.sleep(5)

    # Start Audio Bridge
    try:
        print("   Starting Audio Bridge...")
        await manager.start_service("audio-bridge", start_dependencies=False)
        print("   ✓ Audio Bridge started")
    except Exception as e:
        print(f"   ✗ Failed to start Audio Bridge: {e}")
        import traceback
        traceback.print_exc()
        await manager.stop_all()
        return

    print()

    # Start Gesture Bridge (optional - won't block if it fails)
    try:
        print("   Starting Gesture Bridge (optional)...")
        await manager.start_service("gesture-bridge", start_dependencies=False)
        print("   ✓ Gesture Bridge started")
    except Exception as e:
        print(f"   ⚠ Gesture Bridge failed (continuing): {e}")

    print()

    # Test dispatch logic
    print("8. Testing dispatch logic...")
    from app.controllers.manual import ManualController

    # Create manual controller
    controller = ManualController(manager)

    # Get room name from environment or use default
    test_room = "g1-lab"

    try:
        print(f"   Dispatching conversation to room: {test_room}")
        dispatch_result = await controller.dispatch_conversation(room=test_room)
        print(f"   ✓ Dispatch result: {dispatch_result}")

        # Show status
        status = controller.get_status()
        print(f"   Current engagement state: {status['state']}")
        print(f"   Job ID: {status.get('job_id')}")
        print(f"   Room: {status.get('room')}")

        # Wait a bit to let conversation run
        print("   Waiting 10 seconds to let conversation run...")
        await asyncio.sleep(30)

        # Wrap conversation
        print("   Wrapping conversation...")
        wrap_result = await controller.wrap_conversation()
        print(f"   ✓ Wrap result: {wrap_result}")

    except Exception as e:
        print(f"   ✗ Dispatch test failed: {e}")
        import traceback
        traceback.print_exc()
    print()

    # Show status
    print("9. Service status:")
    statuses = manager.get_all_status()
    for status in statuses:
        state_icon = {
            "running": "🟢",
            "stopped": "🟡",
            "failed": "🔴",
            "starting": "🔵",
            "stopping": "🔵"
        }.get(status.state, "⚪")

        uptime = f" (uptime: {status.uptime_seconds:.1f}s)" if status.uptime_seconds else ""
        print(f"   {state_icon} {status.display_name}: {status.state}{uptime}")
    print()

    # Wait for user to stop
    print("=" * 60)
    print("Services running. Press Ctrl+C to stop...")
    print("=" * 60)
    print()
    print("Environment variables needed for audio bridge:")
    print("  export LIVEKIT_URL='ws://localhost:7880'")
    print("  export LIVEKIT_API_KEY='devkey'")
    print("  export LIVEKIT_API_SECRET='secret'")
    print("  export LIVEKIT_ROOM='test-room'")
    print()

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\n\nStopping services...")
    finally:
        # Always stop services on exit (normal or error)
        if services_started:
            print("\n10. Stopping services in reverse order...")
            try:
                await manager.stop_all()
                print("   ✓ All services stopped")
            except Exception as e:
                print(f"   ✗ Error stopping services: {e}")
                import traceback
                traceback.print_exc()
            print()

        # Archive logs
        print("11. Archiving logs...")
        logs_dir = Path(__file__).parent / "logs"
        try:
            archive_dir = archive_logs(logs_dir)
            if archive_dir:
                print(f"   ✓ Archived logs to {archive_dir.relative_to(logs_dir.parent)}")
            else:
                print("   ℹ No logs to archive")
        except Exception as e:
            print(f"   ✗ Error archiving logs: {e}")
        print()

    print("=" * 60)
    print("Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
