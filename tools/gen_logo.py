"""PyEngineSim logo: a hot-side turbo — dark housing, glowing turbine wheel."""
import math
import os
os.environ['SDL_VIDEODRIVER'] = 'dummy'
import pygame

pygame.init()
S = 512
surf = pygame.Surface((S, S), pygame.SRCALPHA)
cx, cy = 250, 268                    # housing centre (room for actuator top-right)
R = 176                              # housing outer radius

# ---------------- housing: volute scroll + round turbine housing -------------
# volute: the scroll grows around the housing (offset fat ring behind)
for ang0, rr, off in ((210, R + 26, 14), (300, R + 14, 8)):
    a = math.radians(ang0)
    ox, oy = cx + off * math.cos(a), cy + off * math.sin(a)
    pygame.draw.circle(surf, (40, 43, 50), (int(ox), int(oy)), rr)
pygame.draw.circle(surf, (48, 52, 60), (cx, cy), R)              # main housing
pygame.draw.circle(surf, (26, 28, 33), (cx, cy), R, 6)           # dark rim
pygame.draw.circle(surf, (72, 77, 88), (cx, cy), R - 10, 3)      # inner ring lip
# subtle top-left sheen
sheen = pygame.Surface((S, S), pygame.SRCALPHA)
pygame.draw.circle(sheen, (255, 255, 255, 16), (cx - 46, cy - 52), R - 26)
surf.blit(sheen, (0, 0))
# bolts around the rim
for k in range(6):
    a = math.radians(60 * k + 12)
    bx = cx + (R - 22) * math.cos(a)
    by = cy + (R - 22) * math.sin(a)
    pygame.draw.circle(surf, (96, 102, 114), (int(bx), int(by)), 8)
    pygame.draw.circle(surf, (22, 24, 28), (int(bx), int(by)), 8, 2)

# outlet duct + flange (drawn AFTER the housing so the stub reads clearly)
duct_w = 84
pygame.draw.rect(surf, (44, 47, 54), (cx + R - 16, cy - duct_w // 2, 96, duct_w),
                 border_radius=12)
pygame.draw.rect(surf, (24, 26, 30), (cx + R - 16, cy - duct_w // 2, 96, duct_w),
                 3, border_radius=12)
fx = cx + R + 66
pygame.draw.rect(surf, (58, 62, 70), (fx, cy - duct_w // 2 - 14, 24, duct_w + 28),
                 border_radius=8)
pygame.draw.rect(surf, (26, 28, 33), (fx, cy - duct_w // 2 - 14, 24, duct_w + 28),
                 3, border_radius=8)
for by in (cy - duct_w // 2 - 2, cy + duct_w // 2 + 2):
    pygame.draw.circle(surf, (110, 116, 128), (fx + 12, by), 7)
    pygame.draw.circle(surf, (20, 22, 26), (fx + 12, by), 7, 2)

# ---------------- glowing turbine wheel --------------------------------------
BR = 118                              # blade tip radius
pygame.draw.circle(surf, (14, 13, 15), (cx, cy), BR + 14)        # dark bore
# radial heat glow (additive-ish stacked translucent circles)
glow = pygame.Surface((S, S), pygame.SRCALPHA)
for rr, col in ((BR + 10, (255, 110, 15, 44)), (BR - 8, (255, 150, 30, 66)),
                (BR - 34, (255, 185, 55, 92)), (BR - 62, (255, 215, 95, 120))):
    pygame.draw.circle(glow, col, (cx, cy), rr)
surf.blit(glow, (0, 0))
# 11 radiating blades, slightly swept (each a curved-ish tapered quad)
NB = 11
for k in range(NB):
    a = 2 * math.pi * k / NB
    a2 = a + 0.32                     # sweep the tip forward -> swirl feel
    hx, hy = cx + 24 * math.cos(a), cy + 24 * math.sin(a)
    tx, ty = cx + BR * math.cos(a2), cy + BR * math.sin(a2)
    # blade edges: offset the root perpendicular +/- for taper
    px, py = -math.sin(a), math.cos(a)
    pts = [(hx + 11 * px, hy + 11 * py), (tx + 3 * px, ty + 3 * py),
           (tx - 3 * px, ty - 3 * py), (hx - 11 * px, hy - 11 * py)]
    pygame.draw.polygon(surf, (255, 196, 60), pts)
    pygame.draw.polygon(surf, (255, 236, 150), pts, 2)
# white-hot hub
for rr, col in ((30, (255, 214, 90)), (20, (255, 240, 170)), (10, (255, 255, 230))):
    pygame.draw.circle(surf, col, (cx, cy), rr)
pygame.draw.circle(surf, (150, 90, 20), (cx, cy), 5)             # shaft nut

# ---------------- wastegate actuator pod (top-right) -------------------------
ax, ay = cx + 108, cy - R - 6
pygame.draw.line(surf, (30, 32, 38), (ax - 6, ay + 30), (cx + 70, cy - 96), 10)
pygame.draw.ellipse(surf, (58, 62, 72), (ax - 34, ay - 24, 68, 48))
pygame.draw.ellipse(surf, (26, 28, 33), (ax - 34, ay - 24, 68, 48), 3)
pygame.draw.ellipse(surf, (86, 92, 104), (ax - 22, ay - 15, 44, 18))
pygame.draw.circle(surf, (110, 116, 128), (ax, ay - 24), 6)      # hose nipple

# ---------------- floating heat pixels (cute sparks) -------------------------
for px_, py_, s_, col in ((72, 96, 10, (255, 170, 40)), (108, 70, 7, (255, 210, 90)),
                          (60, 150, 6, (255, 140, 30)), (430, 120, 8, (255, 190, 60))):
    pygame.draw.rect(surf, col, (px_, py_, s_, s_), border_radius=2)

out = r"D:\EngineSim\engine-sim-community-edition\engine-sim-py\engine_sim\assets\logo.png"
os.makedirs(os.path.dirname(out), exist_ok=True)
pygame.image.save(pygame.transform.smoothscale(surf, (256, 256)), out)
prev = r"C:\Users\ZIYU~1.LIU\AppData\Local\Temp\claude\d--EngineSim-engine-sim-community-edition\d0a314c2-0b3e-44cf-95fb-9853266f5a13\scratchpad\logo_prev.png"
# preview on the app's dark background so I can judge it in context
bg = pygame.Surface((S, S))
bg.fill((22, 24, 28))
bg.blit(surf, (0, 0))
pygame.image.save(bg, prev)
print("logo saved:", out)
