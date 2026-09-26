import torch

if(torch.cuda.is_available()):
    print("There is torch.")
else:
    print("Torch not here...")