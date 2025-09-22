# -*- coding: utf-8 -*-
"""
输入处理模块
该模块提供了处理Jarvis系统中用户输入的实用工具。
包含：
- 支持历史记录的单行输入
- 增强补全功能的多行输入
- 带有模糊匹配的文件路径补全
- 用于输入控制的自定义键绑定
"""
import os
import sys
import base64
from typing import Iterable, List, Optional
import wcwidth

from colorama import Fore
from colorama import Style as ColoramaStyle
from fuzzywuzzy import process
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.completion import (
    Completer,
    Completion,
    PathCompleter,
)
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.enums import DEFAULT_BUFFER
from prompt_toolkit.filters import has_focus
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style as PromptStyle

from jarvis.jarvis_utils.clipboard import copy_to_clipboard
from jarvis.jarvis_utils.config import get_data_dir, get_replace_map
from jarvis.jarvis_utils.globals import get_message_history
from jarvis.jarvis_utils.output import OutputType, PrettyOutput
from jarvis.jarvis_utils.tag import ot
from jarvis import jarvis_native as _jarvis_native  # type: ignore

# Sentinel value to indicate that Ctrl+O was pressed
CTRL_O_SENTINEL = "__CTRL_O_PRESSED__"
# Sentinel prefix to indicate that Ctrl+F (fzf) inserted content should prefill next prompt
FZF_INSERT_SENTINEL_PREFIX = "__FZF_INSERT__::"
# Sentinel to request running fzf outside the prompt and then prefill next prompt
FZF_REQUEST_SENTINEL_PREFIX = "__FZF_REQUEST__::"
# Sentinel to request running fzf outside the prompt for all-files mode (exclude .git)
FZF_REQUEST_ALL_SENTINEL_PREFIX = "__FZF_REQUEST_ALL__::"

# Persistent hint marker for multiline input (shown only once across runs)
_MULTILINE_HINT_MARK_FILE = os.path.join(get_data_dir(), "multiline_enter_hint_shown")


def _display_width(s: str) -> int:
    """Calculate printable width of a string in terminal columns (handles wide chars)."""
    try:
        w = 0
        for ch in s:
            cw = wcwidth.wcwidth(ch)
            if cw is None or cw < 0:
                # Fallback for unknown width chars (e.g. emoji on some terminals)
                cw = 1
            w += cw
        return w
    except Exception:
        return len(s)


