#!/usr/bin/env python
"""
Test script for Voice Agent service.
Tests agent discovery, selection, and running.
"""

import asyncio
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.voice_agent import VoiceAgentService


async def test_agent_discovery():
    """Test discovering agents in a directory."""
    print("=== Voice Agent Discovery Test ===\n")

    # Create service instance
    config = {
        "display_name": "Voice Agent",
        "agents_directory": "livekit-client"  # Directory containing agent files
    }

    service = VoiceAgentService(name="voice-agent-test", config=config)

    print("1. Initial status:")
    status = service.get_status()
    print(f"   State: {status.state}")
    print(f"   Display name: {status.display_name}")
    print()

    # Test agent discovery
    print("2. Discovering agents...")
    agents_dir = config["agents_directory"]
    print(f"   Scanning directory: {agents_dir}")

    if not Path(agents_dir).exists():
        print(f"   ✗ Directory does not exist!")
        print(f"   Please create the directory or adjust the path")
        return

    try:
        agents = await service.discover_agents()
        print(f"   ✓ Found {len(agents)} agent(s)")
        print()

        if agents:
            print("3. Available agents:")
            for agent_info in service.get_available_agents():
                print(f"   - {agent_info['name']}")
                print(f"     Module: {agent_info['module']}")
                print(f"     Path: {agent_info['path']}")
            print()

            # Show available modes (agent names)
            print("4. Available modes:")
            modes = service.get_available_modes()
            for mode in modes:
                print(f"   - {mode}")
            print()

            # Show current agent selection
            print("5. Current agent:")
            current = service.get_current_agent()
            if current:
                print(f"   {current['name']} (selected)")
            else:
                print("   None (will auto-select first agent on start)")
            print()

            # Test configuration parameters
            print("6. Configuration parameters:")
            params = service.get_config_parameters()
            for param in params:
                print(f"   - {param.key}: {param.value}")
                if param.choices:
                    print(f"     Choices: {', '.join(param.choices)}")
            print()

        else:
            print("   No agents found in directory")
            print(f"   Looking for files matching: *_agent.py or agent_*.py")
            print()

    except Exception as e:
        print(f"   ✗ Error during discovery: {e}")
        import traceback
        traceback.print_exc()
        return

    print("=== Discovery Test Complete ===")
    return service, agents


async def test_agent_selection(service, agents):
    """Test selecting a specific agent."""
    if not agents:
        print("\nSkipping selection test (no agents available)")
        return

    print("\n=== Agent Selection Test ===\n")

    # Get first agent
    first_agent = agents[0]
    agent_implementation = first_agent.name

    print(f"1. Selecting agent implementation: {agent_implementation}")
    try:
        await service.set_agent(agent_implementation)
        current = service.get_current_agent()
        print(f"   ✓ Selected: {current['name']}")
        print()
    except Exception as e:
        print(f"   ✗ Failed to select agent: {e}")
        return

    print("=== Selection Test Complete ===")


async def test_agent_running(service, agents):
    """Test actually running an agent."""
    if not agents:
        print("\nSkipping run test (no agents available)")
        return

    print("\n=== Agent Running Test ===\n")

    # Check if LiveKit environment variables are set
    print("1. Checking environment variables:")
    env_vars = {
        "LIVEKIT_URL": os.getenv("LIVEKIT_URL"),
        "LIVEKIT_API_KEY": os.getenv("LIVEKIT_API_KEY"),
        "LIVEKIT_API_SECRET": os.getenv("LIVEKIT_API_SECRET")
    }

    all_set = True
    for key, value in env_vars.items():
        if value:
            # Mask secret
            display_value = "***" if "SECRET" in key else value
            print(f"   ✓ {key}: {display_value}")
        else:
            print(f"   ✗ {key}: Not set")
            all_set = False

    if not all_set:
        print("\n   WARNING: Some environment variables are not set!")
        print("   The agent may fail to start without proper credentials.")
        print("\n   To set them:")
        print("   export LIVEKIT_URL=ws://localhost:7880")
        print("   export LIVEKIT_API_KEY=devkey")
        print("   export LIVEKIT_API_SECRET=secret")
        print()

        response = input("   Continue anyway? (y/N): ")
        if response.lower() != 'y':
            print("   Skipping run test")
            return

    print()

    # Start the agent
    print("2. Starting voice agent...")
    print(f"   Selected agent: {service.get_current_agent()['name']}")

    try:
        await service.start()
        print(f"   ✓ Agent started successfully")
        print(f"   PID: {service.get_status().pid}")
        print(f"   State: {service.get_status().state}")
        print()
    except Exception as e:
        print(f"   ✗ Failed to start agent: {e}")
        import traceback
        traceback.print_exc()
        return

    # Let it run briefly
    print("3. Letting agent run for 5 seconds...")
    await asyncio.sleep(5)

    # Check health
    healthy = await service.check_health()
    print(f"   Health check: {'✓ Healthy' if healthy else '✗ Unhealthy'}")

    if service.get_status().uptime_seconds:
        print(f"   Uptime: {service.get_status().uptime_seconds:.1f} seconds")
    print()

    # Check logs
    print("4. Checking log file...")
    log_path = service.get_log_path()
    print(f"   Log path: {log_path}")

    if Path(log_path).exists():
        print("   ✓ Log file exists")
        with open(log_path, 'r') as f:
            lines = f.readlines()
            if lines:
                print(f"   Last 10 lines:")
                for line in lines[-10:]:
                    print(f"     {line.rstrip()}")
    else:
        print("   ✗ Log file not found")
    print()

    # Stop the agent
    print("5. Stopping voice agent...")
    try:
        await service.stop()
        print("   ✓ Agent stopped successfully")
        print(f"   State: {service.get_status().state}")
        print()
    except Exception as e:
        print(f"   ✗ Failed to stop agent: {e}")

    print("=== Run Test Complete ===")


async def main():
    """Run all tests."""
    try:
        # Test agent discovery
        result = await test_agent_discovery()

        if result is None:
            return

        service, agents = result

        # Test agent selection
        await test_agent_selection(service, agents)

        # Test actually running an agent
        await test_agent_running(service, agents)

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
