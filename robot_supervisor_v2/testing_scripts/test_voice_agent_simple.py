#!/usr/bin/env python
"""
Simple test script for Voice Agent service.
Tests agent discovery and selection without actually running agents.
"""

import asyncio
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.voice_agent import VoiceAgentService


async def main():
    print("=== Voice Agent Service Test ===\n")

    # Create service instance
    config = {
        "display_name": "Voice Agent",
        "agents_directory": "livekit-client"
    }

    service = VoiceAgentService(name="voice-agent-test", config=config)

    # Test 1: Discovery
    print("1. Agent Discovery")
    print(f"   Directory: {config['agents_directory']}")

    agents = await service.discover_agents()
    print(f"   Found: {len(agents)} agent(s)")
    print()

    if not agents:
        print("   No agents found!")
        print("   Looking for files: *agent*.py")
        return

    # Test 2: List all agents
    print("2. Available Agents")
    for agent_info in service.get_available_agents():
        print(f"   • {agent_info['name']}")
        print(f"     Module: {agent_info['module']}")
        print(f"     Path: {agent_info['path']}")
        print()

    # Test 3: Agent selection
    print("3. Agent Selection")
    first_agent = agents[0]
    print(f"   Selecting: {first_agent.name}")

    await service.set_agent(first_agent.name)
    current = service.get_current_agent()
    print(f"   ✓ Current agent: {current['name']}")
    print()

    # Test 4: Configuration
    print("4. Configuration Parameters")
    params = service.get_config_parameters()
    for param in params:
        print(f"   • {param.key}: {param.value}")
        if param.choices:
            print(f"     Available: {', '.join(param.choices)}")
    print()

    # Test 5: Environment check
    print("5. Environment Variables")
    env_vars = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]

    for var in env_vars:
        value = os.getenv(var)
        if value:
            display = "***" if "SECRET" in var else value
            print(f"   ✓ {var}: {display}")
        else:
            print(f"   ✗ {var}: Not set")

    print()
    print("   Note: Set these with:")
    print("   export LIVEKIT_URL=ws://localhost:7880")
    print("   export LIVEKIT_API_KEY=devkey")
    print("   export LIVEKIT_API_SECRET=secret")
    print()

    # Test 6: Service status
    print("6. Service Status")
    status = service.get_status()
    print(f"   State: {status.state}")
    print(f"   Display Name: {status.display_name}")
    print(f"   Current Mode: {status.current_mode}")
    print(f"   Available Modes: {', '.join(status.available_modes)}")
    print()

    print("=== Test Complete ===")
    print("\nTo actually run an agent, use service.start()")
    print(f"Example command: python {current['path']}")


if __name__ == "__main__":
    asyncio.run(main())
