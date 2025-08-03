# -*- coding: utf-8 -*-
# 标准库导入
import datetime
import os
import platform
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Union

# 第三方库导入

# 本地库导入
# jarvis_agent 相关
from jarvis.jarvis_agent.prompt_builder import build_action_prompt
from jarvis.jarvis_agent.protocols import OutputHandlerProtocol
from jarvis.jarvis_agent.session_manager import SessionManager
from jarvis.jarvis_agent.tool_executor import execute_tool_call
from jarvis.jarvis_agent.prompts import (
    DEFAULT_SUMMARY_PROMPT,
    SUMMARY_REQUEST_PROMPT,
    TASK_ANALYSIS_PROMPT,
)

# jarvis_platform 相关
from jarvis.jarvis_platform.base import BasePlatform
from jarvis.jarvis_platform.registry import PlatformRegistry

# jarvis_utils 相关
from jarvis.jarvis_utils.config import (
    get_max_token_count,
    get_normal_model_name,
    get_normal_platform_name,
    get_thinking_model_name,
    get_thinking_platform_name,
    is_execute_tool_confirm,
    is_use_analysis,
    is_use_methodology,
)
from jarvis.jarvis_utils.embedding import get_context_token_count
from jarvis.jarvis_utils.globals import (
    delete_agent,
    get_interrupt,
    make_agent_name,
    set_agent,
    set_interrupt,
)
from jarvis.jarvis_utils.input import get_multiline_input, user_confirm
from jarvis.jarvis_utils.methodology import load_methodology, upload_methodology
from jarvis.jarvis_utils.output import OutputType, PrettyOutput
from jarvis.jarvis_utils.tag import ct, ot

origin_agent_system_prompt = f"""
<role>
# 🤖 角色
你是一个专业的任务执行助手，根据用户需求制定并执行详细的计划。
</role>

<rules>
# ❗ 核心规则
1.  **单步操作**: 每个响应必须包含且仅包含一个工具调用。
2.  **任务终结**: 当任务完成时，明确指出任务已完成。这是唯一可以不调用工具的例外。
3.  **无响应错误**: 空响应或仅有分析无工具调用的响应是致命错误，会导致系统挂起。
4.  **决策即工具**: 所有的决策和分析都必须通过工具调用来体现。
5.  **等待结果**: 在继续下一步之前，必须等待当前工具的执行结果。
6.  **持续推进**: 除非任务完成，否则必须生成可操作的下一步。
7.  **记录沉淀**: 如果解决方案有普适价值，应记录为方法论。
8.  **用户语言**: 始终使用用户的语言进行交流。
</rules>

<workflow>
# 🔄 工作流程
1.  **分析**: 理解和分析问题，定义清晰的目标。
2.  **设计**: 设计解决方案并制定详细的行动计划。
3.  **执行**: 按照计划，一次一个步骤地执行。
4.  **完成**: 验证任务是否达成目标，并进行总结。
</workflow>

<system_info>
# 系统信息
- OS: {platform.platform()} {platform.version()}
- Time: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
</system_info>
"""


