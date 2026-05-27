import json
import os
import queue
import shutil
import string
import subprocess
import threading
import time
import traceback
import uuid
import tkinter as tk
from hashlib import sha256
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "U盘文件夹自动导入"
DEFAULT_SCAN_INTERVAL_SECONDS = 5
MAX_PARALLEL_UPLOADS = 35
EXCLUDED_TOP_LEVEL_FOLDER_NAMES = {"system volume information"}
HISTORY_FILENAME = ".usb_folder_import_history.json"


class UsbMp4TransferApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("900x680")

        self.destination_var = tk.StringVar(value=str(Path.home() / "Movies" / "U盘导入"))
        self.interval_var = tk.StringVar(value=str(DEFAULT_SCAN_INTERVAL_SECONDS))
        self.status_var = tk.StringVar(value="状态：未启动")

        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.completion_queue = queue.Queue()
        self.progress_queue = queue.Queue()
        self.status_queue = queue.Queue()
        self.device_status_queue = queue.Queue()
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="进度：未开始")
        self.device_rows = {}
        self.import_history = self._load_import_history()
        self.imported_this_run = set()
        self.imported_lock = threading.Lock()
        self.history_lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.scan_once_thread = None
        self.target_locks = {}
        self.target_locks_lock = threading.Lock()

        self._build_ui()
        self.root.after(150, self._flush_log_queue)

    def _build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=12, pady=10)

        tk.Label(top, text="目标文件夹:").grid(row=0, column=0, sticky="w")
        tk.Entry(top, textvariable=self.destination_var, width=58).grid(row=0, column=1, sticky="we", padx=8)

        dest_btns = tk.Frame(top)
        dest_btns.grid(row=0, column=2, sticky="e")
        tk.Button(dest_btns, text="选择", command=self._pick_destination).pack(side="left", padx=(0, 6))
        tk.Button(dest_btns, text="自动找网络盘", command=self._pick_network_destination).pack(side="left")

        tk.Label(top, text="扫描间隔(秒):").grid(row=1, column=0, sticky="w", pady=(10, 0))
        tk.Entry(top, textvariable=self.interval_var, width=10).grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))

        btns = tk.Frame(top)
        btns.grid(row=1, column=2, sticky="e", pady=(10, 0))
        tk.Button(btns, text="立即扫描", command=self.scan_once).pack(side="left", padx=(0, 6))
        tk.Button(btns, text="开始监听", command=self.start_monitoring).pack(side="left", padx=(0, 6))
        tk.Button(btns, text="停止监听", command=self.stop_monitoring).pack(side="left")

        top.columnconfigure(1, weight=1)

        status_frame = tk.Frame(self.root)
        status_frame.pack(fill="x", padx=12)
        tk.Label(status_frame, textvariable=self.status_var, fg="#0b5bcb").pack(anchor="w")
        tk.Label(status_frame, textvariable=self.progress_text_var, fg="#333333").pack(anchor="w", pady=(4, 0))
        ttk.Progressbar(status_frame, variable=self.progress_var, maximum=100).pack(fill="x", pady=(4, 0))

        device_frame = tk.LabelFrame(self.root, text="U盘状态")
        device_frame.pack(fill="x", padx=12, pady=(10, 0))

        columns = ("device", "status", "progress")
        self.device_tree = ttk.Treeview(device_frame, columns=columns, show="headings", height=7)
        self.device_tree.heading("device", text="U盘")
        self.device_tree.heading("status", text="状态")
        self.device_tree.heading("progress", text="进度")
        self.device_tree.column("device", width=260, anchor="w")
        self.device_tree.column("status", width=250, anchor="w")
        self.device_tree.column("progress", width=120, anchor="center")
        self.device_tree.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=8)

        device_scroll = tk.Scrollbar(device_frame, command=self.device_tree.yview)
        device_scroll.pack(side="right", fill="y", padx=(0, 8), pady=8)
        self.device_tree.config(yscrollcommand=device_scroll.set)

        log_frame = tk.Frame(self.root)
        log_frame.pack(fill="both", expand=True, padx=12, pady=10)

        self.log_text = tk.Text(log_frame, wrap="word", height=22)
        self.log_text.pack(side="left", fill="both", expand=True)

        scroll = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scroll.set)
        self.log_text.insert("end", "欢迎使用，设置目标文件夹后点击【开始监听】。\n")
        self.log_text.config(state="disabled")

    def _pick_destination(self):
        initial_dir = self._first_existing_network_mount() or str(Path.home())
        selected = filedialog.askdirectory(title="选择目标文件夹", initialdir=initial_dir)
        if selected:
            self.destination_var.set(selected)

    def _pick_network_destination(self):
        network_mounts = self._network_mount_candidates()
        if not network_mounts:
            messagebox.showinfo(
                APP_TITLE,
                "没有找到已打开的网络盘。\n\n"
                "请先打开 Ubuntu 的【文件】，在左边点击 CIFS on 192...，"
                "看到里面的文件后，再回到这里点【自动找网络盘】。",
            )
            return

        if len(network_mounts) == 1:
            self.destination_var.set(str(network_mounts[0]))
            self._append_log(f"已选择网络盘: {network_mounts[0]}")
            return

        picker = tk.Toplevel(self.root)
        picker.title("选择网络盘")
        picker.geometry("680x280")
        picker.transient(self.root)
        picker.grab_set()

        tk.Label(picker, text="找到多个网络盘，请选择一个目标文件夹:").pack(anchor="w", padx=12, pady=(12, 6))

        listbox = tk.Listbox(picker, height=8)
        listbox.pack(fill="both", expand=True, padx=12)
        for mount in network_mounts:
            listbox.insert("end", str(mount))
        listbox.selection_set(0)

        def choose_selected():
            selection = listbox.curselection()
            if not selection:
                return
            selected = network_mounts[selection[0]]
            self.destination_var.set(str(selected))
            self._append_log(f"已选择网络盘: {selected}")
            picker.destroy()

        button_row = tk.Frame(picker)
        button_row.pack(fill="x", padx=12, pady=12)
        tk.Button(button_row, text="使用这个", command=choose_selected).pack(side="right")
        tk.Button(button_row, text="取消", command=picker.destroy).pack(side="right", padx=(0, 8))
        listbox.bind("<Double-Button-1>", lambda _event: choose_selected())

    def _first_existing_network_mount(self):
        mounts = self._network_mount_candidates()
        return str(mounts[0]) if mounts else None

    @staticmethod
    def _network_mount_candidates():
        candidates = []
        seen = set()

        uid = os.getuid() if hasattr(os, "getuid") else None
        bases = []
        if uid is not None:
            bases.append(Path("/run/user") / str(uid) / "gvfs")
        bases.extend([Path("/media"), Path("/mnt"), Path("/nfs")])

        for base in bases:
            if not base.exists():
                continue
            try:
                children = list(base.iterdir())
            except OSError:
                continue

            for item in children:
                if not item.is_dir():
                    continue
                name = item.name.lower()
                looks_network = (
                    "smb" in name
                    or "cifs" in name
                    or "share" in name
                    or "server=" in name
                    or base.name in {"mnt", "nfs"}
                )
                if not looks_network:
                    continue
                resolved = str(item)
                if resolved not in seen:
                    candidates.append(item)
                    seen.add(resolved)

        return candidates

    def _append_log(self, message: str):
        now = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{now}] {message}\n")

    def _flush_log_queue(self):
        flushed = False
        self.log_text.config(state="normal")
        lines_flushed = 0
        while lines_flushed < 120:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line)
            flushed = True
            lines_flushed += 1

        if flushed:
            self.log_text.see("end")
        self.log_text.config(state="disabled")
        self._flush_status_queue()
        self._flush_progress_queue()
        self._flush_device_status_queue()
        self._flush_completion_queue()
        self.root.after(150, self._flush_log_queue)

    def _flush_status_queue(self):
        latest_status = None
        while True:
            try:
                latest_status = self.status_queue.get_nowait()
            except queue.Empty:
                break

        if latest_status is not None:
            self.status_var.set(latest_status)

    def _flush_progress_queue(self):
        latest_progress = None
        while True:
            try:
                latest_progress = self.progress_queue.get_nowait()
            except queue.Empty:
                break

        if latest_progress is not None:
            percent, text = latest_progress
            self.progress_var.set(percent)
            self.progress_text_var.set(text)

    def _flush_device_status_queue(self):
        while True:
            try:
                update = self.device_status_queue.get_nowait()
            except queue.Empty:
                break

            action = update.get("action")
            if action == "clear":
                for row_id in self.device_tree.get_children():
                    self.device_tree.delete(row_id)
                self.device_rows.clear()
                continue

            device_id = update["device_id"]
            values = (
                update.get("device", device_id),
                update.get("status", ""),
                update.get("progress", ""),
            )
            row_id = self.device_rows.get(device_id)
            if row_id and self.device_tree.exists(row_id):
                self.device_tree.item(row_id, values=values)
            else:
                self.device_rows[device_id] = self.device_tree.insert("", "end", values=values)

    def _flush_completion_queue(self):
        latest_count = None
        while True:
            try:
                latest_count = self.completion_queue.get_nowait()
            except queue.Empty:
                break

        if latest_count is None:
            return

        self.status_var.set(f"状态：导入完成，更新 {latest_count} 个文件夹")
        self.progress_var.set(100)
        self.progress_text_var.set("进度：导入完成")
        messagebox.showinfo(APP_TITLE, f"导入完成！\n\n本次更新 {latest_count} 个文件夹。")

    def _notify_transfer_complete(self, imported_count: int):
        if imported_count > 0:
            self.completion_queue.put(imported_count)

    def _set_status_from_worker(self, status: str):
        self.status_queue.put(status)

    def _set_progress_from_worker(self, percent: float, text: str):
        self.progress_queue.put((max(0, min(100, percent)), text))

    def _clear_device_statuses_from_worker(self):
        self.device_status_queue.put({"action": "clear"})

    def _set_device_status_from_worker(self, device_id: str, device: str, status: str, progress: str = ""):
        self.device_status_queue.put(
            {
                "device_id": device_id,
                "device": device,
                "status": status,
                "progress": progress,
            }
        )

    def _load_import_history(self):
        path = self._history_path()
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            self._append_log(f"读取导入历史失败，将重新记录: {exc}")
            return {}

        if isinstance(data, dict):
            return data
        return {}

    def _save_import_history(self):
        path = self._history_path()
        temp_path = path.with_name(f"{path.name}.tmp")
        with self.history_lock:
            try:
                with temp_path.open("w", encoding="utf-8") as f:
                    json.dump(self.import_history, f, ensure_ascii=False, indent=2, sort_keys=True)
                temp_path.replace(path)
            except Exception as exc:
                self._append_log(f"保存导入历史失败: {exc}")
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _history_path() -> Path:
        return Path.home() / HISTORY_FILENAME

    def _parse_interval(self) -> int:
        try:
            value = int(self.interval_var.get().strip())
            if value < 1:
                raise ValueError
            return value
        except ValueError:
            raise ValueError("扫描间隔必须是正整数")

    def _ensure_destination(self, destination_text=None) -> Path:
        if destination_text is None:
            destination_text = self.destination_var.get()
        destination_text = destination_text.strip()
        if not destination_text:
            raise ValueError("请先设置目标文件夹")
        dest = Path(destination_text).expanduser()
        if ";" in str(dest):
            raise ValueError("目标文件夹路径不正确。请点击【选择】按钮选择网络盘里的文件夹，不要手动输入带分号的路径。")
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise ValueError(
                f"没有权限访问目标文件夹：\n{dest}\n\n"
                "请先在文件管理器里打开这个网络盘，确认可以新建文件夹；"
                "然后回到程序点击【选择】，选择网络盘里面你有权限的文件夹。"
            )

        probe = dest / ".usb_folder_import_write_test"
        try:
            with probe.open("w", encoding="utf-8") as f:
                f.write("ok")
            probe.unlink()
        except PermissionError:
            raise ValueError(
                f"目标文件夹不能写入：\n{dest}\n\n"
                "请检查这个网络盘是否有上传/写入权限，或在网络盘里新建一个可写文件夹后重新选择。"
            )
        except OSError as exc:
            raise ValueError(f"目标文件夹不可用：\n{dest}\n\n原因：{exc}")
        return dest

    def start_monitoring(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            messagebox.showinfo(APP_TITLE, "监听已在运行")
            return

        try:
            interval = self._parse_interval()
            destination = self._ensure_destination()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, args=(interval, destination), daemon=True)
        self.monitor_thread.start()
        self.status_var.set("状态：监听中")
        self._append_log(f"开始监听，每 {interval} 秒扫描一次")

    def stop_monitoring(self):
        self.stop_event.set()
        self.status_var.set("状态：已停止")
        self._append_log("监听已停止")

    def scan_once(self):
        try:
            destination = self._ensure_destination()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        if self.scan_once_thread and self.scan_once_thread.is_alive():
            messagebox.showinfo(APP_TITLE, "扫描正在进行，请稍等")
            return

        self.status_var.set("状态：正在扫描/导入")
        self.progress_var.set(0)
        self.progress_text_var.set("进度：正在扫描U盘")
        self.scan_once_thread = threading.Thread(
            target=self._run_single_scan,
            args=(destination, "本次扫描完成"),
            daemon=True,
        )
        self.scan_once_thread.start()

    def _run_single_scan(self, destination: Path, finished_message: str) -> int:
        if not self.scan_lock.acquire(blocking=False):
            self._append_log("已有扫描/导入任务正在进行，跳过本次扫描")
            return 0

        try:
            self._set_progress_from_worker(0, "进度：正在扫描U盘")
            imported_count = self._import_folders_from_removable_drives(destination)
            self._append_log(f"{finished_message}，更新 {imported_count} 个文件夹")
            self._set_status_from_worker(f"状态：{finished_message}，更新 {imported_count} 个文件夹")
            if imported_count == 0:
                self._set_progress_from_worker(100, "进度：扫描完成，没有需要更新的文件夹")
            self._notify_transfer_complete(imported_count)
            return imported_count
        finally:
            self.scan_lock.release()

    def _monitor_loop(self, interval: int, destination: Path):
        while not self.stop_event.is_set():
            try:
                imported_count = self._run_single_scan(destination, "自动扫描完成")
                if imported_count > 0:
                    self._append_log(f"自动扫描发现文件夹变化，已更新 {imported_count} 个文件夹")
            except Exception as exc:
                self._append_log(f"监听异常：{exc}")

            self.stop_event.wait(interval)

    def _list_removable_mount_points(self):
        points, _unavailable = self._removable_drive_report()
        return points

    def _removable_drive_report(self):
        points = []
        unavailable = []

        if os.name == "nt":
            import ctypes

            DRIVE_REMOVABLE = 2
            kernel32 = ctypes.windll.kernel32
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if not os.path.exists(drive):
                    continue
                drive_type = kernel32.GetDriveTypeW(drive)
                if drive_type == DRIVE_REMOVABLE:
                    points.append(Path(drive))
            return points, unavailable

        if sys_platform() == "darwin":
            volumes = Path("/Volumes")
            if volumes.exists():
                for item in volumes.iterdir():
                    if item.is_dir() and not item.name.startswith(".") and item.name != "Macintosh HD":
                        points.append(item)
            return points, unavailable

        points, unavailable = self._linux_lsblk_removable_report()
        if points:
            return points, unavailable

        user = os.environ.get("USER", "")
        candidates = [
            Path("/media") / user,
            Path("/run/media") / user,
        ]
        return self._existing_child_directories(candidates), unavailable

    @staticmethod
    def _linux_lsblk_removable_mount_points():
        points, _unavailable = UsbMp4TransferApp._linux_lsblk_removable_report()
        return points

    @staticmethod
    def _linux_lsblk_removable_report():
        for mount_column in ("MOUNTPOINTS", "MOUNTPOINT"):
            try:
                result = subprocess.run(
                    ["lsblk", "-J", "-o", f"NAME,LABEL,MODEL,SIZE,RM,TYPE,{mount_column}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                data = json.loads(result.stdout)
            except Exception:
                continue

            points = []
            seen = set()
            unavailable = []

            def visit(device, removable_parent=False):
                removable = removable_parent or str(device.get("rm", "")).lower() in {"1", "true"}
                for child in device.get("children") or []:
                    visit(child, removable)

                children = device.get("children") or []
                device_type = device.get("type")
                if not removable or device_type not in {"disk", "part"} or children:
                    return

                mount_values = device.get(mount_column.lower())
                if isinstance(mount_values, str):
                    mount_values = [mount_values]

                valid_mounts = []
                for mount_value in mount_values or []:
                    if not mount_value:
                        continue
                    mount_path = Path(mount_value)
                    mount_key = str(mount_path)
                    if mount_path.is_dir() and mount_key not in seen:
                        points.append(mount_path)
                        seen.add(mount_key)
                        valid_mounts.append(mount_key)

                if valid_mounts:
                    return

                label = device.get("label") or device.get("model") or device.get("name") or "未知设备"
                size = device.get("size") or ""
                name = device.get("name") or ""
                unavailable.append(f"{label} {size} ({name})".strip())

            for block_device in data.get("blockdevices") or []:
                visit(block_device)

            if points or unavailable:
                return points, unavailable

        return [], []

    @staticmethod
    def _existing_child_directories(bases):
        points = []
        seen = set()
        for base in bases:
            if not base.exists():
                continue
            try:
                children = list(base.iterdir())
            except OSError:
                continue
            for item in children:
                item_key = str(item)
                if item.is_dir() and item_key not in seen:
                    points.append(item)
                    seen.add(item_key)
        return points

    def _import_folders_from_removable_drives(self, destination: Path) -> int:
        self._clear_device_statuses_from_worker()
        mount_points, unavailable_devices = self._removable_drive_report()
        if not mount_points:
            if unavailable_devices:
                self._append_log(
                    f"系统识别到 {len(unavailable_devices)} 个可移动设备，但都没有可导入的挂载点"
                )
                for device in unavailable_devices[:12]:
                    self._append_log(f"未挂载或不可访问: {device}")
            return 0

        parallel_limit = min(MAX_PARALLEL_UPLOADS, len(mount_points))
        total_seen = len(mount_points) + len(unavailable_devices)
        self._append_log(
            f"系统识别可移动设备 {total_seen} 个，可导入 {len(mount_points)} 个，"
            f"未挂载/不可访问 {len(unavailable_devices)} 个，同时导入上限 {MAX_PARALLEL_UPLOADS}，本轮并发 {parallel_limit}"
        )
        for device in unavailable_devices[:12]:
            self._append_log(f"未挂载或不可访问: {device}")

        semaphore = threading.Semaphore(parallel_limit)
        result_queue = queue.Queue()
        threads = []
        for mount in mount_points:
            self._set_device_status_from_worker(str(mount), str(mount), "等待导入", "0%")
            worker = threading.Thread(
                target=self._import_mount_worker,
                args=(mount, destination, semaphore, result_queue),
                daemon=True,
            )
            worker.start()
            threads.append(worker)

        for worker in threads:
            worker.join()

        imported_count = 0
        skipped_count = 0
        failed_count = 0
        total_files = 0
        total_size = 0
        while not result_queue.empty():
            result = result_queue.get()
            imported_count += result.get("imported", 0)
            skipped_count += result.get("skipped", 0)
            failed_count += result.get("failed", 0)
            total_files += result.get("files", 0)
            total_size += result.get("size", 0)

        if imported_count > 0 or failed_count > 0 or unavailable_devices:
            report_path = self._write_scan_report(
                destination,
                total_seen=total_seen,
                importable_count=len(mount_points),
                unavailable_devices=unavailable_devices,
                imported_count=imported_count,
                skipped_count=skipped_count,
                failed_count=failed_count,
                total_files=total_files,
                total_size=total_size,
            )
            if report_path:
                self._append_log(f"本轮报告已保存: {report_path}")
        return imported_count

    def _import_mount_worker(
        self, mount: Path, destination: Path, semaphore: threading.Semaphore, result_queue: queue.Queue
    ):
        with semaphore:
            device_id = str(mount)
            try:
                self._set_device_status_from_worker(device_id, device_id, "正在扫描", "0%")
                result = self._import_folders_from_single_mount(mount, destination)
                self._set_device_status_from_worker(
                    device_id,
                    device_id,
                    f"复制完成，请弹出后拔出，更新 {result['imported']} 个，跳过 {result['skipped']} 个",
                    "100%",
                )
                result_queue.put(result)
            except Exception as exc:
                reason = self._friendly_error_message(exc)
                self._set_device_status_from_worker(device_id, device_id, f"处理异常: {reason}", "失败")
                self._append_log(f"设备处理异常：{mount}，原因：{reason}（{exc}）")
                result_queue.put(self._empty_import_result(failed=1))

    def _import_folders_from_single_mount(self, mount: Path, destination: Path) -> dict:
        device_name = self._device_folder_name(mount)
        device_destination = destination / device_name
        device_destination.mkdir(parents=True, exist_ok=True)
        self._append_log(f"扫描设备: {mount} -> 目标子文件夹: {device_name}")

        source_folders = list(self._iter_top_level_folders(mount, destination))
        if not source_folders:
            self._set_device_status_from_worker(str(mount), str(mount), "没有可导入的文件夹", "100%")
            self._append_log(f"设备没有可导入的文件夹: {mount}")
            return self._empty_import_result()

        folder_plans = []
        total_files = 0
        total_size = 0
        for source_folder in source_folders:
            final_name = self._sanitize_folder_name(source_folder.name)
            signature = self._folder_signature(source_folder)
            folder_plans.append((source_folder, final_name, signature))
            total_files += signature["file_count"]
            total_size += signature["total_size"]

        self._set_device_status_from_worker(
            str(mount),
            str(mount),
            f"发现 {len(folder_plans)} 个文件夹，{total_files} 个文件，{self._format_bytes(total_size)}",
            "0%",
        )

        result = self._empty_import_result(files=total_files, size=total_size)

        for index, (source_folder, final_name, signature) in enumerate(folder_plans, start=1):
            target_folder = device_destination / final_name
            match_key = f"{device_name}/{final_name}".casefold()
            history_key = self._history_key(device_name, final_name)
            history_record = self._history_record(signature, target_folder)

            with self.imported_lock:
                if self._signature_key(signature) in self.imported_this_run and target_folder.exists():
                    result["skipped"] += 1
                    self._set_device_status_from_worker(str(mount), str(mount), f"跳过本轮已导入: {final_name}", "")
                    self._append_log(f"本轮已导入过，跳过: {source_folder}")
                    continue

            with self.history_lock:
                already_imported = self.import_history.get(history_key) == history_record and target_folder.exists()

            if already_imported:
                result["skipped"] += 1
                self._set_device_status_from_worker(str(mount), str(mount), f"历史已导入，跳过: {final_name}", "100%")
                self._append_log(f"历史记录一致，跳过: {source_folder} -> {target_folder}")
                continue

            lock = self._target_lock_for(match_key)
            with lock:
                try:
                    self._set_device_status_from_worker(
                        str(mount),
                        str(mount),
                        f"正在复制 {index}/{len(folder_plans)}: {final_name}",
                        "0%",
                    )
                    replaced_name = target_folder.name if target_folder.exists() else None
                    did_replace = self._install_folder(
                        source_folder,
                        target_folder,
                        progress_name=f"{final_name} ({index}/{len(folder_plans)})",
                        device_id=str(mount),
                        device_name=str(mount),
                        total_files=signature["file_count"],
                    )
                    if not did_replace:
                        continue

                    with self.imported_lock:
                        self.imported_this_run.add(self._signature_key(signature))
                    with self.history_lock:
                        self.import_history[history_key] = history_record
                    self._save_import_history()

                    result["imported"] += 1
                    if replaced_name:
                        self._append_log(f"已覆盖: {source_folder} -> {target_folder}（原目标: {replaced_name}）")
                    else:
                        self._append_log(f"已新建导入: {source_folder} -> {target_folder}")
                except Exception as exc:
                    result["failed"] += 1
                    reason = self._friendly_error_message(exc)
                    self._set_device_status_from_worker(str(mount), str(mount), f"导入失败: {reason}", "失败")
                    self._append_log(f"导入失败: {source_folder}，原因: {reason}（{exc}）")

        return result

    @staticmethod
    def _empty_import_result(imported=0, skipped=0, failed=0, files=0, size=0):
        return {
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "files": files,
            "size": size,
        }

    def _history_key(self, device_name: str, folder_name: str) -> str:
        return f"{device_name}/{folder_name}".casefold()

    @staticmethod
    def _signature_key(signature: dict) -> tuple:
        return (
            signature["file_count"],
            signature["dir_count"],
            signature["total_size"],
            signature["max_mtime_ns"],
            signature["digest"],
        )

    @staticmethod
    def _history_record(signature: dict, target_folder: Path) -> dict:
        return {
            "file_count": signature["file_count"],
            "dir_count": signature["dir_count"],
            "total_size": signature["total_size"],
            "max_mtime_ns": signature["max_mtime_ns"],
            "digest": signature["digest"],
            "target": str(target_folder),
        }

    @staticmethod
    def _format_bytes(size: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)}{unit}"
                return f"{value:.1f}{unit}"
            value /= 1024
        return f"{size}B"

    @staticmethod
    def _friendly_error_message(exc: Exception) -> str:
        text = str(exc).lower()
        if isinstance(exc, PermissionError) or "permission denied" in text:
            return "没有权限读取U盘或写入目标盘"
        if isinstance(exc, FileNotFoundError) or "no such file" in text:
            return "U盘或目标文件中途断开"
        if "no space left" in text or "disk full" in text:
            return "目标盘空间不足"
        if "read-only file system" in text:
            return "目标盘是只读状态"
        if "input/output error" in text or "i/o error" in text:
            return "U盘或目标盘读写异常"
        if "network is unreachable" in text or "connection timed out" in text or "stale file handle" in text:
            return "网络盘连接异常"
        if "file name too long" in text:
            return "文件名过长"
        return "未知错误"

    def _write_scan_report(
        self,
        destination: Path,
        total_seen: int,
        importable_count: int,
        unavailable_devices,
        imported_count: int,
        skipped_count: int,
        failed_count: int,
        total_files: int,
        total_size: int,
    ):
        report_dir = destination / "_usb_import_reports"
        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            report_path = report_dir / f"usb_import_report_{timestamp}.txt"
            lines = [
                f"扫描时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"目标目录: {destination}",
                f"系统识别可移动设备: {total_seen}",
                f"可导入设备: {importable_count}",
                f"未挂载/不可访问: {len(unavailable_devices)}",
                f"成功导入文件夹: {imported_count}",
                f"跳过文件夹: {skipped_count}",
                f"失败文件夹: {failed_count}",
                f"本轮扫描文件数: {total_files}",
                f"本轮扫描容量: {self._format_bytes(total_size)}",
            ]
            if unavailable_devices:
                lines.append("")
                lines.append("未挂载或不可访问设备:")
                lines.extend(f"- {device}" for device in unavailable_devices)
            with report_path.open("w", encoding="utf-8") as f:
                f.write("\n".join(lines))
                f.write("\n")
            return report_path
        except Exception as exc:
            self._append_log(f"保存本轮报告失败: {exc}")
            return None

    @staticmethod
    def _sanitize_folder_name(name: str) -> str:
        invalid = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in invalid else ch for ch in name).strip().rstrip(".")
        return cleaned or "USB"

    def _device_folder_name(self, mount: Path) -> str:
        name = ""
        if os.name == "nt":
            name = self._windows_volume_label(mount)
            drive_letter = str(mount).rstrip("\\/")
            if not name:
                name = f"USB_{drive_letter.replace(':', '')}"
            else:
                name = f"{name}_{drive_letter.replace(':', '')}"
        else:
            name = mount.name.strip() or "USB"

        return self._sanitize_folder_name(name)

    @staticmethod
    def _windows_volume_label(mount: Path) -> str:
        try:
            import ctypes

            volume_name_buffer = ctypes.create_unicode_buffer(261)
            fs_name_buffer = ctypes.create_unicode_buffer(261)
            serial_number = ctypes.c_uint(0)
            max_component_len = ctypes.c_uint(0)
            file_system_flags = ctypes.c_uint(0)

            result = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(str(mount)),
                volume_name_buffer,
                ctypes.sizeof(volume_name_buffer),
                ctypes.byref(serial_number),
                ctypes.byref(max_component_len),
                ctypes.byref(file_system_flags),
                fs_name_buffer,
                ctypes.sizeof(fs_name_buffer),
            )
            if result:
                return volume_name_buffer.value.strip()
        except Exception:
            return ""
        return ""

    def _iter_top_level_folders(self, mount: Path, destination: Path):
        try:
            mount_resolved = mount.resolve()
            destination_resolved = destination.resolve()
            children = list(mount.iterdir())
        except OSError as exc:
            self._append_log(f"读取U盘目录失败: {mount}，原因: {exc}")
            return

        for item in children:
            try:
                if not item.is_dir():
                    continue
                if self._is_excluded_top_level_folder(item.name):
                    self._append_log(f"跳过系统文件夹: {item}")
                    continue
                item_resolved = item.resolve()
                if self._is_relative_to(destination_resolved, item_resolved):
                    self._append_log(f"跳过目标磁盘所在目录，避免循环复制: {item}")
                    continue
                if item_resolved == mount_resolved:
                    continue
                yield item
            except OSError as exc:
                self._append_log(f"跳过无法访问的文件夹: {item}，原因: {exc}")

    @staticmethod
    def _is_excluded_top_level_folder(name: str) -> bool:
        normalized = " ".join(name.strip().casefold().split())
        return normalized in EXCLUDED_TOP_LEVEL_FOLDER_NAMES

    def _install_folder(
        self,
        source_folder: Path,
        target_folder: Path,
        progress_name="",
        device_id="",
        device_name="",
        total_files=None,
    ):
        source_resolved = source_folder.resolve()
        target_resolved = target_folder.resolve() if target_folder.exists() else target_folder.parent.resolve() / target_folder.name
        if source_resolved == target_resolved:
            self._append_log(f"源文件夹和目标文件夹相同，跳过: {source_folder}")
            return False
        if self._is_relative_to(target_resolved, source_resolved):
            raise ValueError("目标文件夹不能在源文件夹内部")

        temp_target = target_folder.parent / f".usb_folder_import_tmp_{uuid.uuid4().hex}"
        try:
            self._copy_folder_with_progress(
                source_folder,
                temp_target,
                progress_name or source_folder.name,
                device_id=device_id,
                device_name=device_name,
                total_files=total_files,
            )

            if target_folder.exists():
                if target_folder.is_dir():
                    shutil.rmtree(target_folder)
                else:
                    target_folder.unlink()

            temp_target.rename(target_folder)
            return True
        except Exception:
            if temp_target.exists():
                shutil.rmtree(temp_target, ignore_errors=True)
            raise

    def _copy_folder_with_progress(
        self,
        source_folder: Path,
        temp_target: Path,
        progress_name: str,
        device_id="",
        device_name="",
        total_files=None,
    ):
        if total_files is None:
            total_files = self._count_files(source_folder)
        copied_files = 0
        last_reported_percent = -1
        temp_target.mkdir(parents=True, exist_ok=True)

        def handle_walk_error(err):
            self._append_log(f"复制时跳过无权限目录: {err}")

        self._set_progress_from_worker(0, f"进度：正在复制 {progress_name} (0/{total_files})")
        if device_id:
            self._set_device_status_from_worker(device_id, device_name or device_id, f"正在复制: {progress_name}", "0%")
        for root, dirs, files in os.walk(source_folder, onerror=handle_walk_error):
            dirs.sort()
            files.sort()
            root_path = Path(root)
            relative_root = root_path.relative_to(source_folder)
            target_root = temp_target / relative_root
            target_root.mkdir(parents=True, exist_ok=True)

            for dirname in dirs:
                (target_root / dirname).mkdir(exist_ok=True)

            for filename in files:
                source_file = root_path / filename
                target_file = target_root / filename
                shutil.copy2(source_file, target_file)
                copied_files += 1
                percent = 100 if total_files == 0 else int(copied_files * 100 / total_files)
                if percent != last_reported_percent:
                    last_reported_percent = percent
                    self._set_progress_from_worker(
                        percent,
                        f"进度：正在复制 {progress_name} ({copied_files}/{total_files})",
                    )
                    if device_id:
                        self._set_device_status_from_worker(
                            device_id,
                            device_name or device_id,
                            f"正在复制: {progress_name}",
                            f"{percent}%",
                        )

            try:
                shutil.copystat(root_path, target_root)
            except OSError:
                pass

        if total_files == 0:
            self._set_progress_from_worker(100, f"进度：正在复制 {progress_name} (0/0)")
            if device_id:
                self._set_device_status_from_worker(
                    device_id,
                    device_name or device_id,
                    f"正在复制: {progress_name}",
                    "100%",
                )

    @staticmethod
    def _count_files(folder: Path) -> int:
        total = 0
        for _, _, files in os.walk(folder):
            total += len(files)
        return total

    def _target_lock_for(self, match_key: str):
        with self.target_locks_lock:
            lock = self.target_locks.get(match_key)
            if lock is None:
                lock = threading.Lock()
                self.target_locks[match_key] = lock
            return lock

    def _folder_signature(self, folder: Path) -> dict:
        total_size = 0
        file_count = 0
        dir_count = 0
        max_mtime_ns = 0
        digest = sha256()

        def handle_walk_error(err):
            self._append_log(f"跳过无权限目录: {err}")

        for root, dirs, files in os.walk(folder, onerror=handle_walk_error):
            dirs.sort()
            files.sort()
            root_path = Path(root)
            dir_count += len(dirs)
            for name in dirs:
                relative_name = str((root_path / name).relative_to(folder))
                digest.update(f"D\t{relative_name}\n".encode("utf-8", errors="surrogateescape"))
            for name in files:
                path = root_path / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                file_count += 1
                total_size += stat.st_size
                max_mtime_ns = max(max_mtime_ns, stat.st_mtime_ns)
                relative_name = str(path.relative_to(folder))
                digest.update(
                    f"F\t{relative_name}\t{stat.st_size}\t{stat.st_mtime_ns}\n".encode(
                        "utf-8",
                        errors="surrogateescape",
                    )
                )

        return {
            "file_count": file_count,
            "dir_count": dir_count,
            "total_size": total_size,
            "max_mtime_ns": max_mtime_ns,
            "digest": digest.hexdigest(),
        }

    @classmethod
    def _folder_match_key(cls, name: str) -> str:
        normalized = name.strip()
        changed = True
        while changed:
            changed = False
            for prefix in LEGACY_FOLDER_PREFIXES:
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix) :].strip()
                    changed = True
        return normalized.casefold()

    @staticmethod
    def _is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False


def sys_platform() -> str:
    import platform

    return platform.system().lower()


def main():
    try:
        root = tk.Tk()
        app = UsbMp4TransferApp(root)

        def on_close():
            app.stop_monitoring()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()
    except Exception:
        log_path = Path.home() / "usb_folder_import_error.log"
        error_text = traceback.format_exc()
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Application startup error\n")
            f.write(error_text)
            f.write("\n")

        try:
            err_root = tk.Tk()
            err_root.withdraw()
            messagebox.showerror(
                APP_TITLE,
                f"程序启动失败，请查看日志：\n{log_path}\n\n{error_text.splitlines()[-1]}",
            )
            err_root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
