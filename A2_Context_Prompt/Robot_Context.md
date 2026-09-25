We are working on an Agibot A2 Ultra humanoid robot. The primary development we are doing is on this file system, humanoid-platform, and we are developing an application (named "The Supervisor"), which is meant to be a configurable application that users can use to give the robot certain functionalities and activate and deactivate them at will.

The functionalities are as follows:

1. The robot can understand speech, particularly understanding Serbian and English language. It utilizes Speech-To-Text to understand what was said to it, and then sends said text to a ChatGPT API, and then converts the text into Speech using a Text-To-Speech model API. 

2. The robot can have gestures turned on, allowing it 