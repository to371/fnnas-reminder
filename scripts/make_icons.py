"""生成应用图标（纯标准库，无需 Pillow）。

输出：
  fpk-src/ICON.PNG           64   包自身图标（fnOS 应用中心列表用）
  fpk-src/ICON_256.PNG      256   高清包图标
  app/ui/images/icon_64.png       桌面入口图标（manifest 里 desktop_uidir=ui，
  app/ui/images/icon_256.png      所以必须在 app/ui/ 下、且文件名是下划线）
"""
import os
import struct
import zlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(BASE, "fpk-src")
# 桌面图标放在 app/ui/ 下：desktop_uidir 是相对「应用目录」解析的，
# 放到 fpk-src/ui/ 装机后图标不会出现（踩过）。
UI_IMAGES = os.path.join(BASE, "app", "ui", "images")


def write_png(path, size, pixels):
    raw = b"".join(b"\x00" + bytes(pixels[y]) for y in range(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)


def render(size, ss=4):
    n = size * ss
    rows = []
    c1, c2 = (0x2F, 0x6B, 0xFF), (0x7A, 0xA2, 0xFF)
    r = n * 0.22          # 圆角半径
    for y in range(size):
        row = []
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    px = x * ss + sx + 0.5
                    py = y * ss + sy + 0.5
                    # 圆角矩形内判定
                    dx = max(r - px, px - (n - r), 0)
                    dy = max(r - py, py - (n - r), 0)
                    inside = (dx * dx + dy * dy) <= r * r
                    if not inside:
                        continue
                    t = (px + py) / (2 * n)
                    col = [c1[i] + (c2[i] - c1[i]) * t for i in range(3)]
                    a = 1.0
                    # 表盘白环
                    cx = cy = n / 2
                    d = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
                    if 0.245 * n <= d <= 0.305 * n:
                        col, a = [255, 255, 255], 1.0
                    # 指针
                    if d < 0.20 * n:
                        if abs(px - cx) < 0.030 * n and py <= cy + 0.02 * n and py >= cy - 0.16 * n:
                            col = [255, 255, 255]
                        elif abs(py - cy) < 0.030 * n and px >= cx - 0.02 * n and px <= cx + 0.115 * n:
                            col = [255, 255, 255]
                    acc[0] += col[0] * a
                    acc[1] += col[1] * a
                    acc[2] += col[2] * a
                    acc[3] += a
            k = ss * ss
            if acc[3] == 0:
                row += [0, 0, 0, 0]
            else:
                row += [int(acc[0] / acc[3]), int(acc[1] / acc[3]), int(acc[2] / acc[3]), int(255 * acc[3] / k)]
        rows.append(row)
    return rows


if __name__ == "__main__":
    big = render(256)
    small = render(64, ss=6)
    write_png(os.path.join(ROOT, "ICON_256.PNG"), 256, big)
    write_png(os.path.join(ROOT, "ICON.PNG"), 64, small)
    write_png(os.path.join(UI_IMAGES, "icon_256.png"), 256, big)
    write_png(os.path.join(UI_IMAGES, "icon_64.png"), 64, small)
    print("icons written")
