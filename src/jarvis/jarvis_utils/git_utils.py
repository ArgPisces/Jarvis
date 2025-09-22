# -*- coding: utf-8 -*-
"""
Git工具模块
该模块提供了与Git仓库交互的工具。
包含以下功能：
- 查找Git仓库的根目录
- 检查是否有未提交的更改
- 获取两个哈希值之间的提交历史
- 获取最新提交的哈希值
- 从Git差异中提取修改的行范围
"""
import datetime
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Set, Tuple

from jarvis.jarvis_utils.config import get_data_dir, is_confirm_before_apply_patch
from jarvis.jarvis_utils.output import OutputType, PrettyOutput
from jarvis.jarvis_utils.input import user_confirm
from jarvis.jarvis_utils.utils import is_rag_installed


def find_git_root_and_cd(start_dir: str = ".") -> str:
    """
    切换到给定路径的Git根目录，如果不是Git仓库则初始化。

    参数:
        start_dir (str): 起始查找目录，默认为当前目录。

    返回:
        str: Git仓库根目录路径。如果目录不是Git仓库，则会初始化一个新的Git仓库。
    """
    from jarvis import jarvis_native as _jarvis_native  # type: ignore
    # 保持纯函数的命令在原生侧运行（不改变当前进程cwd）
    root = _jarvis_native.git_find_root(start_dir, True)
    root = str(root or "").strip()
    if not root:
        # 兜底：若原生实现失败，保持旧逻辑
        os.chdir(start_dir)
        try:
            git_root = os.popen("git rev-parse --show-toplevel").read().strip()
            if not git_root:
                subprocess.run(["git", "init"], check=True)
                git_root = os.path.abspath(".")
        except subprocess.CalledProcessError:
            subprocess.run(["git", "init"], check=True)
            git_root = os.path.abspath(".")
        os.chdir(git_root)
        return git_root

    # 切换到根目录并返回
    os.chdir(root)
    return root


def has_uncommitted_changes() -> bool:
    """检查Git仓库中是否有未提交的更改（Rust原生实现）"""
    try:
        from jarvis import jarvis_native as _jarvis_native  # type: ignore
        return bool(_jarvis_native.git_has_uncommitted_changes())
    except Exception:
        return False


def get_commits_between(start_hash: str, end_hash: str) -> List[Tuple[str, str]]:
    """获取两个提交哈希值之间的提交列表（Rust原生实现）"""
    try:
        from jarvis import jarvis_native as _jarvis_native  # type: ignore
        commits = _jarvis_native.git_get_commits_between(start_hash, end_hash)
        # commits 是 (hash, message) 的元组列表
        return [(str(h), str(m)) for (h, m) in commits]
    except Exception as e:
        PrettyOutput.print(f"获取commit历史异常: {str(e)}", OutputType.ERROR)
        return []


# 修改后的获取差异函数


def get_diff() -> str:
    """使用git获取工作区差异，包括修改和新增的文件内容

    返回:
        str: 差异内容或错误信息
    """
    try:
        # 检查是否为空仓库
        head_check = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        confirm_add_new_files()
        if head_check.returncode != 0:
            # 空仓库情况，直接获取工作区差异
            result = subprocess.run(
                ["git", "diff"], capture_output=True, text=False, check=True
            )
        else:
            # 暂存新增文件
            subprocess.run(["git", "add", "-N", "."], check=True)

            # 获取所有差异（包括新增文件）
            result = subprocess.run(
                ["git", "diff", "HEAD"], capture_output=True, text=False, check=True
            )

            # 重置暂存区
            subprocess.run(["git", "reset"], check=True)

        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return result.stdout.decode("utf-8", errors="replace")

    except subprocess.CalledProcessError as e:
        return f"获取差异失败: {str(e)}"
    except Exception as e:
        return f"发生意外错误: {str(e)}"