class Agent:
    def clear(self):
        """
        Clears the current conversation history by delegating to the session manager.
        """
        self.session.clear()

    def __del__(self):
        # 只有在记录启动时才停止记录
        delete_agent(self.name)

    def get_tool_usage_prompt(self) -> str:
        """获取工具使用提示"""
        return build_action_prompt(self.output_handler)  # type: ignore

    def __init__(
        self,
        system_prompt: str,
        name: str = "Jarvis",
        description: str = "",
        llm_type: str = "normal",
        model_group: Optional[str] = None,
        summary_prompt: Optional[str] = None,
        auto_complete: bool = False,
        output_handler: List[OutputHandlerProtocol] = [],
        use_tools: List[str] = [],
        input_handler: Optional[List[Callable[[str, Any], Tuple[str, bool]]]] = None,
        execute_tool_confirm: Optional[bool] = None,
        need_summary: bool = True,
        multiline_inputer: Optional[Callable[[str], str]] = None,
        use_methodology: Optional[bool] = None,
        use_analysis: Optional[bool] = None,
        files: List[str] = [],
    ):
        """初始化Jarvis Agent实例

        参数:
            system_prompt: 系统提示词，定义Agent的行为准则
            name: Agent名称，默认为"Jarvis"
            description: Agent描述信息
            llm_type: LLM类型，可以是 'normal' 或 'thinking'
            summary_prompt: 任务总结提示模板
            auto_complete: 是否自动完成任务
            output_handler: 输出处理器列表
            input_handler: 输入处理器列表
            max_context_length: 最大上下文长度
            execute_tool_confirm: 执行工具前是否需要确认
            need_summary: 是否需要生成总结
            multiline_inputer: 多行输入处理器
            use_methodology: 是否使用方法论
            use_analysis: 是否使用任务分析
        """
        # 基础属性初始化
        self.files = files
        self.name = make_agent_name(name)
        self.description = description
        self.system_prompt = system_prompt
        self.need_summary = need_summary
        self.auto_complete = auto_complete
        self.first = True
        self.run_input_handlers_next_turn = False
        self.user_data: Dict[str, Any] = {}
        self.after_tool_call_cb: Optional[Callable[[Agent], None]] = None

        # 初始化模型和会话
        self._init_model(llm_type, model_group)
        self._init_session()

        # 初始化处理器
        self._init_handlers(output_handler, input_handler, multiline_inputer, use_tools)

        # 初始化配置
        self._init_config(
            use_methodology,
            use_analysis,
            execute_tool_confirm,
            summary_prompt,
            model_group,
        )

        # 设置系统提示词
        self._setup_system_prompt()

        # 打印欢迎信息
        welcome_message = f"{name} 初始化完成 - 使用 {self.model.name()} 模型"  # type: ignore
        PrettyOutput.print(welcome_message, OutputType.SYSTEM)

    def _init_model(self, llm_type: str, model_group: Optional[str]):
        """初始化模型平台"""
        if llm_type == "thinking":
            platform_name = get_thinking_platform_name(model_group)
            model_name = get_thinking_model_name(model_group)
        else:  # 默认为 normal
            platform_name = get_normal_platform_name(model_group)
            model_name = get_normal_model_name(model_group)

        self.model = PlatformRegistry().create_platform(platform_name)
        if self.model is None:
            PrettyOutput.print(
                f"平台 {platform_name} 不存在，将使用普通模型", OutputType.WARNING
            )
            self.model = PlatformRegistry().get_normal_platform()

        if model_name:
            self.model.set_model_name(model_name)

        self.model.set_model_group(model_group)
        self.model.set_suppress_output(False)

    def _init_session(self):
        """初始化会话管理器"""
        self.session = SessionManager(model=self.model, agent_name=self.name)  # type: ignore

    def _init_handlers(
        self,
        output_handler: List[OutputHandlerProtocol],
        input_handler: Optional[List[Callable[[str, Any], Tuple[str, bool]]]],
        multiline_inputer: Optional[Callable[[str], str]],
        use_tools: List[str],
    ):
        """初始化各种处理器"""
        from jarvis.jarvis_tools.registry import ToolRegistry

        self.output_handler = output_handler if output_handler else [ToolRegistry()]
        self.set_use_tools(use_tools)

        self.input_handler = input_handler if input_handler is not None else []

        self.multiline_inputer = (
            multiline_inputer if multiline_inputer else get_multiline_input
        )

    def _init_config(
        self,
        use_methodology: Optional[bool],
        use_analysis: Optional[bool],
        execute_tool_confirm: Optional[bool],
        summary_prompt: Optional[str],
        model_group: Optional[str],
    ):
        """初始化配置选项"""
        # 如果有上传文件，自动禁用方法论
        self.use_methodology = (
            False
            if self.files
            else (
                use_methodology if use_methodology is not None else is_use_methodology()
            )
        )

        self.use_analysis = (
            use_analysis if use_analysis is not None else is_use_analysis()
        )

        self.execute_tool_confirm = (
            execute_tool_confirm
            if execute_tool_confirm is not None
            else is_execute_tool_confirm()
        )

        self.summary_prompt = (
            summary_prompt if summary_prompt else DEFAULT_SUMMARY_PROMPT
        )

        self.max_token_count = get_max_token_count(model_group)

    def _setup_system_prompt(self):
        """设置系统提示词"""
        action_prompt = self.get_tool_usage_prompt()
        self.model.set_system_prompt(  # type: ignore
            f"""
{self.system_prompt}

{action_prompt}
"""
        )

    def set_user_data(self, key: str, value: Any):
        """Sets user data in the session."""
        self.session.set_user_data(key, value)

    def get_user_data(self, key: str) -> Optional[Any]:
        """Gets user data from the session."""
        return self.session.get_user_data(key)

    def set_use_tools(self, use_tools):
        """设置要使用的工具列表"""
        from jarvis.jarvis_tools.registry import ToolRegistry

        for handler in self.output_handler:
            if isinstance(handler, ToolRegistry):
                if use_tools:
                    handler.use_tools(use_tools)
                break

    def set_addon_prompt(self, addon_prompt: str):
        """Sets the addon prompt in the session."""
        self.session.set_addon_prompt(addon_prompt)

    def set_run_input_handlers_next_turn(self, value: bool):
        """Sets the flag to run input handlers on the next turn."""
        self.run_input_handlers_next_turn = value

    def set_after_tool_call_cb(self, cb: Callable[[Any], None]):  # type: ignore
        """设置工具调用后回调函数。

        参数:
            cb: 回调函数
        """
        self.after_tool_call_cb = cb

    def save_session(self) -> bool:
        """Saves the current session state by delegating to the session manager."""
        return self.session.save_session()

    def restore_session(self) -> bool:
        """Restores the session state by delegating to the session manager."""
        if self.session.restore_session():
            self.first = False
            return True
        return False

    def get_tool_registry(self) -> Optional[Any]:
        """获取工具注册表实例"""
        from jarvis.jarvis_tools.registry import ToolRegistry

        for handler in self.output_handler:
            if isinstance(handler, ToolRegistry):
                return handler
        return None

    def _call_model(
        self, message: str, need_complete: bool = False, run_input_handlers: bool = True
    ) -> str:
        """调用AI模型并实现重试逻辑

        参数:
            message: 输入给模型的消息
            need_complete: 是否需要完成任务标记
            run_input_handlers: 是否运行输入处理器

        返回:
            str: 模型的响应

        注意:
            1. 将使用指数退避重试，最多重试30秒
            2. 会自动处理输入处理器链
            3. 会自动添加附加提示
            4. 会检查并处理上下文长度限制
        """
        # 处理输入
        message = self._process_input(message, run_input_handlers)
        if run_input_handlers and self._should_return_early(message):
            return message

        # 添加附加提示
        message = self._add_addon_prompt(message, need_complete)

        # 管理对话长度
        message = self._manage_conversation_length(message)

        # 调用模型
        response = self._invoke_model(message)

        return response

    def _process_input(self, message: str, run_input_handlers: bool) -> str:
        """处理输入消息"""
        if run_input_handlers:
            for handler in self.input_handler:
                message, need_return = handler(message, self)
                if need_return:
                    self._last_handler_returned = True
                    return message
        self._last_handler_returned = False
        return message

    def _should_return_early(self, message: str) -> bool:
        """检查是否需要提前返回"""
        return hasattr(self, "_last_handler_returned") and self._last_handler_returned

    def _add_addon_prompt(self, message: str, need_complete: bool) -> str:
        """添加附加提示到消息"""
        if self.session.addon_prompt:
            message += f"\n\n{self.session.addon_prompt}"
            self.session.addon_prompt = ""
        else:
            message += f"\n\n{self.make_default_addon_prompt(need_complete)}"
        return message

    def _manage_conversation_length(self, message: str) -> str:
        """管理对话长度，必要时进行摘要"""
        self.session.conversation_length += get_context_token_count(message)

        if self.session.conversation_length > self.max_token_count:
            summary = self._summarize_and_clear_history()
            if summary:
                message = summary + "\n\n" + message
            self.session.conversation_length = get_context_token_count(message)

        return message

    def _invoke_model(self, message: str) -> str:
        """实际调用模型获取响应"""
        if not self.model:
            raise RuntimeError("Model not initialized")

        response = self.model.chat_until_success(message)  # type: ignore
        self.session.conversation_length += get_context_token_count(response)

        return response

    def generate_summary(self) -> str:
        """生成对话历史摘要

        返回:
            str: 包含对话摘要的字符串

        注意:
            仅生成摘要，不修改对话状态
        """
        print("📄 正在总结对话历史...")
        try:
            if not self.model:
                raise RuntimeError("Model not initialized")
            summary = self.model.chat_until_success(
                self.session.prompt + "\n" + SUMMARY_REQUEST_PROMPT
            )  # type: ignore
            print("✅ 总结对话历史完成")
            return summary
        except Exception as e:
            print("❌ 总结对话历史失败")
            return ""

    def _summarize_and_clear_history(self) -> str:
        """总结当前对话并清理历史记录

        该方法将:
        1. 调用_generate_summary生成摘要
        2. 清除对话历史
        3. 保留系统消息
        4. 添加摘要作为新上下文
        5. 重置对话长度计数器

        返回:
            str: 包含对话摘要的字符串

        注意:
            当上下文长度超过最大值时使用
        """
        if self._should_use_file_upload():
            return self._handle_history_with_file_upload()
        else:
            return self._handle_history_with_summary()

    def _should_use_file_upload(self) -> bool:
        """判断是否应该使用文件上传方式处理历史"""
        return bool(self.model and self.model.support_upload_files())

    def _handle_history_with_summary(self) -> str:
        """使用摘要方式处理历史"""
        summary = self.generate_summary()
        self.clear_history()

        if not summary:
            return ""

        return self._format_summary_message(summary)

    def _handle_history_with_file_upload(self) -> str:
        """使用文件上传方式处理历史"""
        import tempfile

        tmp_file_name = ""
        try:
            tmp_file = tempfile.NamedTemporaryFile(
                delete=False, mode="w", encoding="utf-8"
            )
            tmp_file_name = tmp_file.name
            tmp_file.write(self.session.prompt)
            tmp_file.close()

            self.clear_history()

            if self.model and self.model.upload_files([tmp_file_name]):
                return "上传的文件是历史对话信息，请基于历史对话信息继续完成任务。"
            else:
                return ""
        finally:
            if tmp_file_name and os.path.exists(tmp_file_name):
                os.remove(tmp_file_name)

    def _format_summary_message(self, summary: str) -> str:
        """格式化摘要消息"""
        return f"""
以下是之前对话的关键信息总结：

<content>
{summary}
</content>

请基于以上信息继续完成任务。请注意，这是之前对话的摘要，上下文长度已超过限制而被重置。请直接继续任务，无需重复已完成的步骤。如有需要，可以询问用户以获取更多信息。
        """

    def _call_tools(self, response: str) -> Tuple[bool, Any]:
        """
        Delegates the tool execution to the external `execute_tool_call` function.
        """
        return execute_tool_call(response, self)

    def _complete_task(self, auto_completed: bool = False) -> str:
        """完成任务并生成总结(如果需要)

        返回:
            str: 任务总结或完成状态

        注意:
            1. 对于主Agent: 可能会生成方法论(如果启用)
            2. 对于子Agent: 可能会生成总结(如果启用)
            3. 使用spinner显示生成状态
        """
        satisfaction_feedback = ""

        if not auto_completed and self.use_analysis:
            if user_confirm("您对本次任务的完成是否满意？", True):
                satisfaction_feedback = "\n\n用户对本次任务的完成表示满意。"
            else:
                feedback = self.multiline_inputer(
                    "请提供您的反馈意见（可留空直接回车）:"
                )
                if feedback:
                    satisfaction_feedback = (
                        f"\n\n用户对本次任务的完成不满意，反馈意见如下：\n{feedback}"
                    )
                else:
                    satisfaction_feedback = (
                        "\n\n用户对本次任务的完成不满意，未提供具体反馈意见。"
                    )

        if self.use_analysis:
            self._analysis_task(satisfaction_feedback)
        else:
            # 如果没有开启分析，也提示用户是否有值得记忆的信息
            self._prompt_memory_save()

        if self.need_summary:
            # 在生成总结前也提示保存记忆（如果之前没有提示过）
            if not self.use_analysis:
                # 已经在上面提示过了，这里不需要重复
                pass
            else:
                # 如果开启了分析，在生成总结前也给一次保存记忆的机会
                self._prompt_memory_save()

            print("📄 正在生成总结...")
            self.session.prompt = self.summary_prompt
            if not self.model:
                raise RuntimeError("Model not initialized")
            ret = self.model.chat_until_success(self.session.prompt)  # type: ignore
            print("✅ 总结生成完成")
            return ret

        return "任务完成"

    def _analysis_task(self, satisfaction_feedback: str = ""):
        """分析任务并生成方法论"""
        print("🔍 正在分析任务...")
        try:
            # 准备分析提示
            self.session.prompt = self._prepare_analysis_prompt(satisfaction_feedback)

            if not self.model:
                raise RuntimeError("Model not initialized")

            # 循环处理工具调用，直到没有工具调用为止
            self._process_analysis_loop()

            print("✅ 分析完成")
        except Exception as e:
            print("❌ 分析失败")

    def _prepare_analysis_prompt(self, satisfaction_feedback: str) -> str:
        """准备分析提示"""
        analysis_prompt = TASK_ANALYSIS_PROMPT
        if satisfaction_feedback:
            analysis_prompt += satisfaction_feedback
        return analysis_prompt

    def _process_analysis_loop(self):
        """处理分析循环"""
        while True:
            response = self.model.chat_until_success(self.session.prompt)  # type: ignore
            self.session.prompt = ""

            # 处理用户中断
            if get_interrupt():
                if not self._handle_analysis_interrupt(response):
                    break

            # 执行工具调用
            need_return, self.session.prompt = self._call_tools(response)

            # 如果没有工具调用或者没有新的提示，退出循环
            if not self.session.prompt:
                break

    def _handle_analysis_interrupt(self, response: str) -> bool:
        """处理分析过程中的用户中断

        返回:
            bool: True 继续分析，False 退出分析
        """
        set_interrupt(False)
        user_input = self.multiline_inputer(f"分析任务期间被中断，请输入用户干预信息：")

        if not user_input:
            # 用户输入为空，退出分析
            return False

        if self._has_tool_calls(response):
            self.session.prompt = self._handle_interrupt_with_tool_calls(user_input)
        else:
            self.session.prompt = f"被用户中断，用户补充信息为：{user_input}"

        return True

    def _has_tool_calls(self, response: str) -> bool:
        """检查响应中是否有工具调用"""
        return any(handler.can_handle(response) for handler in self.output_handler)

    def _handle_interrupt_with_tool_calls(self, user_input: str) -> str:
        """处理有工具调用时的中断"""
        if user_confirm("检测到有工具调用，是否继续处理工具调用？", True):
            return f"被用户中断，用户补充信息为：{user_input}\n\n用户同意继续工具调用。"
        else:
            return f"被用户中断，用户补充信息为：{user_input}\n\n检测到有工具调用，但被用户拒绝执行。请根据用户的补充信息重新考虑下一步操作。"

    def make_default_addon_prompt(self, need_complete: bool) -> str:
        """生成附加提示。

        参数:
            need_complete: 是否需要完成任务

        """
        # 结构化系统指令
        action_handlers = ", ".join([handler.name() for handler in self.output_handler])

        # 任务完成提示
        complete_prompt = (
            f"- 输出{ot('!!!COMPLETE!!!')}"
            if need_complete and self.auto_complete
            else ""
        )

        # 检查工具列表并添加记忆工具相关提示
        memory_prompts = ""
        tool_registry = self.get_tool_registry()
        if tool_registry:
            tool_names = [tool.name for tool in tool_registry.tools.values()]

            # 如果有save_memory工具，添加相关提示
            if "save_memory" in tool_names:
                memory_prompts += (
                    "\n    - 如果有关键信息需要记忆，请调用save_memory工具进行记忆："
                )
                memory_prompts += (
                    "\n      * project_long_term: 保存与当前项目相关的长期信息"
                )
                memory_prompts += (
                    "\n      * global_long_term: 保存通用的信息、用户喜好、知识、方法等"
                )
                memory_prompts += "\n      * short_term: 保存当前任务相关的临时信息"

            # 如果有retrieve_memory工具，添加相关提示
            if "retrieve_memory" in tool_names:
                memory_prompts += (
                    "\n    - 如果需要检索相关记忆信息，请调用retrieve_memory工具"
                )

        addon_prompt = f"""
<system_prompt>
    请判断是否已经完成任务，如果已经完成：
    - 直接输出完成原因，不需要再有新的操作，不要输出{ot("TOOL_CALL")}标签
    {complete_prompt}
    如果没有完成，请进行下一步操作：
    - 仅包含一个操作
    - 如果信息不明确，请请求用户补充
    - 如果执行过程中连续失败5次，请使用ask_user询问用户操作
    - 操作列表：{action_handlers}{memory_prompts}
</system_prompt>

请继续。
"""

        return addon_prompt

    def run(self, user_input: str) -> Any:
        """处理用户输入并执行任务

        参数:
            user_input: 任务描述或请求

        返回:
            str|Dict: 任务总结报告或要发送的消息

        注意:
            1. 这是Agent的主运行循环
            2. 处理完整的任务生命周期
            3. 包含错误处理和恢复逻辑
            4. 自动加载相关方法论(如果是首次运行)
        """
        self.session.prompt = f"{user_input}"
        try:
            set_agent(self.name, self)
            return self._main_loop()
        except Exception as e:
            PrettyOutput.print(f"任务失败: {str(e)}", OutputType.ERROR)
            return f"Task failed: {str(e)}"

    def _main_loop(self) -> Any:
        """主运行循环"""
        run_input_handlers = True

        while True:
            try:
                # 更新输入处理器标志
                if self.run_input_handlers_next_turn:
                    run_input_handlers = True
                    self.run_input_handlers_next_turn = False

                # 首次运行初始化
                if self.first:
                    self._first_run()

                # 调用模型获取响应
                current_response = self._call_model(
                    self.session.prompt, True, run_input_handlers
                )
                self.session.prompt = ""
                run_input_handlers = False

                # 处理中断
                interrupt_result = self._handle_run_interrupt(current_response)
                if interrupt_result:
                    if isinstance(interrupt_result, tuple):
                        run_input_handlers, should_continue = interrupt_result
                        if should_continue:
                            continue
                    else:
                        return interrupt_result

                # 处理工具调用
                need_return, self.session.prompt = self._call_tools(current_response)
                if need_return:
                    return self.session.prompt

                # 执行回调
                if self.after_tool_call_cb:
                    self.after_tool_call_cb(self)

                # 检查是否需要继续
                if self.session.prompt or self.session.addon_prompt:
                    continue

                # 检查自动完成
                if self.auto_complete and ot("!!!COMPLETE!!!") in current_response:
                    return self._complete_task(auto_completed=True)

                # 获取下一步用户输入
                next_action = self._get_next_user_action()
                if next_action == "continue":
                    run_input_handlers = True
                    continue
                elif next_action == "complete":
                    return self._complete_task(auto_completed=False)

            except Exception as e:
                PrettyOutput.print(f"任务失败: {str(e)}", OutputType.ERROR)
                return f"Task failed: {str(e)}"

    def _handle_run_interrupt(
        self, current_response: str
    ) -> Optional[Union[Any, Tuple[bool, bool]]]:
        """处理运行中的中断

        返回:
            None: 无中断，继续执行
            Any: 需要返回的结果
            Tuple[bool, bool]: (run_input_handlers, should_continue)
        """
        if not get_interrupt():
            return None

        set_interrupt(False)
        user_input = self.multiline_inputer(f"模型交互期间被中断，请输入用户干预信息：")

        if not user_input:
            # 用户输入为空，完成任务
            return self._complete_task(auto_completed=False)

        if self._has_tool_calls(current_response):
            if user_confirm("检测到有工具调用，是否继续处理工具调用？", True):
                self.session.prompt = f"被用户中断，用户补充信息为：{user_input}\n\n用户同意继续工具调用。"
                return None  # 继续执行工具调用
            else:
                self.session.prompt = f"被用户中断，用户补充信息为：{user_input}\n\n检测到有工具调用，但被用户拒绝执行。请根据用户的补充信息重新考虑下一步操作。"
                return (True, True)  # run_input_handlers=True, should_continue=True
        else:
            self.session.prompt = f"被用户中断，用户补充信息为：{user_input}"
            return (True, True)  # run_input_handlers=True, should_continue=True

    def _get_next_user_action(self) -> str:
        """获取用户下一步操作

        返回:
            str: "continue" 或 "complete"
        """
        user_input = self.multiline_inputer(
            f"{self.name}: 请输入，或输入空行来结束当前任务："
        )

        if user_input:
            self.session.prompt = user_input
            return "continue"
        else:
            return "complete"

    def _first_run(self):
        """首次运行初始化"""
        # 准备记忆标签提示
        memory_tags_prompt = self._prepare_memory_tags_prompt()

        # 处理文件上传和方法论加载
        self._handle_files_and_methodology()

        # 添加记忆标签提示
        if memory_tags_prompt:
            self.session.prompt = f"{self.session.prompt}{memory_tags_prompt}"

        self.first = False

    def _prepare_memory_tags_prompt(self) -> str:
        """准备记忆标签提示"""
        from jarvis.jarvis_utils.globals import get_all_memory_tags

        memory_tags = get_all_memory_tags()
        memory_tags_prompt = ""

        # 检查是否有save_memory工具
        if self._has_save_memory_tool():
            memory_tags_prompt = "\n\n💡 提示：在分析任务之前，建议使用 save_memory 工具将关键信息记录下来，便于后续检索和复用。"

        # 构建记忆标签列表
        if any(tags for tags in memory_tags.values()):
            memory_tags_prompt += self._format_memory_tags(memory_tags)

        return memory_tags_prompt

    def _has_save_memory_tool(self) -> bool:
        """检查是否有save_memory工具"""
        tool_registry = self.get_tool_registry()
        if tool_registry:
            tool_names = [tool.name for tool in tool_registry.tools.values()]
            return "save_memory" in tool_names
        return False

    def _format_memory_tags(self, memory_tags: dict) -> str:
        """格式化记忆标签"""
        prompt = (
            "\n\n系统中存在以下记忆标签，你可以使用 retrieve_memory 工具检索相关记忆："
        )

        type_names = {
            "short_term": "短期记忆",
            "project_long_term": "项目长期记忆",
            "global_long_term": "全局长期记忆",
        }

        for memory_type, tags in memory_tags.items():
            if tags:
                type_name = type_names.get(memory_type, memory_type)
                prompt += f"\n- {type_name}: {', '.join(tags)}"

        return prompt

    def _handle_files_and_methodology(self):
        """处理文件上传和方法论加载"""
        if self.model and self.model.support_upload_files():
            self._handle_file_upload_mode()
        else:
            self._handle_local_mode()

    def _handle_file_upload_mode(self):
        """处理支持文件上传的模式"""
        if self.use_methodology:
            self._handle_methodology_upload()
        elif self.files:
            self._handle_files_upload()

    def _handle_methodology_upload(self):
        """处理方法论上传"""
        if not upload_methodology(self.model, other_files=self.files):  # type: ignore
            if self.files:
                PrettyOutput.print("文件上传失败，将忽略文件列表", OutputType.WARNING)
            # 上传失败则回退到本地加载
            self._load_local_methodology()
        else:
            # 上传成功
            memory_tags_prompt = self._prepare_memory_tags_prompt()
            if self.files:
                self.session.prompt = f"{self.session.prompt}\n\n上传的文件包含历史对话信息和方法论文件，可以从中获取一些经验信息。{memory_tags_prompt}"
            else:
                self.session.prompt = f"{self.session.prompt}\n\n上传的文件包含历史对话信息，可以从中获取一些经验信息。{memory_tags_prompt}"

    def _handle_files_upload(self):
        """处理普通文件上传"""
        if not self.model.upload_files(self.files):  # type: ignore
            PrettyOutput.print("文件上传失败，将忽略文件列表", OutputType.WARNING)
        else:
            self.session.prompt = f"{self.session.prompt}\n\n上传的文件包含历史对话信息，可以从中获取一些经验信息。"

    def _handle_local_mode(self):
        """处理本地模式（不支持文件上传）"""
        if self.files:
            PrettyOutput.print("不支持上传文件，将忽略文件列表", OutputType.WARNING)
        if self.use_methodology:
            self._load_local_methodology()

    def _load_local_methodology(self):
        """加载本地方法论"""
        msg = self.session.prompt
        for handler in self.input_handler:
            msg, _ = handler(msg, self)

        memory_tags_prompt = self._prepare_memory_tags_prompt()
        methodology = load_methodology(msg, self.get_tool_registry())
        self.session.prompt = f"{self.session.prompt}\n\n以下是历史类似问题的执行经验，可参考：\n{methodology}{memory_tags_prompt}"

    def _prompt_memory_save(self):
        """让大模型自动判断并保存值得记忆的信息"""
        # 检查是否有记忆相关工具
        tool_registry = self.get_tool_registry()
        if not tool_registry:
            return

        tool_names = [tool.name for tool in tool_registry.tools.values()]
        if "save_memory" not in tool_names:
            return

        print("🔍 正在分析是否有值得记忆的信息...")

        # 构建提示词，让大模型自己判断并保存记忆
        prompt = """请回顾本次任务的整个过程，判断是否有值得长期记忆或项目记忆的信息。

如果有以下类型的信息，请使用 save_memory 工具保存：
1. 解决问题的新方法或技巧（适合保存为 global_long_term）
2. 项目相关的重要发现或配置（适合保存为 project_long_term）
3. 用户的偏好或习惯（适合保存为 global_long_term）
4. 重要的技术知识或经验（适合保存为 global_long_term）
5. 项目特定的实现细节或约定（适合保存为 project_long_term）

请分析并保存有价值的信息，选择合适的记忆类型和标签。如果没有值得记忆的信息，请直接说明。"""

        # 处理记忆保存
        try:
            response = self.model.chat_until_success(prompt)  # type: ignore

            # 执行工具调用（如果有）
            need_return, result = self._call_tools(response)

            # 根据响应判断是否保存了记忆
            if "save_memory" in response:
                print("✅ 已自动保存有价值的信息到记忆系统")
            else:
                print("📝 本次任务没有特别需要记忆的信息")

        except Exception as e:
            print(f"❌ 记忆分析失败: {str(e)}")

    def clear_history(self):
        """
        Clears conversation history by delegating to the session manager.
        """
        self.session.clear_history()
