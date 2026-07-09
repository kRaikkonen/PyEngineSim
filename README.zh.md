# PyEngineSim（中文）

**用纯 Python 编写的实时引擎 *声音 + 机械* 模拟器。**
作者 **Leo** · `v0.9` · [🇬🇧 English README](README.md) · 🇨🇳 中文（本文件）

![PyEngineSim — 福特 GT 3.5 V6](docs/screenshot_fordgt.png)

PyEngineSim 从第一性原理对四冲程发动机建模 —— 曲轴/连杆/活塞运动学、有限燃烧
热力学循环、容积效率进气、涡轮/机械增压能量平衡、循环温度、曲轴刚体动力学 ——
并把排气脉冲实时合成为 **引擎声音**。它绘制带动画的发动机舱（活塞、配气机构、涡轮、
歧管）、完整仪表盘、一套物理 **分析仪（Analyzer）**，并内置 **130+ 真实发动机预设**
—— 从直三到布加迪 **W16**、F1 **V10/V8**、梅林 **V12** 航空发动机、转子（汪克尔）、
大排量 **柴油** 和 **混动**。

它还能 **通过 UDP 跟随真实的极限竞速（Forza）游戏** —— 你在 Forza Horizon /
Motorsport 里开车，PyEngineSim 实时按相同转速咆哮对应的发动机。

---

## ✨ 与众不同之处：它是**白盒**的

这里没有任何"画得像模像样"的手调曲线。发动机的每一个环节都由物理重建，而那些数字
是从物理中**自己长出来**的：

- **进气呼吸** —— 容积效率来自 Taylor 马赫指数（高转窒息）、Engelman/Helmholtz 进气
  谐振调谐（中段扭矩驼峰）和残余废气回流，全部基于发动机真实的缸径/行程/进气道几何，
  而不是一条高斯钟形曲线。
- **燃烧** —— 有限燃烧的 **Wiebe** 放热配真实点火提前角图；峰值缸压落在上止点后几度，
  和真实压力曲线一样。
- **扭矩** —— 平均 BMEP 由"空气受限"的能量核算得出（`IMEP = η_otto · η_shape ·
  (LHV/AFR) · ρ · VE`），并按真实充量温度做爆震降扭。
- **强制进气** —— 涡轮增压来自涡轮/压气机**能量平衡**，带真实迟滞（`τ ~ J_turbo /
  排气功率`）；roots 与离心机械增压分别由容积式与叶尖速²物理得出；含增压充量加热 +
  中冷 + 热浸。
- **排气声** —— 真实的吹泄脉冲串穿过白盒排气声学（喇叭口、三元/GPF、共振腔、消音器），
  加上结构传导的缸体辐射。
- **ERS / 混动** —— MGU-K 释放/回收、MGU-H 热能回收、一套电池荷电状态。

因为全是物理，**显示的 dyno 就是发动机真正做出来的**，整个车队都**按真实规格标定**：
130 台车都做出各自的额定功率和扭矩（车队相对真实规格的中位数 = **1.00**）。凡是被
电子**扭矩/功率限制**的发动机（被限死在约 1000 N·m 的 AMG/阿斯顿双涡轮），都用真实的
ECU 包络建模，而不是糊弄。

**分析仪（按 `E`）** 里的每一块表 —— 燃烧脉冲、排气流量、气门升程、缸压、点火提前角、
扭矩/马力 dyno —— 都直接由运行中的物理绘制。

---

## ⬇️ 下载与运行

| 平台 | 方式 |
|---|---|
| **Windows（快）** | 解压 `PyEngineSim-onedir.zip`，运行 `PyEngineSim/PyEngineSim.exe` |
| **Windows（单文件）** | `PyEngineSim-onefile.zip` → 单个 `.exe`（首次启动较慢） |
| **安卓（arm64）** | 侧载 `pyenginesim-0.9-arm64-v8a-debug.apk` —— 触屏 UI、SDL2 音频、不需要 scipy |
| **源码运行** | `pip install numpy scipy sounddevice pygame` → `python run.py` |

启动默认加载 **兰博基尼 Aventador V12**；可在 **Demo cars ▾** 菜单切换任意发动机。
桌面端 `scipy` 会让几个音频滤波更锐利，但它是可选的 —— 每一处 scipy 调用都有纯 numpy
兜底（这也正是安卓路径）。

---

## 🚗 车库（130+）

自吸高转机器（Aventador V12、LFA V10、458、F1 **V10/V8**）、涡轮传奇（F40、2JZ、
R35、911 Turbo/GT2）、机械增压（地狱猫、GT500、F-Type）、转子（787B 四转子、RX-7）、
大排柴油（康明斯、Actros、Iron Knight）、混动与 F1 动力单元（918、P1、FXX-K、SF-25），
以及像喷火战斗机 **梅林 V12** 和 Wildcat 星型发动机这样的奇葩。每台车都带着自己真实的
几何、点火顺序、凸轮型线、进气方式与排气硬件 —— 所以它们听起来、开起来都像自己，而不是
一台通用 V8。