def revert_file(filepath: str) -> None:
    """增强版git恢复，处理新文件"""
    import subprocess

    try:
        # 检查文件是否在版本控制中
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", filepath],
            stderr=subprocess.PIPE,
            text=False,  # 禁用自动文本解码
        )
        if result.returncode == 0:
            subprocess.run(["git", "checkout", "HEAD", "--", filepath], check=True)
        else:
            if os.path.exists(filepath):
                os.remove(filepath)
        subprocess.run(["git", "clean", "-f", "--", filepath], check=True)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        PrettyOutput.print(f"恢复文件失败: {error_msg}", OutputType.ERROR)


# 修改后的恢复函数


def revert_change() -> None:
    """恢复所有未提交的修改到HEAD状态"""
    import subprocess

    try:
        # 检查是否为空仓库
        head_check = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        if head_check.returncode == 0:
            subprocess.run(["git", "reset", "--hard", "HEAD"], check=True)
        subprocess.run(["git", "clean", "-fd"], check=True)
    except subprocess.CalledProcessError as e:
        PrettyOutput.print(f"恢复更改失败: {str(e)}", OutputType.ERROR)


def handle_commit_workflow() -> bool:
    """Handle the git commit workflow and return the commit details.

    Returns:
        bool: 提交是否成功
    """
    if is_confirm_before_apply_patch() and not user_confirm(
        "是否要提交代码？", default=True
    ):
        revert_change()
        return False

    import subprocess

    try:
        confirm_add_new_files()

        if not has_uncommitted_changes():
            return False

        # 获取当前分支的提交总数
        commit_result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"], capture_output=True, text=True
        )
        if commit_result.returncode != 0:
            return False

        commit_count = int(commit_result.stdout.strip())

        # 暂存所有修改
        subprocess.run(["git", "add", "."], check=True)

        # 提交变更
        subprocess.run(
            ["git", "commit", "-m", f"CheckPoint #{commit_count + 1}"], check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def get_latest_commit_hash() -> str:
    """获取当前Git仓库的最新提交哈希值（Rust原生实现）"""
    try:
        from jarvis import jarvis_native as _jarvis_native  # type: ignore
        return str(_jarvis_native.git_get_latest_commit_hash() or "")
    except Exception:
        return ""


def get_modified_line_ranges() -> Dict[str, List[Tuple[int, int]]]:
    """从Git差异中获取所有更改文件的修改行范围（Rust原生实现）"""
    from jarvis import jarvis_native as _jarvis_native  # type: ignore
    try:
        native_res = _jarvis_native.git_modified_line_ranges()
        # 转换为 Dict[str, List[Tuple[int,int]]]
        return {
            str(k): [(int(a), int(b)) for (a, b) in v]
            for k, v in native_res.items()
        }
    except Exception:
        return {}


def is_file_in_git_repo(filepath: str) -> bool:
    """检查文件是否在当前Git仓库中（Rust原生实现）"""
    try:
        from jarvis import jarvis_native as _jarvis_native  # type: ignore
        return bool(_jarvis_native.git_is_file_in_repo(filepath))
    except Exception:
        return False


def check_and_update_git_repo(repo_path: str) -> bool:
    """检查并更新git仓库

    参数:
        repo_path: 仓库路径

    返回:
        bool: 是否执行了更新
    """
    # 检查上次检查日期
    last_check_file = os.path.join(get_data_dir(), "last_git_check")
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    if os.path.exists(last_check_file):
        with open(last_check_file, "r") as f:
            last_check_date = f.read().strip()
        if last_check_date == today_str:
            return False

    curr_dir = os.path.abspath(os.getcwd())
    git_root = find_git_root_and_cd(repo_path)
    if git_root is None:
        return False

    try:
        # 检查是否有未提交的修改
        if has_uncommitted_changes():
            return False

        # 获取远程tag更新
        subprocess.run(["git", "fetch", "--tags"], cwd=git_root, check=True)
        # 获取最新本地tag
        local_tag_result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=git_root,
            capture_output=True,
            text=True,
        )
        # 获取最新远程tag
        remote_tag_result = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", "origin"],
            cwd=git_root,
            capture_output=True,
            text=True,
        )
        if remote_tag_result.returncode == 0:
            # 提取最新的tag名称
            tags = [ref.split("/")[-1] for ref in remote_tag_result.stdout.splitlines()]
            tags = sorted(
                tags,
                key=lambda x: [
                    int(i) if i.isdigit() else i for i in re.split(r"([0-9]+)", x)
                ],
            )
            remote_tag = tags[-1] if tags else ""
            remote_tag_result.stdout = remote_tag

        if (
            local_tag_result.returncode == 0
            and remote_tag_result.returncode == 0
            and local_tag_result.stdout.strip() != remote_tag_result.stdout.strip()
        ):
            PrettyOutput.print(
                f"检测到新版本tag {remote_tag_result.stdout.strip()}，正在更新Jarvis...",
                OutputType.INFO,
            )
            subprocess.run(
                ["git", "checkout", remote_tag_result.stdout.strip()],
                cwd=git_root,
                check=True,
            )
            PrettyOutput.print(
                f"Jarvis已更新到tag {remote_tag_result.stdout.strip()}",
                OutputType.SUCCESS,
            )

            # 执行pip安装更新代码
            try:
                PrettyOutput.print("正在安装更新后的代码...", OutputType.INFO)

                # 检查是否在虚拟环境中
                in_venv = hasattr(sys, "real_prefix") or (
                    hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
                )

                # 检测 uv 可用性：优先虚拟环境内的 uv，其次 PATH 中的 uv
                from shutil import which as _which
                uv_executable = None
                if sys.platform == "win32":
                    venv_uv = os.path.join(sys.prefix, "Scripts", "uv.exe")
                else:
                    venv_uv = os.path.join(sys.prefix, "bin", "uv")
                if os.path.exists(venv_uv):
                    uv_executable = venv_uv
                else:
                    path_uv = _which("uv")
                    if path_uv:
                        uv_executable = path_uv

                # 根据环境选择安装命令
                # 检测是否安装了 RAG 特性（更精确）
                rag_installed = is_rag_installed()

                # 根据 uv 可用性与 RAG 特性选择安装命令（优先使用 uv）
                if uv_executable:
                    if rag_installed:
                        install_cmd = [uv_executable, "pip", "install", "-e", ".[rag]"]
                    else:
                        install_cmd = [uv_executable, "pip", "install", "-e", "."]
                else:
                    if rag_installed:
                        install_cmd = [
                            sys.executable,
                            "-m",
                            "pip",
                            "install",
                            "-e",
                            ".[rag]",
                        ]
                    else:
                        install_cmd = [
                            sys.executable,
                            "-m",
                            "pip",
                            "install",
                            "-e",
                            ".",
                        ]

                # 尝试安装
                result = subprocess.run(
                    install_cmd, cwd=git_root, capture_output=True, text=True
                )

                if result.returncode == 0:
                    PrettyOutput.print("代码更新安装成功", OutputType.SUCCESS)
                    return True

                # 处理权限错误
                error_msg = result.stderr.strip()
                if not in_venv and (
                    "Permission denied" in error_msg or "not writeable" in error_msg
                ):
                    if user_confirm(
                        "检测到权限问题，是否尝试用户级安装(--user)？", True
                    ):
                        user_result = subprocess.run(
                            install_cmd + ["--user"],
                            cwd=git_root,
                            capture_output=True,
                            text=True,
                        )
                        if user_result.returncode == 0:
                            PrettyOutput.print("用户级代码安装成功", OutputType.SUCCESS)
                            return True
                        error_msg = user_result.stderr.strip()

                PrettyOutput.print(f"代码安装失败: {error_msg}", OutputType.ERROR)
                return False
            except Exception as e:
                PrettyOutput.print(
                    f"安装过程中发生意外错误: {str(e)}", OutputType.ERROR
                )
                return False
        # 更新检查日期文件
        with open(last_check_file, "w") as f:
            f.write(today_str)
        return False
    except Exception as e:
        PrettyOutput.print(f"Git仓库更新检查失败: {e}", OutputType.WARNING)
        return False
    finally:
        os.chdir(curr_dir)


