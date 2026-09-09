# AI 智能体安装说明

目标：在 Windows 上把本发布包安装到用户现有 Cinema 4D 环境，不复制任何用户配置或素材。

## 自动执行顺序

1. 确认发布包已经解压，根目录存在 `release-info.json` 和 `zhireAI/lumastage_ai.pyp`。
2. 运行 `Verify-Package.ps1`，校验所有正式文件的 SHA256。
3. 确认 Cinema 4D 已关闭。不要替用户保存、覆盖或删除工程。
4. 运行 `Install-ZhireAI.ps1`。默认安装到最近使用的兼容 C4D 用户配置目录。
5. 需要同时安装 Codex 提示词 Skill 时，额外传入 `-InstallCodexSkill`。Skill 会放到用户的 Codex skills 目录，不会放进 C4D 插件目录。
6. 再次执行发布包校验，并报告实际安装目标和备份目录。

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-ZhireAI.ps1
```

同时安装 Codex Skill：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-ZhireAI.ps1 -InstallCodexSkill
```

安装器不得读取、导出或写入用户 API Key，不得复制用户参考图、历史记录、生成文件和 C4D 工程。首次启动后由用户在插件设置中填写自己的挚热 API Key。
