# Simai Chart Renderer

Simai 谱面可视化渲染工具 -- 将 Maimai DX / 中二节奏的 Simai 格式谱面解析并渲染为精美图片。

## 功能特性

- 支持三种输入方式：
  - `-t` / `--text`：直接输入 Simai 文本
  - `-f` / `--file`：读取 .txt 谱面文件
  - `-z` / `--zip`：解析压缩包（含 maidata.txt + 背景图）
- 完整的 Simai 语法支持：Tap、Hold、Touch、Slide、Break、EX、Each
- 自动 BPM 检测与分音标注
- 多难度选择（压缩包模式）
- 烟花特效渲染
- 高清大图输出（3072px 高度）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 基本用法

**直接输入谱面文本：**
```bash
python main.py -t "(120){4}1,3,5,7,2h[4:1],4,6,8," -o output.png
```

**从文件读取：**
```bash
python main.py -f input.simai -o output.png
```

**从压缩包读取（含多难度选择）：**
```bash
python main.py -z "Fine Logic no pv.zip" -o output.png
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-t`, `--text` | 直接输入 Simai 谱面文本 |
| `-f`, `--file` | 谱面文件路径（.txt） |
| `-z`, `--zip` | 压缩包路径（含 maidata.txt + 背景图） |
| `-o`, `--output` | 输出图片路径（默认: output.png） |

> 注意：-t、-f、-z 三个参数互斥，必须且只能使用其中一个。

## 项目结构

```
chartForSimai/
├── main.py              # 命令行入口
├── simai_parser.py      # Simai 谱面语法解析器
├── simai_maidx.py       # maidata.txt 压缩包解析模块
├── simai_render.py      # 谱面渲染引擎
├── requirements.txt     # Python 依赖
├── src/                 # 贴图资源
│   ├── tap.png          # Tap 音符
│   ├── hold.png         # Hold 音符
│   ├── slide.png        # Slide 滑条
│   ├── star.png         # Slide 星星
│   ├── touch.png        # Touch 音符
│   ├── touchhold.png    # Touch Hold
│   └── ...              # 各变种（break / each / ex）
└── unknown.jpg          # 默认背景图
```

## 许可证

本项目基于 MIT 许可证开源，详见 LICENSE 文件。