def get_diff_file_list() -> List[str]:
    """获取HEAD到当前变更的文件列表，包括修改和新增的文件（Rust原生实现）"""
    try:
        from jarvis import jarvis_native as _jarvis_native  # type: ignore
        confirm_add_new_files()
        files = _jarvis_native.git_get_diff_file_list()
        return [str(f) for f in files if f]
    except Exception as e:
        PrettyOutput.print(f"获取差异文件列表异常: {str(e)}", OutputType.ERROR)
        return []


def get_recent_commits_with_files() -> List[Dict[str, Any]]:
    """获取最近5次提交的commit信息和文件清单

    返回:
        List[Dict[str, Any]]: 包含commit信息和文件清单的字典列表，格式为:
            [
                {
                    'hash': str,
                    'message': str,
                    'author': str,
                    'date': str,
                    'files': List[str]  # 修改的文件列表 (最多20个文件)
                },
                ...
            ]
            失败时返回空列表
    """
    try:
        # 获取当前git用户名
        current_author = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
        ).stdout.strip()

        # 调用原生实现（limit=5）
        from jarvis import jarvis_native as _jarvis_native  # type: ignore
        native_list = _jarvis_native.git_recent_commits_with_files(5, current_author or None)

        # 转换为 List[Dict[str, Any]]
        commits: List[Dict[str, Any]] = []
        for item in native_list:
            try:
                commits.append(
                    {
                        "hash": str(item.get("hash", "")),
                        "message": str(item.get("message", "")),
                        "author": str(item.get("author", "")),
                        "date": str(item.get("date", "")),
                        "files": list(item.get("files", [])),  # type: ignore
                    }
                )
            except Exception:
                continue
        return commits
    except Exception:
        return []


