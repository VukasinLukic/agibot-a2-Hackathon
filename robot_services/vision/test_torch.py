import torch
import os
import sys
import numpy as np
import torchvision


if (torch.cuda.is_available()):
    print("CUDA IS AVAILABLE! YIPEEE")
else:
    print("CUDA NOT AVAILABLE...")

print(f"Torch Ver: {torch.__version__}")
print(f"Torchvision Ver: {torchvision.__version__}")
print(f"Numpy Ver: {np.__version__}")


sys.exit()