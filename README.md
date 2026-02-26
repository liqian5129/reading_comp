# AI 读书搭子 (Reading Companion)

通过 USB 摄像头拍书页 → 透视矫正 → OCR 提取文字，按住右⌥键说话与 AI 实时对话（AI 既能"看"书页又能听到提问），TTS 播报 AI 回复。读完后自动推送飞书富文本卡片总结；不在电脑旁时可通过飞书 bot 继续聊书。

## 功能特性

- 📷 **自动扫描**：每 2 秒自动拍摄书页，透视矫正 + OCR 识别
- 🎤 **语音交互**：按住右 Alt 键说话，实时 ASR 转文字
- 🤖 **AI 对话**：Kimi 2.5 支持，能"看"书页内容
- 🔊 **TTS 播报**：阿里云语音合成，自动播报 AI 回复
- 📝 **笔记记录**：语音指令记录读书笔记
- 📊 **飞书推送**：阅读结束后推送总结卡片
- 💬 **飞书 Bot**：通过飞书继续与 AI 聊书

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 生成配置文件

```bash
# 使用交互式向导生成 config.json
python setup.py
```

向导会询问以下信息：
- **Kimi API Key**: 从 https://platform.moonshot.cn/ 获取
- **阿里云 NLS App Key**: 从 https://nls-portal.console.aliyun.com/ 获取
- **阿里云 NLS Token**: 同上，用于 ASR 和 TTS
- **飞书配置**（可选）: 从 https://open.feishu.cn/app/ 创建应用获取

也可以手动复制模板：

```bash
cp config.json.example config.json
# 然后编辑 config.json 填写你的密钥
```

### 3. 验证配置

```bash
python config.py
```

### 4. 运行测试

```bash
python test_basic.py
```

### 5. 启动程序

```bash
python main.py
```

## 使用指南

### 语音指令

按住 **右 Alt 键** 说话，松开后 AI 会回答：

| 指令 | 说明 |
|------|------|
| "开始读书" / "打开书" | 开始新的阅读会话，启动自动扫描 |
| "看看这页" / "拍一下" | 手动拍摄当前页面 |
| "这页讲了什么" | 询问 AI 当前页面内容 |
| "记录一下..." / "摘抄这段" | 记录笔记 |
| "读完了" / "结束阅读" | 结束会话，推送飞书总结 |
| "今天读了什么" | 查询今日阅读历史 |

### 飞书 Bot

在飞书群中 @机器人或私聊：
- "今天读了什么" - 查看今日阅读总结
- 任何问题都可以问，AI 会结合阅读历史回答

## 配置文件说明

`config.json` 包含以下部分：

```json
{
  "ai": {
    "api_key": "你的 Kimi API Key",
    "model": "kimi-latest",
    "base_url": "https://api.moonshot.cn/v1"
  },
  "aliyun_nls": {
    "app_key": "你的 NLS App Key",
    "token": "你的 NLS Token",
    "access_key_id": "可选：用于自动获取 Token",
    "access_key_secret": "可选"
  },
  "tts": {
    "voice": "zh-CN-XiaoxiaoNeural",
    "player_cmd": "afplay"
  },
  "feishu": {
    "enabled": false,
    "app_id": "cli_xxx",
    "app_secret": "xxx"
  },
  "camera": {
    "device": 0,
    "auto_scan_interval": 2
  },
  "data": {
    "data_dir": "./data"
  }
}
```

### 环境变量（可选）

如果不想用配置文件，也可以通过环境变量设置（优先级更高）：

```bash
export KIMI_API_KEY="your-key"
export ALIYUN_NLS_APP_KEY="your-key"
export ALIYUN_NLS_TOKEN="your-token"
export FEISHU_APP_ID="cli-xxx"
export FEISHU_APP_SECRET="xxx"
```

## 项目结构

