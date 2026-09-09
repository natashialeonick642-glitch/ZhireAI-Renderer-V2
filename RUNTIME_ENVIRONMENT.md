# 运行环境

## 已验证环境

- 操作系统：Windows 11 64 位
- Cinema 4D：2025.3.3
- Python：Cinema 4D 自带 Python 3.11
- 网络：能够访问 `https://api.juaihub.cn`
- 外部 Python 包：无

## 兼容范围

插件代码兼容 Cinema 4D 2023、2024、2025、2026 所带的 Python 3 环境，但本发布版只在 Cinema 4D 2025.3.3 完成了完整生成测试。其他版本应先运行环境检测和插件静态检查，再进行实际生成。

## 必需软件

- Windows 10 或 Windows 11 64 位
- 已合法安装并启动过一次的 Cinema 4D
- PowerShell 5.1 或更高版本，用于一键安装和校验
- 用户自己的挚热 API Key

Cinema 4D、系统组件和 API Key 不随本项目分发。

## 检测

在解压目录中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Test-Environment.ps1
```

只校验发布包而不安装：

```powershell
powershell -ExecutionPolicy Bypass -File .\Verify-Package.ps1
```

## 本地数据位置

插件代码安装到当前 C4D 用户配置目录下的 `plugins\zhireAI`。API Key、历史记录、参考图索引和生成结果保存在用户自己的 C4D 配置目录或自选输出目录，不属于发布包。
