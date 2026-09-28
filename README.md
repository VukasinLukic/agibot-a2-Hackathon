A2 Table Tennis Referee

This is an project developed at a hackathon in which a humanoid robot functions as an intelligent referee for table tennis.

About the Project

A2 Table Tennis Referee is developed for the Agibot A2 humanoid robot so that it can function as a smart, autonomous umpire for table tennis. The system combines computer vision, large language models (LLMs), real-time voice communication, and autonomous navigation in order to smoothly track the matches, grant points, interact with the players, and handle the progression of the game.

Key Features & Implementation

* Computer Vision & Ball Tracking: Implemented live vision processing utilizing custom models (BallNet and YOLO detection) to track the ball and detect bounces on the table with high precision.
* Scoring Logic & Referee Decisions: Created an automated point-recognition engine that analyzes ball trajectories, validates bounces, and makes refereeing decisions while filtering out false triggers.
* Voice Communication & LLM Integration: Integrated LiveKit for real-time speech, connected with a Supervisor and an LLM-powered joke/response bank for natural, engaging robot-player interactions.
* Backend & Synchronization: Established robust communication channels between the robot, its head display, mobile devices, and the central backend service for real-time match state and score tracking.
* Autonomous Navigation & Safety: Developed robot movement planning featuring built-in E-stop mechanisms, cancellation handlers, and position-arrival verification.

Architecture Overview

* User Interface & Scripts: Python services, LiveKit clients, and automated PowerShell/Bash scripts for streamlined synchronization and execution on the robot.
* Testing & Stability: Backed by over 300 passing tests alongside dedicated calibration and field-testing protocols.
* Repository Structure: Modular layout organized into vision, rag_service, robot_supervisor_v2, livekit_config, and core table tennis logic modules.
