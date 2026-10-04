import torch
import torch.nn as nn
import torch.nn.functional as F

class MultiScaleSpectralLoss(nn.Module):
    r"""
    Hàm mất mát phổ đa thang theo đúng Công thức (6), (7), (8), (9) của Đề cương C5:
      L_time = (1 / 4N) sum_{j=1}^4 sum_{n=0}^{N-1} ((x_j[n] - \hat{x}_j[n]) / s_j)^2
      A_T{v}[k] = |rFFT{h_T * (v - v_bar)}[k]| / sum_n h_T[n]
      S_T(v, \hat{v}; w_T) = (sum_{k=1}^{N_T/2} w_T[k] (A_T - \hat{A}_T)^2) / (sum_{k=1}^{N_T/2} w_T[k] A_T^2 + eps_f)
      L_spec = 0.5 * [(1/4) sum_{j=1}^4 S_8(x_j, \hat{x}_j; w_8) + S_32(v_32, \hat{v}_32; w_32)]
      L = L_time + beta * L_spec
    """
    def __init__(self, fs: int = 125, beta: float = 0.5, 
                 hr_band: tuple[float, float] = (0.8, 3.0), hr_weight: float = 2.0,
                 rr_band: tuple[float, float] = (0.1, 0.4), rr_weight: float = 4.0,
                 eps_f: float = 1e-8,
                 mode: str = "full"):
        super().__init__()
        self.fs = fs
        self.beta = beta
        self.hr_band = hr_band
        self.hr_weight = hr_weight
        self.rr_band = rr_band
        self.rr_weight = rr_weight
        self.eps_f = eps_f
        self.mode = mode

        if mode == "mse":
            self.beta = 0.0
        elif mode == "unweighted":
            self.hr_weight = 1.0
            self.rr_weight = 1.0
        elif mode == "hr_only":
            self.rr_weight = 1.0

        # Cửa sổ Hann và trọng số cho 8s (N8 = 1000 mẫu)
        n8 = 1000
        hann8 = torch.hann_window(n8, periodic=False)
        self.register_buffer('hann8', hann8)
        self.register_buffer('hann8_sum', torch.sum(hann8))
        
        freqs8 = torch.fft.rfftfreq(n8, d=1.0 / fs)
        w8 = torch.ones_like(freqs8)
        mask_hr = (freqs8 >= hr_band[0]) & (freqs8 <= hr_band[1])
        w8[mask_hr] = self.hr_weight
        w8[0] = 0.0  # Bỏ thành phần DC (k = 0)
        self.register_buffer('w8', w8)

        # Cửa sổ Hann và trọng số cho 32s (N32 = 4000 mẫu)
        n32 = 4000
        hann32 = torch.hann_window(n32, periodic=False)
        self.register_buffer('hann32', hann32)
        self.register_buffer('hann32_sum', torch.sum(hann32))
        
        freqs32 = torch.fft.rfftfreq(n32, d=1.0 / fs)
        w32 = torch.ones_like(freqs32)
        mask_rr = (freqs32 >= rr_band[0]) & (freqs32 <= rr_band[1])
        w32[mask_rr] = self.rr_weight
        w32[0] = 0.0  # Bỏ thành phần DC (k = 0)
        self.register_buffer('w32', w32)

    def _calc_spectral_discrepancy(self, v: torch.Tensor, v_hat: torch.Tensor, 
                                   hann_win: torch.Tensor, hann_sum: torch.Tensor,
                                   weights: torch.Tensor) -> torch.Tensor:
        """
        Tính sai lệch phổ S_T(v, v_hat; w_T) theo Công thức (7) và (8).
        """
        # v, v_hat: [B, N]
        v_centered = v - torch.mean(v, dim=-1, keepdim=True)
        v_hat_centered = v_hat - torch.mean(v_hat, dim=-1, keepdim=True)
        
        v_win = v_centered * hann_win
        v_hat_win = v_hat_centered * hann_win
        
        # Công thức (7): Chuẩn hóa biên độ rFFT bằng tổng cửa sổ Hann
        A = torch.abs(torch.fft.rfft(v_win, dim=-1)) / hann_sum
        A_hat = torch.abs(torch.fft.rfft(v_hat_win, dim=-1)) / hann_sum
        
        # Công thức (8): Bỏ thành phần DC (k=0), tính từ k=1 đến N_T / 2
        A_k = A[..., 1:]
        A_hat_k = A_hat[..., 1:]
        w_k = weights[1:]
        
        diff_sq = (A_k - A_hat_k) ** 2
        weighted_diff = torch.sum(w_k * diff_sq, dim=-1)
        ref_energy = torch.sum(w_k * (A_k ** 2), dim=-1) + self.eps_f
        
        s_t = weighted_diff / ref_energy
        return torch.mean(s_t)

    def forward(self, x_blocks: torch.Tensor, x_hat_blocks: torch.Tensor,
                x_seq_32s: torch.Tensor, x_hat_seq_32s: torch.Tensor,
                block_scales: torch.Tensor = None) -> tuple[torch.Tensor, dict]:
        """
        Đầu vào:
          x_blocks, x_hat_blocks: [B * 4, 1, 1000] hoặc [B * 4, 1000]
          x_seq_32s, x_hat_seq_32s: [B, 4000]
          block_scales: [B * 4, 1, 1], [B * 4, 1], [B * 4], hoặc [B, 4] (hệ số chuẩn hóa s_j)
        """
        # Kiểm tra kích thước lô
        if x_blocks.numel() == 0:
            raise ValueError("Batch x_blocks rỗng, không thể tính mất mát")
        if block_scales is None:
            raise ValueError("block_scales (s_j) là bắt buộc theo Công thức (6) để chuẩn hóa sai số thời gian")
        if not torch.isfinite(block_scales).all() or not (block_scales > 0).all():
            raise ValueError("block_scales phải hữu hạn và dương")
            
        # Công thức (6): Sai số thời gian chuẩn hóa theo s_j của từng khối
        if x_blocks.ndim == 3:
            s = block_scales.view(-1, 1, 1)
        else:
            s = block_scales.view(-1, 1)
        diff_normalized = (x_blocks - x_hat_blocks) / s
        loss_time = torch.mean(diff_normalized ** 2)
        
        # Công thức (8): Sai lệch phổ 8 giây (trung bình qua 4 khối)
        x_b_2d = x_blocks.squeeze(1) if x_blocks.ndim == 3 else x_blocks
        x_hat_b_2d = x_hat_blocks.squeeze(1) if x_hat_blocks.ndim == 3 else x_hat_blocks
        loss_spec_8s = self._calc_spectral_discrepancy(
            x_b_2d, x_hat_b_2d,
            self.hann8, self.hann8_sum, self.w8
        )
        
        # Công thức (8): Sai lệch phổ 32 giây
        loss_spec_32s = self._calc_spectral_discrepancy(
            x_seq_32s, x_hat_seq_32s,
            self.hann32, self.hann32_sum, self.w32
        )
        
        # Công thức (9): L_spec = 0.5 * (loss_spec_8s + loss_spec_32s)
        loss_spec_total = 0.5 * (loss_spec_8s + loss_spec_32s)
        
        # Tổng hợp mất mát
        loss_total = loss_time + self.beta * loss_spec_total
        
        loss_dict = {
            "loss_total": loss_total.item(),
            "loss_time": loss_time.item(),
            "loss_spec_8s": loss_spec_8s.item(),
            "loss_spec_32s": loss_spec_32s.item(),
            "loss_spec_total": loss_spec_total.item()
        }
        return loss_total, loss_dict
