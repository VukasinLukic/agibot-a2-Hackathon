#!/usr/bin/env python
"""
Test script for service definitions.
Demonstrates service registration, device enumeration, and basic operations.
"""

import asyncio
import yaml
from pathlib import Path
from app.services import ServiceRegistry, ServiceManager


async def main():
    print("=== Robot Supervisor V2 - Service Test ===\n")

    # Load configuration
    config_path = Path("robot_supervisor_v2/config.example.yaml")
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Create service manager
    manager = ServiceManager()

    print("1. Creating services from config...")
    print(f"   Found {len(config['services'])} service definitions\n")

    # Register all services
    for service_def in config["services"]:
        service_type = service_def["type"]
        service_name = service_def["name"]
        service_config = service_def["config"]

        try:
            service = ServiceRegistry.create_service(service_type, service_name, service_config)
            manager.register(service)
            print(f"   ✓ Registered {service_name} ({service_type})")
        except Exception as e:
            print(f"   ✗ Failed to register {service_name}: {e}")

    print("\n2. Listing all services:")
    for service in manager.list_all():
        status = service.get_status()
        print(f"   - {status.display_name} ({status.name})")
        print(f"     State: {status.state}")
        print(f"     Backend: {status.backend}")

    print("\n3. Testing device enumeration...\n")

    # Test camera devices
    camera_service = manager.get("camera-bridge")
    if camera_service:
        print("   Camera devices:")
        devices = await camera_service.list_devices()
        if devices:
            for device in devices:
                default_marker = " [DEFAULT]" if device.is_default else ""
                print(f"     - {device.name}: {device.id}{default_marker}")
        else:
            print("     No camera devices found")

    # Test audio devices
    audio_service = manager.get("audio-bridge")
    if audio_service:
        print("\n   Audio devices:")
        devices = await audio_service.list_devices()

        # Separate by type
        input_devices = [d for d in devices if d.type == "audio_input"]
        output_devices = [d for d in devices if d.type == "audio_output"]

        print(f"\n     Input devices ({len(input_devices)}):")
        for device in input_devices:
            default_marker = " [DEFAULT]" if device.is_default else ""
            channels = device.metadata.get("channels", "?")
            samplerate = device.metadata.get("samplerate", "?")
            print(f"       - {device.name}{default_marker}")
            print(f"         Channels: {channels}, Sample rate: {samplerate} Hz")

        print(f"\n     Output devices ({len(output_devices)}):")
        for device in output_devices:
            default_marker = " [DEFAULT]" if device.is_default else ""
            channels = device.metadata.get("channels", "?")
            samplerate = device.metadata.get("samplerate", "?")
            print(f"       - {device.name}{default_marker}")
            print(f"         Channels: {channels}, Sample rate: {samplerate} Hz")

    # Test voice agent discovery
    voice_agent = manager.get("voice-agent")
    if voice_agent:
        print("\n   Discovering voice agents...")
        agents = await voice_agent.discover_agents()
        if agents:
            print(f"   Found {len(agents)} agent(s):")
            for agent_info in voice_agent.get_available_agents():
                print(f"     - {agent_info['name']}")
                print(f"       Module: {agent_info['module']}")
                print(f"       Path: {agent_info['path']}")
        else:
            agents_dir = voice_agent._config.get("agents_directory")
            print(f"     No agents found in: {agents_dir}")

    print("\n4. Testing configuration parameters...\n")

    for service in manager.list_all():
        params = service.get_config_parameters()
        if params:
            print(f"   {service.display_name}:")
            for param in params:
                req = " *" if param.required else ""
                print(f"     - {param.key}{req}: {param.description}")
                if param.choices:
                    print(f"       Choices: {', '.join(param.choices)}")

    print("\n5. Testing health check configuration...\n")

    for service in manager.list_all():
        health_config = service.get_health_config()
        if health_config:
            print(f"   {service.display_name}:")
            print(f"     Type: {health_config.type}")
            if health_config.endpoint:
                print(f"     Endpoint: {health_config.endpoint}")
            if health_config.port:
                print(f"     Port: {health_config.port}")
            print(f"     Timeout: {health_config.timeout_seconds}s")

    print("\n=== Test Complete ===")
    print("\nNote: Services were not started in this test.")
    print("To actually start services, use the API or create a supervisor instance.")


if __name__ == "__main__":
    asyncio.run(main())
