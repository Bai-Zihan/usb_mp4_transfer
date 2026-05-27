# U盘文件夹自动导入 App

这是一个用 Python + Tkinter 写的小工具：
- 自动检测U盘挂载目录
- 支持多个U盘同时扫描/导入（并行处理）
- 默认最多支持 35 个U盘并行导入
- 扫描U盘根目录下的所有文件夹
- 在目标目录下先创建和U盘同名的子文件夹
- 把U盘根目录下的所有文件夹导入到这个U盘同名子文件夹里
- 会自动跳过 `System Volume Information` 系统文件夹
- U盘根目录下的单个文件（如 `.exe`、`.dmg`、`.pdf`）不会导入
- 如果目标目录下已经有同名U盘子文件夹，里面同名的采集文件夹会被新版覆盖
- 界面会显示当前复制进度，导入完成后会弹窗提示
- 同一次运行中，U盘文件夹内容没有变化时会自动跳过，避免监听模式反复覆盖
- 导入结束后会弹出 `导入完成` 提示，并显示本次更新的文件夹数量

## 项目说明

本软件由用户 `ziyi020924-png` 开发。当前由我接收使用，并会根据实际使用场景、设备环境和导入流程需求持续迭代完善。

## 运行环境
- Python 3.9+
- 无需第三方依赖（使用 Python 标准库）

## 启动方法
在该目录执行：

```bash
python3 usb_mp4_transfer_app.py
```

## 使用步骤
1. 点击 `选择` 设置目标文件夹。
2. 设置扫描间隔（默认5秒）。
3. 点击 `开始监听`。
4. 插入U盘后，程序会在目标目录下创建一个和U盘名字相同的子文件夹。
5. 程序会把U盘根目录下的所有文件夹导入到这个U盘同名子文件夹里。
6. 如果目标位置已经有同名采集文件夹，程序会先复制到临时目录，复制成功后再覆盖旧文件夹。
7. 导入时界面会显示复制进度；有文件夹导入或覆盖完成后，程序会弹窗提示 `导入完成`。

你也可以点击 `立即扫描` 立刻执行一次。

## 注意事项
- macOS 下会扫描 `/Volumes` 中可见的外部卷。
- Windows 下会扫描可移动磁盘盘符。
- Ubuntu/Linux 下优先使用 `lsblk` 识别真正的可移动U盘挂载点；不兼容时回退扫描 `/media/$USER`、`/run/media/$USER`。
- 本程序不会删除U盘原文件夹。
- 覆盖目标目录中的旧采集文件夹时，会删除目标目录里对应位置的旧内容；请确认目标目录选择正确。
- 程序只导入U盘根目录下的文件夹，不导入U盘根目录下的单个文件，也不会导入 `System Volume Information`。

## Ubuntu 直接打开（双击启动）
本目录已包含脚本：
- `run_ubuntu.sh`：启动程序
- `install_ubuntu_desktop_icon.sh`：安装 Ubuntu 应用菜单图标
- `setup_ubuntu_env.sh`：新机器一键安装环境并创建启动图标

推荐一键安装（Ubuntu 新机器）：
`./setup_ubuntu_env.sh`

在 Ubuntu 上操作：
1. 安装依赖：
   `sudo apt update && sudo apt install -y python3 python3-tk`
2. 进入项目目录后执行：
   `./install_ubuntu_desktop_icon.sh`
3. 打开应用菜单，搜索 `U盘文件夹自动导入`，点击即可启动。

如果只想临时运行，不装图标，可直接执行：
`./run_ubuntu.sh`

## 可选：打包成双击可运行的 App（macOS）
如果你想把它变成 `.app`，可后续用 `pyinstaller` 打包。我可以继续帮你一键生成打包命令。

## Windows 打包成 EXE（可选）
当前目录没有包含 Windows 打包脚本。如需分发给 Windows 电脑，可后续补充 `pyinstaller` 打包脚本。

在 Windows 上操作：
1. 安装 Python 3.9+（安装时勾选 `Add python.exe to PATH`）。
2. 把整个 `U盘上传app` 文件夹拷到 Windows 电脑。
3. 使用 `pyinstaller` 打包主程序。
4. 打包完成后，程序通常在：
   `dist\usb_folder_import\usb_folder_import.exe`

说明：
- 建议在目标 Windows 电脑上打包并运行（Windows 版本/架构更兼容）。

## EXE 打不开时怎么排查
1. 先确认是用 Windows 电脑打包出来的 EXE（不要用 macOS 产物直接在 Windows 运行）。
2. 优先生成带控制台的调试版 EXE，查看控制台报错。
3. 若仍无明显信息，查看用户目录日志文件：`%USERPROFILE%\usb_folder_import_error.log`。
