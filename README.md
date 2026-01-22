# 🚀 Haru Runtime GM Console

一款基于 **NiceGUI** 构建的轻量级、响应式游戏运行时 GM 指令控制台工具。专为跨平台游戏开发调试设计，支持 PC 与 Android 设备。

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![UI Framework](https://img.shields.io/badge/UI-NiceGUI-orange.svg)](https://nicegui.io/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## ✨ 核心特性

- 📱 **多跨平台支持**：支持 PC 调试模式及 Android 手机（通过 ADB 转发）。
- 🎨 **现代化 UI**：暗黑模式设计，采用 Slate 调色盘与 Inter/Fira Code 字体，视觉极致享受。
- 🔍 **动态 GM 浏览器**：支持动态推送的 GM 指令树，提供下钻导航、面包屑路径及实时全局搜索。
- ⌨️ **代码实时执行**：内置全功能 Lua 编辑器，支持代码一键执行与广播。
- 🛠️ **自定义 GM 库**：可灵活存储与管理常用 GM 命令，打造个人专属调试套件。
- ⚡ **异步性能**：基于 `asyncio` 与 `socket`，响应极速，支持多设备同时接入。

## 🛠️ 环境要求

- **Python 3.9+**
- **pip** (Python 包管理器)

## 🚀 快速启动

1. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

2. **启动控制台**
   ```bash
   python gm_console.py
   ```
   启动后访问：[http://localhost:9529](http://localhost:9529)

## 📖 使用指南

### 1. 基础流程
1. 启动本工具。
2. 启动游戏客户端（需开启 `Debug` 相关配置）。
3. 游戏会自动连接至本工具，侧边栏将显示在线设备。
4. 在 Lua 执行区输入代码并点击运行，或在 GM 浏览器中直接交互。

### 2. Android 设备连接
1. 通过 USB 将手机连接至电脑并开启 ADB 调试。
2. 在终端执行以下命令进行端口转发：
   ```bash
   # 默认端口 12581
   adb reverse tcp:12581 tcp:12581
   ```
3. 启动手机端的运行，设备将自动出现在连接列表中。

## 🎨 设计规范

- **主题色**：Primary `#3B82F6` | Background `#0F172A`
- **图标集**：集成轻量化 SVG 图标库
- **交互**：完全响应式布局，完美适配不同尺寸显示器

---

*由 Antigravity 强力驱动 🛠️*