def _get_new_files() -> List[str]:
    """获取新增文件列表（Rust原生实现）"""
    try:
        from jarvis import jarvis_native as _jarvis_native  # type: ignore
        return list(_jarvis_native.git_list_untracked_files())
    except Exception:
        # 回退到git命令
        try:
            return subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
        except Exception:
            return []


def confirm_add_new_files() -> None:
    """确认新增文件、代码行数和二进制文件"""

    def _get_added_lines() -> int:
        """获取新增代码行数"""
        diff_stats = subprocess.run(
            ["git", "diff", "--numstat"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()

        added_lines = 0
        for stat in diff_stats:
            parts = stat.split()
            if len(parts) >= 1:
                try:
                    added_lines += int(parts[0])
                except ValueError:
                    pass
        return added_lines

    def _get_binary_files(files: List[str]) -> List[str]:
        """从文件列表中识别二进制文件"""
        binary_files = []
        for file in files:
            try:
                with open(file, "rb") as f:
                    if b"\x00" in f.read(1024):
                        binary_files.append(file)
            except (IOError, PermissionError):
                continue
        return binary_files

    def _check_conditions(
        new_files: List[str], added_lines: int, binary_files: List[str]
    ) -> bool:
        """检查各种条件并打印提示信息"""
        need_confirm = False
        output_lines = []

        if len(new_files) > 20:
            output_lines.append(f"检测到{len(new_files)}个新增文件(选择N将重新检测)")
            output_lines.append("新增文件列表:")
            output_lines.extend(f"  - {file}" for file in new_files)
            need_confirm = True

        if added_lines > 500:
            output_lines.append(f"检测到{added_lines}行新增代码(选择N将重新检测)")
            need_confirm = True

        if binary_files:
            output_lines.append(
                f"检测到{len(binary_files)}个二进制文件(选择N将重新检测)"
            )
            output_lines.append("二进制文件列表:")
            output_lines.extend(f"  - {file}" for file in binary_files)
            need_confirm = True

        if output_lines:
            PrettyOutput.print(
                "\n".join(output_lines),
                OutputType.WARNING if need_confirm else OutputType.INFO,
            )

        return need_confirm

    while True:
        new_files = _get_new_files()
        added_lines = _get_added_lines()
        binary_files = _get_binary_files(new_files)

        if not _check_conditions(new_files, added_lines, binary_files):
            break

        if not user_confirm(
            "是否要添加这些变更（如果不需要请修改.gitignore文件以忽略不需要的文件）？",
            False,
        ):
            continue

        break