```
reading_comp/
├── main.py              # 启动入口
├── config.py            # 配置管理（支持 config.json + 环境变量）
├── setup.py             # 交互式配置向导
├── test_basic.py        # 基础测试脚本
├── requirements.txt     # 依赖
├── config.json.example  # 配置文件模板
├── README.md            # 使用文档
│
├── camera/              # 摄像头模块
│   ├── capture.py       # 图像捕获
│   ├── perspective.py   # 透视矫正
│   └── page_tracker.py  # 翻页检测
│
├── ocr/                 # OCR 模块
│   └── engine.py        # PaddleOCR 封装
│
├── voice/               # 语音模块
│   ├── recorder.py      # 录音（pynput + sounddevice）
│   └── asr.py           # 阿里云 NLS 实时 ASR
│
├── tts/                 # TTS 模块
│   └── speaker.py       # 阿里云 TTS + 播放
│
├── agent/               # AI Agent
│   ├── kimi_client.py   # Kimi 客户端
│   ├── memory.py        # 记忆系统
│   └── tools.py         # 工具定义与执行
│
├── session/             # 会话管理
│   ├── models.py        # 数据模型
│   ├── storage.py       # SQLite 存储
│   └── manager.py       # 会话管理器
│
├── scanner/             # 自动扫描
│   └── auto_scanner.py  # 后台扫描器
│
├── feishu/              # 飞书集成
│   ├── bot.py           # WebSocket Bot
│   └── push.py          # 消息推送
│
└── data/                # 数据目录
    ├── sessions.db      # SQLite 数据库
    ├── snapshots/       # 书页图片
    ├── notes/           # 笔记导出
    └── persona.json     # 用户画像
```

## 技术选型

| 模块 | 技术 | 说明 |
|------|------|------|
| 主框架 | Python 3.11+, asyncio | 异步 + 多线程混合 |
| 摄像头 | OpenCV (cv2) | VideoCapture |
| 透视矫正 | OpenCV warpPerspective | Canny + 轮廓检测 |
| OCR | PaddleOCR | use_angle_cls=True, lang='ch' |
| 按键检测 | pynput | 全局监听右 Alt 键 |
| 录音 | sounddevice | 16kHz PCM |
| ASR | 阿里云 NLS | 实时流式识别 |
| TTS | 阿里云 NLS | 流式合成 |
| AI | Moonshot Kimi | 支持视觉 + 工具调用 |
| 存储 | SQLite + aiosqlite | 异步数据库 |
| 飞书 | lark-oapi | WebSocket 长连接 |

## 获取 API 密钥

### 1. Kimi API Key

1. 访问 https://platform.moonshot.cn/
2. 注册/登录账号
3. 进入「控制台」→「API Key 管理」
4. 创建 API Key
5. 复制到 `config.json` 的 `ai.api_key`

**注意**：Kimi API 使用 OpenAI 兼容格式，支持：
- 文本对话
- 视觉输入（图片理解）
- 工具调用（Function Calling）

### 2. 阿里云 NLS

1. 访问 https://nls-portal.console.aliyun.com/
2. 创建新项目
3. 获取 App Key
4. 在服务管控台创建 Token
5. 复制到 `config.json` 的 `aliyun_nls.app_key` 和 `aliyun_nls.token`

**注意**：Token 有过期时间，可以配置 `access_key_id` 和 `access_key_secret` 自动刷新。

### 3. 飞书应用（可选）

1. 访问 https://open.feishu.cn/app/
2. 创建企业自建应用
3. 在"凭证与基础信息"中获取 App ID 和 App Secret
4. 在"权限管理"中添加：`im:message:send` 和 `im:message.group_msg`
5. 发布应用并添加到群聊或个人使用

## 常见问题

### 1. PaddleOCR 首次运行下载模型

第一次运行时会自动下载 OCR 模型，需要联网，请耐心等待。

### 2. 摄像头权限（macOS）

如果出现摄像头无法打开：
- 系统设置 → 隐私与安全性 → 摄像头
- 给终端（Terminal/iTerm）授权

### 3. 右 Alt 键在 Windows 上无效

Windows 没有右 Alt 键概念，可以修改 `voice/recorder.py` 中的 `trigger_key`：
```python
trigger_key=keyboard.Key.cmd  # 改为左 Win/Cmd 键
```

### 4. 播放器命令

- macOS: `afplay`（默认）
- Linux: `aplay`, `mpg123`, `mpg321`, `cvlc`
- Windows: 需要安装播放器并添加到 PATH

### 5. Kimi API 调用失败

检查：
1. API Key 是否正确
2. 账户是否有足够余额
3. 模型名称是否正确（如 `kimi-latest` 或 `kimi-k2-5`）

## License

MIT