def _calc_prompt_rows(prev_text: str) -> int:
    """
    Estimate how many terminal rows the previous prompt occupied.
    Considers prompt prefix and soft-wrapping across terminal columns.
    """
    try:
        cols = os.get_terminal_size().columns
    except Exception:
        cols = 80
    prefix = "👤 > "
    prefix_w = _display_width(prefix)

    if prev_text is None:
        return 1

    lines = prev_text.splitlines()
    if not lines:
        lines = [""]
    # If the text ends with a newline, there is a visible empty line at the end.
    if prev_text.endswith("\n"):
        lines.append("")
    total_rows = 0
    for i, line in enumerate(lines):
        lw = _display_width(line)
        if i == 0:
            width = prefix_w + lw
        else:
            width = lw
        rows = max(1, (width + cols - 1) // cols)
        total_rows += rows
    return max(1, total_rows)


def _multiline_hint_already_shown() -> bool:
    """Check if the multiline Enter hint has been shown before (persisted)."""
    try:
        return os.path.exists(_MULTILINE_HINT_MARK_FILE)
    except Exception:
        return False


def _mark_multiline_hint_shown() -> None:
    """Persist that the multiline Enter hint has been shown."""
    try:
        os.makedirs(os.path.dirname(_MULTILINE_HINT_MARK_FILE), exist_ok=True)
        with open(_MULTILINE_HINT_MARK_FILE, "w", encoding="utf-8") as f:
            f.write("1")
    except Exception:
        # Non-critical persistence failure; ignore to avoid breaking input flow
        pass


def get_single_line_input(tip: str, default: str = "") -> str:
    """
    获取支持历史记录的单行输入。
    """
    session: PromptSession = PromptSession(history=None)
    style = PromptStyle.from_dict(
        {"prompt": "ansicyan", "bottom-toolbar": "fg:#888888"}
    )
    prompt = FormattedText([("class:prompt", f"👤 > {tip}")])
    return session.prompt(prompt, default=default, style=style)


def get_choice(tip: str, choices: List[str]) -> str:
    """
    提供一个可滚动的选择列表供用户选择。
    """
    if not choices:
        raise ValueError("Choices cannot be empty.")

    try:
        terminal_height = os.get_terminal_size().lines
    except OSError:
        terminal_height = 25  # 如果无法确定终端大小，则使用默认高度

    # 为提示和缓冲区保留行
    max_visible_choices = max(5, terminal_height - 4)

    bindings = KeyBindings()
    selected_index = 0
    start_index = 0

    @bindings.add("up")
    def _(event):
        nonlocal selected_index, start_index
        selected_index = (selected_index - 1 + len(choices)) % len(choices)
        if selected_index < start_index:
            start_index = selected_index
        elif selected_index == len(choices) - 1:  # 支持从第一项上翻到最后一项时滚动
            start_index = max(0, len(choices) - max_visible_choices)
        event.app.invalidate()

    @bindings.add("down")
    def _(event):
        nonlocal selected_index, start_index
        selected_index = (selected_index + 1) % len(choices)
        if selected_index >= start_index + max_visible_choices:
            start_index = selected_index - max_visible_choices + 1
        elif selected_index == 0:  # 支持从最后一项下翻到第一项时滚动
            start_index = 0
        event.app.invalidate()

    @bindings.add("enter")
    def _(event):
        event.app.exit(result=choices[selected_index])

    def get_prompt_tokens():
        tokens = [("class:question", f"{tip} (使用上下箭头选择, Enter确认)\n")]

        end_index = min(start_index + max_visible_choices, len(choices))
        visible_choices_slice = choices[start_index:end_index]

        if start_index > 0:
            tokens.append(("class:indicator", "  ... (更多选项在上方) ...\n"))

        for i, choice in enumerate(visible_choices_slice, start=start_index):
            if i == selected_index:
                tokens.append(("class:selected", f"> {choice}\n"))
            else:
                tokens.append(("", f"  {choice}\n"))

        if end_index < len(choices):
            tokens.append(("class:indicator", "  ... (更多选项在下方) ...\n"))

        return FormattedText(tokens)

    style = PromptStyle.from_dict(
        {
            "question": "bold",
            "selected": "bg:#696969 #ffffff",
            "indicator": "fg:gray",
        }
    )

    layout = Layout(
        container=Window(
            content=FormattedTextControl(
                text=get_prompt_tokens,
                focusable=True,
                key_bindings=bindings,
            )
        )
    )

    app: Application = Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        mouse_support=True,
        full_screen=True,
    )

    try:
        result = app.run()
        return result if result is not None else ""
    except (KeyboardInterrupt, EOFError):
        return ""


