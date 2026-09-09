# 挚热AI渲染器 V2

面向 Cinema 4D 产品视觉工作的 AI 渲染插件。插件从当前 C4D 场景生成白模结构图与 Z 深度约束图，并把场景、深度、参考图和提示词提交到用户配置的挚热 API。

GitHub 发布版本：`0.1.0`  
插件内部版本：`2.0.7`

## 主要能力

- 使用当前活动摄像机和画幅生成白模参考图与近白远黑的 Z 深度图。
- 支持摄像机胶片偏移、常用画幅和模型对应的 2K 参考尺寸。
- 图像模型：Banana 2、Banana Pro、GPT Image 2、Image 2.5 Flare、Image 2.5 Sunburst。
- 提示词模型：GPT-5.6 Sol，并支持外部提示词 Skill。
- 固定高度提示词编辑器，长中文按面板宽度显示折行，提交时不会加入显示层换行。
- 保存生成原图，不额外生成带尺寸尾缀的缩放副本。

## 快速安装

1. 安装并至少启动过一次 Cinema 4D。
2. 关闭 Cinema 4D。
3. 解压发布包，双击 `Install-ZhireAI.cmd`。
4. 启动 Cinema 4D，在布局中打开“挚热AI渲染器 V2”。
5. 进入设置，填写自己的挚热 API Key 并连接。

安装器会自动备份同名旧插件。它不会读取或复制旧版本的 API Key、参考图、历史记录和生成文件。

## API 与模型

项目保留挚热 API 的接口实现和模型映射，但不包含任何 API Key。

- API：`https://api.juaihub.cn`
- Banana 2：`gemini-3.1-flash-image-preview`
- Banana Pro：`gemini-3-pro-image-preview`
- GPT Image 2：`gpt-image-2`
- Image 2.5：`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`
- 提示词模型：`gpt-5.6-sol`

Banana 系列使用 Gemini 原生 `/v1beta/models/{model}:generateContent`；Image 2/2.5 使用兼容 Images API。

## 隐私与发布内容

本仓库不包含用户 API Key、用户设置、绝对文件路径、C4D 工程、参考素材、生成图片、历史列表、日志或调试备份。API Key 仅由使用者在本机插件设置中填写。

详细环境要求见 [RUNTIME_ENVIRONMENT.md](RUNTIME_ENVIRONMENT.md)，AI 智能体安装步骤见 [INSTALL_FOR_AI_AGENTS.md](INSTALL_FOR_AI_AGENTS.md)。

## 许可

使用前请阅读 [软件使用许可](zhireAI/LICENSE_zh-CN.txt)。Cinema 4D 为 Maxon 的授权软件，不包含在本项目中。