---

## 🎮 连接真实的 Forza 游戏（Data Out → PyEngineSim）

Forza Horizon 4/5 与 Forza Motorsport 能通过 UDP 输出实时遥测，PyEngineSim 会监听并
让发动机跟随游戏转速。

1. **在 PyEngineSim 中：** 点击工具栏的 **`Forza`**。🔴 红 = 正在监听但还没数据；
   🟢 绿 = 已收到数据包。监听 **UDP 端口 `5300`**。
2. **在 Forza 游戏中：** **设置 → HUD 与游戏**（Horizon）/ **游戏与 HUD**
   （Motorsport）：**Data Out：`开`**、**端口：`5300`**，并（Horizon）选 **"Dash"** 格式。
3. 开始驾驶 —— 发动机跟随游戏的转速 / 油门 / 增压。

> **该填哪个 IP？** 同机 **Steam** 版 → `127.0.0.1`。同机 **微软商店 / Game Pass**
> （沙盒 UWP，回环被禁）→ 本机的 **局域网 IP**（`ipconfig` 查看）。不同电脑 → 运行
> PyEngineSim 那台电脑的局域网 IP。

---

## ⚡ 性能模式

舱体渲染是最重的部分（W16 要画的东西很多）；物理 + 音频都很轻（音频每 8 毫秒的块只要
约 1 毫秒）。两个开关用画质换 CPU，保证音频不爆音：

- **`Low Q`** —— 一切画成无阴影/无闪光/无半透明的纯色实心形状。大约把重帧砍掉一半；
  数据完全不变。
- **`Forza Ultra`** —— 除了它自己的按钮、**Demo cars** 菜单和一个 **Mixer/EQ** 开关，
  屏幕什么都不画。发动机照常运行并跟随 Forza（约 0.9 毫秒/帧），几乎把整颗 CPU 让给
  游戏 + 音频。**实际比赛时最佳。**

---

## ⌨️ 操作

| 按键 | 功能 | | 按键 | 功能 |
|---|---|---|---|---|
| `↑ / ↓` | 油门 / 刹车 | | `A` | 点火开关 |
| `Shift`（按住） | 离合 | | `S`（按住） | 起动机 |
| `X` | 升挡 | | `Z` | 降挡 |
| `T` | 自动 / 手动 | | `C` | 混音 / EQ |
| `E` | 分析仪示波器 | | `M` | 静音 |
| `V` | 点火音色 | | `Esc` | 退出 |

右上角的 **Touch** 开关会弹出屏幕踏板/拨片；安卓上默认开启。

---

## 🔬 内部原理

每一缸每帧都被推进完整的四冲程循环：曲柄滑块运动学 → 绝热压缩 → Wiebe 点火放热 →
膨胀 → 排气吹泄。缸压经虚功转为曲轴扭矩；所有缸的扭矩 + 起动机 + 摩擦 + 负载作为曲轴
刚体动力学一起积分。打滑离合、变速箱、主减速比和车重让你能起步、熄火、驾驶。每一次
排气门开启都往音频流里盖下一个衰减脉冲 —— **脉冲串就是发动机声。**

运行时使用快速的闭式白盒模型（`ve_model`、`map_model`、`bmep_model`），加载时烘焙成
小查找表；一个离线的第一性原理气体模型（`gas_truth`、`gas_moc`）是它们被对齐一致的
"真理"。全部是纯 Python + NumPy，没有一台车的糊弄图。

| 原版（C++） | 这里（Python） |
|---|---|
| `delta-studio` 渲染器 | `pygame` 窗口 + 绘制 |
| 2D 约束求解器 | 解析曲柄滑块 + 欧拉积分 |
| `.mr` 发动机脚本 | `presets.py` 发动机构建器 |
| 脉冲响应合成 | 白盒排气脉冲合成器（`audio.py`） |

---

## 🛠️ 自行构建

- **Windows exe** —— `pip install pyinstaller`，然后 `pyinstaller
  packaging/PyEngineSim.spec`（文件夹版）或 `packaging/PyEngineSim-onefile.spec`（单文件）。
- **安卓 apk** —— 在 Linux/WSL 上 `buildozer android debug`（见 `buildozer.spec`）。
  pygame/SDL2 只能在 **python-for-android `v2023.09.16`（Python 3.10）+ NDK r25b** 下编译；
  更新的组合会在 `longintrepr.h` / `ALooper` 处失败。安卓上 scipy 被丢弃（纯 numpy 兜底）。

---

## 🙏 致谢

PyEngineSim 的灵感与最大的功劳来自
**[AngeTheGreat](https://github.com/ange-yaghi)** 和他的原版 C++
**[Engine Simulator](https://github.com/ange-yaghi/engine-sim)** —— 请去看他的视频并给
原项目点 Star。本项目是**独立重新实现**，与原版不共享任何代码。

---

*PyEngineSim `v0.9` —— 作者 **Leo**。一个纯 Python、完全白盒的发动机模拟器。特别感谢
**AngeTheGreat** 的 Engine Simulator。*
