"""
轻量 UNet 训练 + ONNX 导出
模型: 3层下采样 UNet, base_filters=16, 参数 < 1M
输入: 128x128 RGB
输出: 128x128 单通道掩码
使用: python -u train_nail_unet.py
"""
import os, sys, cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

# ----- 轻量 UNet -----
class DoubleConv(nn.Module):
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch), nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch), nn.ReLU(inplace=True),
        )
    def forward(self, x): return self.conv(x)

class UNetNail(nn.Module):
    def __init__(self, in_ch=3, base=16, out_ch=1):
        super().__init__()
        self.enc1 = DoubleConv(in_ch, base)
        self.enc2 = DoubleConv(base, base*2)
        self.enc3 = DoubleConv(base*2, base*4)
        self.pool = nn.MaxPool2d(2)
        self.bridge = DoubleConv(base*4, base*8)
        self.up2 = nn.ConvTranspose2d(base*8, base*4, 2, stride=2)
        self.dec2 = DoubleConv(base*8, base*4)
        self.up1 = nn.ConvTranspose2d(base*4, base*2, 2, stride=2)
        self.dec1 = DoubleConv(base*4, base*2)
        self.up0 = nn.ConvTranspose2d(base*2, base, 2, stride=2)
        self.dec0 = DoubleConv(base*2, base)
        self.out = nn.Conv2d(base, out_ch, 1)
    def forward(self, x):
        e1 = self.enc1(x)
        e2 = self.enc2(self.pool(e1))
        e3 = self.enc3(self.pool(e2))
        b = self.bridge(self.pool(e3))
        d2 = self.dec2(torch.cat([self.up2(b), e3], dim=1))
        d1 = self.dec1(torch.cat([self.up1(d2), e2], dim=1))
        d0 = self.dec0(torch.cat([self.up0(d1), e1], dim=1))
        return torch.sigmoid(self.out(d0))

# ----- 合成数据 -----
def gen_data(n=300, sz=128):
    imgs, msks = [], []
    for _ in range(n):
        img = np.random.randint(180, 255, (sz,sz,3), dtype=np.uint8)
        msk = np.zeros((sz,sz), dtype=np.uint8)
        cx = np.random.randint(sz//4, sz*3//4)
        cy = np.random.randint(sz//3, sz*3//4)
        nh = np.random.randint(40, 90)
        nw = np.random.randint(20, 50)
        for y in range(max(0,cy-nh), min(sz,cy+nh//3)):
            r = (y-(cy-nh))/nh
            if r < 0.2: w = int(nw*0.55*(r/0.2))
            elif r < 0.7: w = int(nw*(0.55+0.45*(r-0.2)/0.5))
            else: w = int(nw*(1.0-0.3*(r-0.7)/0.3))
            w = max(3,w)
            lx, rx = max(0,cx-w), min(sz-1,cx+w)
            if 0<=y<sz:
                img[y,lx:rx] = np.clip(np.array([220,200,180],np.int16)+np.random.randint(-20,20,3),140,255).astype(np.uint8)
                msk[y,lx:rx] = 255
        if np.random.random()>0.5:
            a = np.random.uniform(-15,15)
            M = cv2.getRotationMatrix2D((sz//2,sz//2),a,1.0)
            img = cv2.warpAffine(img,M,(sz,sz),borderMode=cv2.BORDER_REPLICATE)
            msk = cv2.warpAffine(msk,M,(sz,sz),borderMode=cv2.BORDER_CONSTANT)
        imgs.append(img); msks.append(msk)
    return imgs, msks

class NailDataset(Dataset):
    def __init__(self, imgs, msks):
        self.imgs, self.msks = imgs, msks
    def __len__(self): return len(self.imgs)
    def __getitem__(self, i):
        img = torch.FloatTensor(self.imgs[i].astype(np.float32)/255.0).permute(2,0,1)
        msk = torch.FloatTensor(self.msks[i].astype(np.float32)/255.0).unsqueeze(0)
        return img, msk

# ----- 主流程 -----
def main():
    print(f"PyTorch {torch.__version__}, 设备: cpu")
    print("生成合成数据...", flush=True)
    imgs, msks = gen_data(300, 128)
    ds = NailDataset(imgs, msks)
    train_loader = DataLoader(ds, batch_size=16, shuffle=True)
    
    model = UNetNail(3, 16, 1)
    total = sum(p.numel() for p in model.parameters())
    print(f"参数量: {total/1e6:.3f}M", flush=True)
    
    opt = optim.AdamW(model.parameters(), lr=1e-3)
    loss_fn = nn.BCELoss()
    
    print("训练 60 epochs...", flush=True)
    model.train()
    for epoch in range(60):
        loss_sum = 0.0
        for im, mk in train_loader:
            opt.zero_grad()
            pred = model(im)
            loss = loss_fn(pred, mk)
            loss.backward()
            opt.step()
            loss_sum += loss.item()
        if (epoch+1)%10==0:
            print(f"  Epoch {epoch+1:3d}/60 | loss={loss_sum/len(train_loader):.4f}", flush=True)
    
    # 保存权重
    model.eval()
    torch.save(model.state_dict(), "nail_unet.pth")
    print("权重已保存: nail_unet.pth", flush=True)
    
    # 导出 ONNX
    dummy = torch.randn(1,3,128,128)
    print("导出 ONNX...", flush=True)
    try:
        torch.onnx.export(model, dummy, "nail_unet.onnx",
                         input_names=["input"], output_names=["output"],
                         opset_version=11, do_constant_folding=True)
        print("ONNX 已导出: nail_unet.onnx", flush=True)
    except Exception as e:
        print(f"ONNX直接导出失败: {e}", flush=True)
        # 用 TorchScript 方式
        print("尝试 TorchScript 导出...", flush=True)
        traced = torch.jit.trace(model, dummy)
        torch.onnx.export(traced, dummy, "nail_unet.onnx",
                         input_names=["input"], output_names=["output"],
                         opset_version=11, do_constant_folding=True)
        print("ONNX 已导出: nail_unet.onnx (via TorchScript)", flush=True)
    
    # 验证
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession("nail_unet.onnx", providers=["CPUExecutionProvider"])
        inp = sess.get_inputs()[0].name
        out = sess.get_outputs()[0].name
        res = sess.run([out], {inp: dummy.numpy()})
        print(f"ONNX 验证通过: 输出 {res[0].shape}", flush=True)
    except Exception as e:
        print(f"ONNX 验证跳过: {e}", flush=True)
    
    print("完成!", flush=True)

if __name__ == "__main__":
    main()