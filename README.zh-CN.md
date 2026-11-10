# Codex 本地 Projects 恢复

这个非官方工具用于在 **切换 ChatGPT 账号**或**改用 API 密钥登录**后，恢复 Codex 桌面版中消失的本地 Projects 入口和已有聊天的项目归属。它只处理本机的项目引用；不会移动课件、科研资料，也不会复制聊天正文。

## 适用范围

- macOS Codex 桌面版，Python 3.10 或更高版本，不需要第三方 Python 依赖。
- 普通账号流程在构建 **25241** 上实测；API 密钥流程在构建 **25894** 上实测。版本变化时会在写入前停止，必须重新核对应用的内部存储格式。
- 只读检查 `state_5.sqlite`，并读写项目配置文件 `~/.codex/.codex-global-state.json`。不读取登录凭据、API 密钥或聊天正文。不恢复云端 Projects、云端聊天及已丢失的项目文件。
- 备份、日志和恢复前完整配置放在 `~/.codex-project-recovery/`，不放在开源仓库中。请勿上传这个目录。详情见 [安全说明](SECURITY.md)。

## 切换 ChatGPT 账号

切换前运行：

```bash
python3 scripts/project_recovery.py inspect
python3 scripts/project_recovery.py backup
```

登录目标账号后运行：

```bash
python3 scripts/project_recovery.py plan
python3 scripts/project_recovery.py restore
```

从屏幕顶部菜单**完全退出**桌面应用，等待桌面出现本次带日期的完成说明，再重新打开。只关窗口不算退出；过早重新打开会让恢复程序继续等待。重新打开后运行：

```bash
python3 scripts/project_recovery.py status
python3 scripts/project_recovery.py verify
```

还要在应用内核对项目数量、不同项目中的聊天归属以及外接硬盘路径。程序显示“写入成功”不能代替这些检查。
账号分区、数据库映射和回退限制见 [普通账号技术说明](docs/CHATGPT_MODE.md)。

## 改用 API 密钥

先确认 `codex login status` 显示 API 密钥登录，并确认已有项目备份、当前项目列表为空。工具会自行检查登录方式，不会输出状态中的密钥片段。

```bash
python3 scripts/api_project_restore.py plan
python3 scripts/api_project_restore.py restore
```

同样要完全退出应用，等完成说明出现后再打开。然后检查：

```bash
python3 scripts/api_project_restore.py status
python3 scripts/api_project_restore.py backup
```

第二条命令用于在确认恢复成功后保存最新项目和聊天归属。API 流程在已核对的构建上会移除活动配置中的账号分区键；原分区完整保存在本次 `runs/.../before.json`。日后切回 ChatGPT 登录时，保留这个文件和历次快照，并重新核查普通账号流程。技术原理见 [API 模式说明](docs/API_MODE.md)。

## 已切换、没有新备份

不要用空列表覆盖旧备份。先用 `inspect` 检查旧账号分区，并检查 `~/.codex-project-recovery/backups/`。确定来源后可用 `backup --source-profile <分区标识>` 导出旧分区。若有多个不同来源，先对照项目名称和路径，不要随意选择。两种 `plan`、`restore` 命令都支持 `--snapshot /绝对路径/备份.json`。

## 注意

本工具使用桌面应用未公开的本地存储格式，只支持已审查的构建。项目入口恢复后，外置硬盘仍须连接才能打开资料。遇到冲突或版本变化时停止，不要修改脚本绕过检查。运行记录包含私人路径和聊天标识，切勿提交到 GitHub。

执行隔离测试：

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

本项目采用 MIT 许可证，与 OpenAI 官方无关。
