"""MediaMind uygulama simgesi (macOS Big Sur dili): 1024 px, 4x örnekleme ile çizilir."""
import sys, math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageChops

K = 4                     # örnekleme katsayısı
W = 1024 * K
out = sys.argv[1]

def lin(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

def squircle_mask(size, box, n=5.0):
    x0, y0, x1, y1 = box
    h = np.linspace(-1, 1, x1 - x0); v = np.linspace(-1, 1, y1 - y0)
    X, Y = np.meshgrid(h, v)
    m = (np.abs(X) ** n + np.abs(Y) ** n) <= 1
    img = Image.new("L", (size, size), 0)
    img.paste(Image.fromarray((m * 255).astype(np.uint8)), (x0, y0))
    return img

def gradient(size, stops, angle_deg):
    a = math.radians(angle_deg)
    yy, xx = np.mgrid[0:size, 0:size] / (size - 1)
    t = (xx * math.cos(a) + yy * math.sin(a)); t = (t - t.min()) / (t.max() - t.min())
    out = np.zeros((size, size, 3))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        seg = (t >= p0) & (t <= p1); u = ((t - p0) / (p1 - p0))[seg]
        for k in range(3): out[..., k][seg] = c0[k] + (c1[k] - c0[k]) * u
    return Image.fromarray(out.astype(np.uint8))

def radial(size, cx, cy, r, color, alpha):
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
    a = np.clip(1 - d, 0, 1) ** 2 * alpha
    layer = Image.new("RGBA", (size, size), color + (0,))
    layer.putalpha(Image.fromarray((a * 255).astype(np.uint8)))
    return layer

def rounded(size, w, h, r, fill):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=fill)
    return im

def place(canvas, layer, cx, cy, angle):
    rot = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    canvas.alpha_composite(rot, (int(cx - rot.width / 2), int(cy - rot.height / 2)))

def shadow(layer, blur, alpha, dy):
    a = layer.split()[3].point(lambda v: int(v * alpha))
    sh = Image.new("RGBA", layer.size, (14, 8, 60, 0)); sh.putalpha(a)
    pad = blur * 3
    big = Image.new("RGBA", (layer.width + 2 * pad, layer.height + 2 * pad), (0, 0, 0, 0))
    big.alpha_composite(sh, (pad, pad + dy))
    return big.filter(ImageFilter.GaussianBlur(blur))

