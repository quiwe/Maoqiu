# 🐱 毛球 AI 助手

一个运行在终端的 AI 助手，基于 OpenAI 兼容 API，支持在 Windows 系统上执行命令。

## ✨ 功能特点

- 💬 自然语言对话，像和朋友聊天一样
- ⚡ 自动执行终端命令（文件查询、代码运行等）
- 🔧 首次运行自动引导配置，填入 API KEY 即可使用
- 🪟 专为 Windows 系统优化

## 🚀 一键安装

### 方式一：Git 克隆（推荐）

```bash
git clone https://github.com/quiwe/Maoqiu.git
cd Maoqiu
pip install -r requirements.txt
python agent.py
```

### 方式二：下载 ZIP

1. 点击页面右上角 **Code** → **Download ZIP**
2. 解压到任意目录
3. 在该目录打开终端，运行：

```bash
pip install -r requirements.txt
python agent.py
```

## ⚙️ 配置说明

首次运行时，毛球会引导你输入：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| API KEY | 大模型服务的密钥 | `sk-xxxx` |
| API 基础地址 | 服务端点 URL | `https://api.openai.com/v1` |
| 模型名称 | 要使用的模型 | `gpt-4o`、`mimo-v2.5-pro` |

配置保存在 `config.json`，后续启动自动加载。

## 📖 使用方法

启动后直接输入自然语言，毛球会自动判断是否需要执行命令：

```
 You > 帮我看看当前目录有什么文件

毛球正在执行命令 dir
 毛球 > 当前目录包含以下文件：...
```

输入 `exit` 退出程序。

## 📁 项目结构

```
Maoqiu/
├── agent.py          # 主程序
├── config.json       # 配置文件（自动生成，已 gitignore）
├── requirements.txt  # Python 依赖
└── README.md         # 项目说明
```

## 📝 许可证

MIT License