class FileCompleter(Completer):
    """
    带有模糊匹配的文件路径自定义补全器。
    """

    def __init__(self):
        self.path_completer = PathCompleter()
        self.max_suggestions = 10
        self.min_score = 10
        self.replace_map = get_replace_map()
        # Caches for file lists to avoid repeated expensive scans
        self._git_files_cache = None
        self._all_files_cache = None
        self._max_walk_files = 10000

    def get_completions(
        self, document: Document, _: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        cursor_pos = document.cursor_position

        # Support both '@' (git files) and '#' (all files excluding .git)
        sym_positions = [(i, ch) for i, ch in enumerate(text) if ch in ("@", "#")]
        if not sym_positions:
            return
        current_pos = None
        current_sym = None
        for i, ch in sym_positions:
            if i < cursor_pos:
                current_pos = i
                current_sym = ch
        if current_pos is None:
            return

        text_after = text[current_pos + 1 : cursor_pos]
        if " " in text_after:
            return

        token = text_after.strip()
        replace_length = len(text_after) + 1

        all_completions = []
        all_completions.extend(
            [(ot(tag), self._get_description(tag)) for tag in self.replace_map.keys()]
        )
        all_completions.extend(
            [
                (ot("Summary"), "总结"),
                (ot("Clear"), "清除历史"),
                (ot("ToolUsage"), "工具使用说明"),
                (ot("ReloadConfig"), "重新加载配置"),
                (ot("SaveSession"), "保存当前会话"),
            ]
        )

        # File path candidates
        try:
            if current_sym == "@":
                if self._git_files_cache is None:
                    try:
                        self._git_files_cache = _jarvis_native.list_git_files()
                    except Exception:
                        self._git_files_cache = []
                paths = self._git_files_cache or []
            else:
                if self._all_files_cache is None:
                    try:
                        self._all_files_cache = _jarvis_native.list_files_excluding_git(
                            ".", int(self._max_walk_files)
                        )
                    except Exception:
                        self._all_files_cache = []
                paths = self._all_files_cache or []
            all_completions.extend([(path, "File") for path in paths])
        except Exception:
            pass

        if token:
            scored_items = process.extract(
                token,
                [item[0] for item in all_completions],
                limit=self.max_suggestions,
            )
            scored_items = [
                (item[0], item[1]) for item in scored_items if item[1] > self.min_score
            ]
            completion_map = {item[0]: item[1] for item in all_completions}
            for t, score in scored_items:
                display_text = f"{t} ({score}%)" if score < 100 else t
                yield Completion(
                    text=f"'{t}'",
                    start_position=-replace_length,
                    display=display_text,
                    display_meta=completion_map.get(t, ""),
                )
        else:
            for t, desc in all_completions[: self.max_suggestions]:
                yield Completion(
                    text=f"'{t}'",
                    start_position=-replace_length,
                    display=t,
                    display_meta=desc,
                )

    def _get_description(self, tag: str) -> str:
        if tag in self.replace_map:
            return (
                self.replace_map[tag].get("description", tag) + "(Append)"
                if "append" in self.replace_map[tag] and self.replace_map[tag]["append"]
                else "(Replace)"
            )
        return tag


def user_confirm(tip: str, default: bool = True) -> bool:
    """提示用户确认是/否问题"""
    try:
        suffix = "[Y/n]" if default else "[y/N]"
        ret = get_single_line_input(f"{tip} {suffix}: ")
        return default if ret == "" else ret.lower() == "y"
    except KeyboardInterrupt:
        return False


def _show_history_and_copy():
    """
    Displays message history and handles copying to clipboard.
    This function uses standard I/O and is safe to call outside a prompt session.
    """

    history = get_message_history()
    if not history:
        PrettyOutput.print("没有可复制的消息", OutputType.INFO)
        return

    # 为避免 PrettyOutput 在循环中为每行加框，先拼接后统一打印
    lines = []
    lines.append("\n" + "=" * 20 + " 消息历史记录 " + "=" * 20)
    for i, msg in enumerate(history):
        cleaned_msg = msg.replace("\n", r"\n")
        display_msg = (
            (cleaned_msg[:70] + "...") if len(cleaned_msg) > 70 else cleaned_msg
        )
        lines.append(f"  {i + 1}: {display_msg.strip()}")
    lines.append("=" * 58 + "\n")
    PrettyOutput.print("\n".join(lines), OutputType.INFO)

    while True:
        try:
            prompt_text = f"{Fore.CYAN}请输入要复制的条目序号 (或输入c取消, 直接回车选择最后一条): {ColoramaStyle.RESET_ALL}"
            choice_str = input(prompt_text)

            if not choice_str:  # User pressed Enter
                if not history:
                    PrettyOutput.print("没有历史记录可供选择。", OutputType.INFO)
                    break
                choice = len(history) - 1
            elif choice_str.lower() == "c":
                PrettyOutput.print("已取消", OutputType.INFO)
                break
            else:
                choice = int(choice_str) - 1

            if 0 <= choice < len(history):
                selected_msg = history[choice]
                copy_to_clipboard(selected_msg)
                PrettyOutput.print(
                    f"已复制消息: {selected_msg[:70]}...", OutputType.SUCCESS
                )
                break
            else:
                PrettyOutput.print("无效的序号，请重试。", OutputType.WARNING)
        except ValueError:
            PrettyOutput.print("无效的输入，请输入数字。", OutputType.WARNING)
        except (KeyboardInterrupt, EOFError):
            PrettyOutput.print("\n操作取消", OutputType.INFO)
            break


def _get_multiline_input_internal(
    tip: str, preset: Optional[str] = None, preset_cursor: Optional[int] = None
) -> str:
    """
    Internal function to get multiline input using prompt_toolkit.
    Returns a sentinel value if Ctrl+O is pressed.
    """
    bindings = KeyBindings()

    # Show a one-time hint on the first Enter press in this invocation (disabled; using inlay toolbar instead)
    first_enter_hint_shown = True

    @bindings.add("enter")
    def _(event):
        nonlocal first_enter_hint_shown
        if not first_enter_hint_shown and not _multiline_hint_already_shown():
            first_enter_hint_shown = True

            def _show_notice():
                PrettyOutput.print(
                    "提示：当前支持多行输入。输入完成请使用 Ctrl+J 确认；Enter 仅用于换行。",
                    OutputType.INFO,
                )
                try:
                    input("按回车继续...")
                except Exception:
                    pass
                # Persist the hint so it won't be shown again in future runs
                try:
                    _mark_multiline_hint_shown()
                except Exception:
                    pass

            run_in_terminal(_show_notice)
            return

        if event.current_buffer.complete_state:
            completion = event.current_buffer.complete_state.current_completion
            if completion:
                event.current_buffer.apply_completion(completion)
            else:
                event.current_buffer.insert_text("\n")
        else:
            event.current_buffer.insert_text("\n")

    @bindings.add("c-j", filter=has_focus(DEFAULT_BUFFER))
    def _(event):
        event.current_buffer.validate_and_handle()

    @bindings.add("c-o", filter=has_focus(DEFAULT_BUFFER))
    def _(event):
        """Handle Ctrl+O by exiting the prompt and returning the sentinel value."""
        event.app.exit(result=CTRL_O_SENTINEL)

    @bindings.add("c-t", filter=has_focus(DEFAULT_BUFFER))
    def _(event):
        """Return a shell command like '!bash' for upper input_handler to execute."""

        def _gen_shell_cmd() -> str:  # type: ignore
            try:
                import os
                import shutil

                if os.name == "nt":
                    # Prefer PowerShell if available, otherwise fallback to cmd
                    for name in ("pwsh", "powershell", "cmd"):
                        if name == "cmd" or shutil.which(name):
                            if name == "cmd":
                                # Keep session open with /K and set env for the spawned shell
                                return "!cmd /K set JARVIS_TERMINAL=1"
                            else:
                                # PowerShell or pwsh: set env then remain in session
                                return f"!{name} -NoExit -Command \"$env:JARVIS_TERMINAL='1'\""
                else:
                    shell_path = os.environ.get("SHELL", "")
                    if shell_path:
                        base = os.path.basename(shell_path)
                        if base:
                            return f"!env JARVIS_TERMINAL=1 {base}"
                    for name in ("fish", "zsh", "bash", "sh"):
                        if shutil.which(name):
                            return f"!env JARVIS_TERMINAL=1 {name}"
                    return "!env JARVIS_TERMINAL=1 bash"
            except Exception:
                return "!env JARVIS_TERMINAL=1 bash"

        # Append a special marker to indicate no-confirm execution in shell_input_handler
        event.app.exit(result=_gen_shell_cmd() + " # JARVIS-NOCONFIRM")

    @bindings.add("@", filter=has_focus(DEFAULT_BUFFER), eager=True)
    def _(event):
        """
        使用 @ 触发 fzf（当 fzf 存在）；否则仅插入 @ 以启用内置补全
        逻辑：
        - 若检测到系统存在 fzf，则先插入 '@'，随后请求外层运行 fzf 并在返回后进行替换/插入
        - 若不存在 fzf 或发生异常，则直接插入 '@'
        """
        try:
            import shutil

            buf = event.current_buffer
            if shutil.which("fzf") is None:
                buf.insert_text("@")
                return
            # 先插入 '@'，以便外层根据最后一个 '@' 进行片段替换
            buf.insert_text("@")
            doc = buf.document
            text = doc.text
            cursor = doc.cursor_position
            payload = (
                f"{cursor}:{base64.b64encode(text.encode('utf-8')).decode('ascii')}"
            )
            event.app.exit(result=FZF_REQUEST_SENTINEL_PREFIX + payload)
            return
        except Exception:
            try:
                event.current_buffer.insert_text("@")
            except Exception:
                pass

    @bindings.add("#", filter=has_focus(DEFAULT_BUFFER), eager=True)
    def _(event):
        """
        使用 # 触发 fzf（当 fzf 存在），以“全量文件模式”进行选择（排除 .git）；否则仅插入 # 启用内置补全
        """
        try:
            import shutil

            buf = event.current_buffer
            if shutil.which("fzf") is None:
                buf.insert_text("#")
                return
            # 先插入 '#'
            buf.insert_text("#")
            doc = buf.document
            text = doc.text
            cursor = doc.cursor_position
            payload = (
                f"{cursor}:{base64.b64encode(text.encode('utf-8')).decode('ascii')}"
            )
            event.app.exit(result=FZF_REQUEST_ALL_SENTINEL_PREFIX + payload)
            return
        except Exception:
            try:
                event.current_buffer.insert_text("#")
            except Exception:
                pass

    style = PromptStyle.from_dict(
        {
            "prompt": "ansibrightmagenta bold",
            "bottom-toolbar": "bg:#4b145b #ffd6ff bold",
            "bt.tip": "bold fg:#ff5f87",
            "bt.sep": "fg:#ffb3de",
            "bt.key": "bg:#d7005f #ffffff bold",
            "bt.label": "fg:#ffd6ff",
        }
    )

    def _bottom_toolbar():
        return FormattedText(
            [
                ("class:bt.tip", f" {tip} "),
                ("class:bt.sep", " • "),
                ("class:bt.label", "快捷键: "),
                ("class:bt.key", "@"),
                ("class:bt.label", " 文件补全 "),
                ("class:bt.sep", " • "),
                ("class:bt.key", "Tab"),
                ("class:bt.label", " 选择 "),
                ("class:bt.sep", " • "),
                ("class:bt.key", "Ctrl+J"),
                ("class:bt.label", " 确认 "),
                ("class:bt.sep", " • "),
                ("class:bt.key", "Ctrl+O"),
                ("class:bt.label", " 历史复制 "),
                ("class:bt.sep", " • "),
                ("class:bt.key", "Ctrl+T"),
                ("class:bt.label", " 终端(!SHELL) "),
                ("class:bt.sep", " • "),
                ("class:bt.key", "Ctrl+C/D"),
                ("class:bt.label", " 取消 "),
            ]
        )

    history_dir = get_data_dir()
    session: PromptSession = PromptSession(
        history=FileHistory(os.path.join(history_dir, "multiline_input_history")),
        completer=FileCompleter(),
        key_bindings=bindings,
        complete_while_typing=True,
        multiline=True,
        vi_mode=False,
        mouse_support=False,
    )

    # Tip is shown in bottom toolbar; avoid extra print
    prompt = FormattedText([("class:prompt", "👤 > ")])

    def _pre_run():
        try:
            from prompt_toolkit.application.current import get_app as _ga

            app = _ga()
            buf = app.current_buffer
            if preset is not None and preset_cursor is not None:
                cp = max(0, min(len(buf.text), preset_cursor))
                buf.cursor_position = cp
        except Exception:
            pass

    try:
        return session.prompt(
            prompt,
            style=style,
            pre_run=_pre_run,
            bottom_toolbar=_bottom_toolbar,
            default=(preset or ""),
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def get_multiline_input(tip: str, print_on_empty: bool = True) -> str:
    """
    获取带有增强补全和确认功能的多行输入。
    此函数处理控制流，允许在不破坏终端状态的情况下处理历史记录复制。

    参数:
        tip: 提示文本，将显示在底部工具栏中
        print_on_empty: 当输入为空字符串时，是否打印“输入已取消”提示。默认打印。
    """
    preset: Optional[str] = None
    preset_cursor: Optional[int] = None
    while True:
        user_input = _get_multiline_input_internal(
            tip, preset=preset, preset_cursor=preset_cursor
        )

        if user_input == CTRL_O_SENTINEL:
            _show_history_and_copy()
            tip = "请继续输入（或按Ctrl+J确认）:"
            continue
        elif isinstance(user_input, str) and user_input.startswith(
            FZF_REQUEST_SENTINEL_PREFIX
        ):
            # Handle fzf request outside the prompt, then prefill new text.
            try:
                payload = user_input[len(FZF_REQUEST_SENTINEL_PREFIX) :]
                sep_index = payload.find(":")
                cursor = int(payload[:sep_index])
                text = base64.b64decode(
                    payload[sep_index + 1 :].encode("ascii")
                ).decode("utf-8")
            except Exception:
                # Malformed payload; just continue without change.
                preset = None
                tip = "FZF 预填失败，继续输入:"
                continue

            # Run fzf to get a file selection synchronously (outside prompt)
            selected_path = ""
            try:
                import shutil
                import subprocess

                if shutil.which("fzf") is None:
                    PrettyOutput.print(
                        "未检测到 fzf，无法打开文件选择器。", OutputType.WARNING
                    )
                else:
                    files = []
                    try:
                        r = subprocess.run(
                            ["git", "ls-files"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        if r.returncode == 0:
                            files = [
                                line for line in r.stdout.splitlines() if line.strip()
                            ]
                    except Exception:
                        files = []

                    if not files:
                        import os as _os

                        for root, _, fnames in _os.walk(".", followlinks=False):
                            for name in fnames:
                                files.append(
                                    _os.path.relpath(_os.path.join(root, name), ".")
                                )
                            if len(files) > 10000:
                                break

                    if not files:
                        PrettyOutput.print("未找到可选择的文件。", OutputType.INFO)
                    else:
                        try:
                            specials = [
                                ot("Summary"),
                                ot("Clear"),
                                ot("ToolUsage"),
                                ot("ReloadConfig"),
                                ot("SaveSession"),
                            ]
                        except Exception:
                            specials = []
                        try:
                            replace_map = get_replace_map()
                            builtin_tags = [
                                ot(tag)
                                for tag in replace_map.keys()
                                if isinstance(tag, str) and tag.strip()
                            ]
                        except Exception:
                            builtin_tags = []
                        items = (
                            [s for s in specials if isinstance(s, str) and s.strip()]
                            + builtin_tags
                            + files
                        )
                        proc = subprocess.run(
                            [
                                "fzf",
                                "--prompt",
                                "Files> ",
                                "--height",
                                "40%",
                                "--border",
                            ],
                            input="\n".join(items),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        sel = proc.stdout.strip()
                        if sel:
                            selected_path = sel
            except Exception as e:
                PrettyOutput.print(f"FZF 执行失败: {e}", OutputType.ERROR)

            # Compute new text based on selection (or keep original if none)
            if selected_path:
                text_before = text[:cursor]
                last_at = text_before.rfind("@")
                if last_at != -1 and " " not in text_before[last_at + 1 :]:
                    # Replace @... segment
                    inserted = f"'{selected_path}'"
                    new_text = text[:last_at] + inserted + text[cursor:]
                    new_cursor = last_at + len(inserted)
                else:
                    # Plain insert
                    inserted = f"'{selected_path}'"
                    new_text = text[:cursor] + inserted + text[cursor:]
                    new_cursor = cursor + len(inserted)
                preset = new_text
                preset_cursor = new_cursor
                tip = "已插入文件，继续编辑或按Ctrl+J确认:"
            else:
                # No selection; keep original text and cursor
                preset = text
                preset_cursor = cursor
                tip = "未选择文件或已取消，继续编辑:"
            # 清除上一条输入行（多行安全），避免多清，保守仅按提示行估算
            try:
                rows_total = _calc_prompt_rows(text)
                for _ in range(rows_total):
                    sys.stdout.write("\x1b[1A")  # 光标上移一行
                    sys.stdout.write("\x1b[2K\r")  # 清除整行
                sys.stdout.flush()
            except Exception:
                pass
            continue
        elif isinstance(user_input, str) and user_input.startswith(
            FZF_REQUEST_ALL_SENTINEL_PREFIX
        ):
            # Handle fzf request (all-files mode, excluding .git) outside the prompt, then prefill new text.
            try:
                payload = user_input[len(FZF_REQUEST_ALL_SENTINEL_PREFIX) :]
                sep_index = payload.find(":")
                cursor = int(payload[:sep_index])
                text = base64.b64decode(
                    payload[sep_index + 1 :].encode("ascii")
                ).decode("utf-8")
            except Exception:
                # Malformed payload; just continue without change.
                preset = None
                tip = "FZF 预填失败，继续输入:"
                continue

            # Run fzf to get a file selection synchronously (outside prompt) with all files (exclude .git)
            selected_path = ""
            try:
                import shutil
                import subprocess

                if shutil.which("fzf") is None:
                    PrettyOutput.print(
                        "未检测到 fzf，无法打开文件选择器。", OutputType.WARNING
                    )
                else:
                    files = []
                    try:
                        import os as _os

                        for root, dirs, fnames in _os.walk(".", followlinks=False):
                            # Exclude .git directories
                            dirs[:] = [d for d in dirs if d != ".git"]
                            for name in fnames:
                                files.append(
                                    _os.path.relpath(_os.path.join(root, name), ".")
                                )
                                if len(files) > 10000:
                                    break
                            if len(files) > 10000:
                                break
                    except Exception:
                        files = []

                    if not files:
                        PrettyOutput.print("未找到可选择的文件。", OutputType.INFO)
                    else:
                        try:
                            specials = [
                                ot("Summary"),
                                ot("Clear"),
                                ot("ToolUsage"),
                                ot("ReloadConfig"),
                                ot("SaveSession"),
                            ]
                        except Exception:
                            specials = []
                        try:
                            replace_map = get_replace_map()
                            builtin_tags = [
                                ot(tag)
                                for tag in replace_map.keys()
                                if isinstance(tag, str) and tag.strip()
                            ]
                        except Exception:
                            builtin_tags = []
                        items = (
                            [s for s in specials if isinstance(s, str) and s.strip()]
                            + builtin_tags
                            + files
                        )
                        proc = subprocess.run(
                            [
                                "fzf",
                                "--prompt",
                                "Files(all)> ",
                                "--height",
                                "40%",
                                "--border",
                            ],
                            input="\n".join(items),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        sel = proc.stdout.strip()
                        if sel:
                            selected_path = sel
            except Exception as e:
                PrettyOutput.print(f"FZF 执行失败: {e}", OutputType.ERROR)

            # Compute new text based on selection (or keep original if none)
            if selected_path:
                text_before = text[:cursor]
                last_hash = text_before.rfind("#")
                if last_hash != -1 and " " not in text_before[last_hash + 1 :]:
                    # Replace #... segment
                    inserted = f"'{selected_path}'"
                    new_text = text[:last_hash] + inserted + text[cursor:]
                    new_cursor = last_hash + len(inserted)
                else:
                    # Plain insert
                    inserted = f"'{selected_path}'"
                    new_text = text[:cursor] + inserted + text[cursor:]
                    new_cursor = cursor + len(inserted)
                preset = new_text
                preset_cursor = new_cursor
                tip = "已插入文件，继续编辑或按Ctrl+J确认:"
            else:
                # No selection; keep original text and cursor
                preset = text
                preset_cursor = cursor
                tip = "未选择文件或已取消，继续编辑:"
            # 清除上一条输入行（多行安全），避免多清，保守仅按提示行估算
            try:
                rows_total = _calc_prompt_rows(text)
                for _ in range(rows_total):
                    sys.stdout.write("\x1b[1A")
                    sys.stdout.write("\x1b[2K\r")
                sys.stdout.flush()
            except Exception:
                pass
            continue
        elif isinstance(user_input, str) and user_input.startswith(
            FZF_INSERT_SENTINEL_PREFIX
        ):
            # 从哨兵载荷中提取新文本，作为下次进入提示的预填内容
            preset = user_input[len(FZF_INSERT_SENTINEL_PREFIX) :]
            preset_cursor = len(preset)

            # 清除上一条输入行（多行安全），避免多清，保守仅按提示行估算
            try:
                rows_total = _calc_prompt_rows(preset)
                for _ in range(rows_total):
                    sys.stdout.write("\x1b[1A")
                    sys.stdout.write("\x1b[2K\r")
                sys.stdout.flush()
            except Exception:
                pass
            tip = "已插入文件，继续编辑或按Ctrl+J确认:"
            continue
        else:
            if not user_input and print_on_empty:
                PrettyOutput.print("输入已取消", OutputType.INFO)
            return user_input