def star(size, r_out, r_in, color):
    """Pürüzsüz 4 kollu parıltı: uçtan uca ikinci derece eğriler; r_in/r_out gövdenin dolgunluğunu belirler."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0)); c = size / 2
    k = r_in / r_out  # kontrol noktasının merkezden uzaklık oranı
    tips = [(c, c - r_out), (c + r_out, c), (c, c + r_out), (c - r_out, c)]
    path = []
    for i in range(4):
        p0, p1 = tips[i], tips[(i + 1) % 4]
        kx = c + ((p0[0] + p1[0]) / 2 - c) * k
        ky = c + ((p0[1] + p1[1]) / 2 - c) * k
        for t in np.linspace(0, 1, 64, endpoint=False):
            path.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * kx + t ** 2 * p1[0],
                         (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ky + t ** 2 * p1[1]))
    ImageDraw.Draw(im).polygon(path, fill=color)
    return im


# ---------- arka plan (squircle) ----------
M = 100 * K
box = (M, M, W - M, W - M)
mask = squircle_mask(W, box)
bg = gradient(W, [(0, (88, 86, 246)), (0.55, (124, 64, 238)), (1, (186, 58, 196))], 55).convert("RGBA")
bg.alpha_composite(radial(W, W * 0.28, W * 0.18, W * 0.75, (160, 190, 255), 0.55))
bg.alpha_composite(radial(W, W * 0.85, W * 0.95, W * 0.6, (255, 120, 150), 0.35))
icon = Image.new("RGBA", (W, W), (0, 0, 0, 0))
icon.paste(bg, (0, 0), mask)

# kenarda ince iç parlama (Big Sur hissi)
edge = mask.filter(ImageFilter.GaussianBlur(6 * K))
inner = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(9)))
hl = Image.new("RGBA", (W, W), (255, 255, 255, 0)); hl.putalpha(inner.point(lambda v: int(v * 0.22)))
icon.alpha_composite(hl)

glyph = Image.new("RGBA", (W, W), (0, 0, 0, 0))
cx, cy = W * 0.485, W * 0.545

# ---------- arka kart (buzlu cam) ----------
bw, bh = int(470 * K), int(370 * K)
back = rounded(W, bw, bh, 52 * K, (255, 255, 255, 92))
back_d = ImageDraw.Draw(back)
back_d.rounded_rectangle((0, 0, bw - 1, bh - 1), 52 * K, outline=(255, 255, 255, 140), width=3 * K)
bsh = shadow(back, 26 * K, 0.35, 14 * K)
place(glyph, bsh, cx - 52 * K, cy - 40 * K, 11)
place(glyph, back, cx - 52 * K, cy - 40 * K, 11)

# ---------- ön kart (fotoğraf) ----------
fw, fh = int(500 * K), int(392 * K)
front = rounded(W, fw, fh, 56 * K, (255, 255, 255, 255))
inset = 26 * K
pw, ph = fw - 2 * inset, fh - 2 * inset
photo = gradient(max(pw, ph), [(0, (255, 205, 120)), (0.45, (255, 128, 120)), (1, (156, 92, 236))], 90).crop((0, 0, pw, ph)).convert("RGBA")
pd = ImageDraw.Draw(photo)
# güneş
sun_r = 62 * K; sx, sy = int(pw * 0.68), int(ph * 0.40)
sun = radial(pw, sx, sy, sun_r * 2.6, (255, 240, 200), 0.75).crop((0, 0, pw, ph)); photo.alpha_composite(sun)
pd.ellipse((sx - sun_r, sy - sun_r, sx + sun_r, sy + sun_r), fill=(255, 244, 214))
# dağlar (iki katman)
far = [(0, ph * 0.72), (pw * 0.18, ph * 0.55), (pw * 0.34, ph * 0.66), (pw * 0.55, ph * 0.46), (pw * 0.78, ph * 0.64), (pw, ph * 0.54), (pw, ph), (0, ph)]
pd.polygon(far, fill=(116, 88, 214))
near = [(0, ph * 0.86), (pw * 0.22, ph * 0.68), (pw * 0.42, ph * 0.84), (pw * 0.63, ph * 0.66), (pw * 0.88, ph * 0.88), (pw, ph * 0.80), (pw, ph), (0, ph)]
pd.polygon(near, fill=(64, 52, 168))
pm = Image.new("L", (pw, ph), 0); ImageDraw.Draw(pm).rounded_rectangle((0, 0, pw - 1, ph - 1), 34 * K, fill=255)
front.paste(photo, (inset, inset), pm)
fsh = shadow(front, 30 * K, 0.45, 22 * K)
place(glyph, fsh, cx + 18 * K, cy + 34 * K, -6)
place(glyph, front, cx + 18 * K, cy + 34 * K, -6)

# ---------- parıltı (zekâ) ----------
st_size = 360 * K
glow = star(st_size, 140 * K, 40 * K, (255, 226, 160, 255)).filter(ImageFilter.GaussianBlur(30 * K))
s1 = star(st_size, 112 * K, 26 * K, (255, 255, 255, 255))
spx, spy = W * 0.715, W * 0.275
for lay in (glow, s1):
    glyph.alpha_composite(lay, (int(spx - st_size / 2), int(spy - st_size / 2)))
s2 = star(160 * K, 46 * K, 11 * K, (255, 255, 255, 230))
glyph.alpha_composite(s2, (int(W * 0.835 - 80 * K), int(W * 0.415 - 80 * K)))

icon.alpha_composite(glyph)
# simge şeklinin dışına taşan gölgeleri kırp, ama hafif dış gölge ekle
clipped = Image.new("RGBA", (W, W), (0, 0, 0, 0)); clipped.paste(icon, (0, 0), mask)
outer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
os_ = Image.new("RGBA", (W, W), (0, 0, 20, 0)); os_.putalpha(mask.point(lambda v: int(v * 0.30)))
outer.alpha_composite(os_, (0, 10 * K)); outer = outer.filter(ImageFilter.GaussianBlur(14 * K))
final = Image.new("RGBA", (W, W), (0, 0, 0, 0)); final.alpha_composite(outer); final.alpha_composite(clipped)
final.resize((1024, 1024), Image.LANCZOS).save(out)
print("ok", out)
