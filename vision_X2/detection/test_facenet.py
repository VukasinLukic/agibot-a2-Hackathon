import torch
from facenet_pytorch import MTCNN, InceptionResnetV1
print(torch.__version__, torch.cuda.is_available())
mtcnn = MTCNN(device='cuda')
resnet = InceptionResnetV1(pretrained='vggface2').eval().to('cuda')