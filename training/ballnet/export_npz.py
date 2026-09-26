"""torch state_dict -> npz for numpy inference. Keys: c0w c0b c1w c1b c2w c2b l0w l0b l1w l1b."""
import torch, numpy as np, sys
sd = torch.load(sys.argv[1])
m = {'f.0': 'c0', 'f.3': 'c1', 'f.6': 'c2', 'h.1': 'l0', 'h.3': 'l1'}
out = {}
for k, v in sd.items():
    base, kind = k.rsplit('.', 1)
    out[m[base] + ('w' if kind == 'weight' else 'b')] = v.numpy().astype(np.float32)
np.savez(sys.argv[2], **out)
print({k: v.shape for k, v in out.items()})
