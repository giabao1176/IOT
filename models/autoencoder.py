import torch
import torch.nn as nn
import torch.nn.functional as F

class STEQuantize(torch.autograd.Function):
    @staticmethod
    def forward(ctx, z, scale_a):
        q = torch.clamp(torch.round(z / scale_a), -32767, 32767)
        z_q = q * scale_a
        return z_q

    @staticmethod
    def backward(ctx, grad_output):
        return grad_output, None

def simulate_quantization(z: torch.Tensor, scale_a: torch.Tensor) -> torch.Tensor:
    return STEQuantize.apply(z, scale_a)

class PPGEncoder(nn.Module):
    def __init__(self, lz: int = 115):
        super().__init__()
        self.lz = lz
        self.e1 = nn.Conv1d(1, 16, kernel_size=7, stride=2, padding=3)
        self.e2 = nn.Conv1d(16, 32, kernel_size=7, stride=2, padding=3)
        self.e3 = nn.Conv1d(32, 1, kernel_size=1, stride=1)
        self.fc = nn.Linear(250, lz)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        x = F.relu(self.e1(x))
        x = F.relu(self.e2(x))
        x = self.e3(x)
        x = x.squeeze(1)
        z = self.fc(x)
        
        max_val = torch.max(torch.abs(z), dim=-1, keepdim=True)[0]
        scale_a = torch.clamp(max_val / 32767.0, min=1e-8)
        return z, scale_a

class PPGDecoder(nn.Module):
    def __init__(self, lz: int = 115):
        super().__init__()
        self.lz = lz
        self.d1 = nn.Linear(lz, 250)
        self.d2 = nn.Conv1d(1, 32, kernel_size=1, stride=1)
        self.d3 = nn.ConvTranspose1d(32, 16, kernel_size=7, stride=2, padding=3, output_padding=1)
        self.d4 = nn.ConvTranspose1d(16, 1, kernel_size=7, stride=2, padding=3, output_padding=1)

    def forward(self, z_q: torch.Tensor) -> torch.Tensor:
        x = self.d1(z_q)
        x = x.unsqueeze(1)
        x = F.relu(self.d2(x))
        x = F.relu(self.d3(x))
        x_hat = self.d4(x)
        return x_hat

class PPGAutoencoder(nn.Module):
    def __init__(self, lz: int = 115):
        super().__init__()
        self.encoder = PPGEncoder(lz=lz)
        self.decoder = PPGDecoder(lz=lz)

    def forward(self, x: torch.Tensor, use_ste: bool = True) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        z, scale_a = self.encoder(x)
        if use_ste:
            z_q = simulate_quantization(z, scale_a)
        else:
            z_q = z
        x_hat = self.decoder(z_q)
        return x_hat, z, scale_a
