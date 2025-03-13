import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from jarvis.jarvis_agent.output_handler import OutputHandler
from jarvis.jarvis_platform.registry import PlatformRegistry
from jarvis.jarvis_tools.base import Tool
from jarvis.jarvis_utils.config import get_max_token_count
from jarvis.jarvis_utils.embedding import get_context_token_count
from jarvis.jarvis_utils.output import OutputType, PrettyOutput



tool_call_help = """
# 🛠️ Tool Usage System
You are using a tool execution system that requires precise formatting and strict rules.

# 📋 Tool Call Format
<TOOL_CALL>
name: tool_name
arguments:
    param1: value1
    param2: value2
</TOOL_CALL>

# ❗ Critical Rules
1. ONE Tool Per Turn
   - Execute only ONE tool at a time
   - Wait for results before next action

2. Strict Format Adherence
   - Follow exact format shown above
   - Use proper YAML indentation
   - Include all required parameters

3. Result Handling
   - Wait for execution results
   - Never assume outcomes
   - Don't create fake responses
   - Don't imagine dialogues

4. Information Management
   - Ask user if info is insufficient
   - Skip unnecessary steps
   - Request guidance if stuck
   - Don't proceed with incomplete info

# 📝 String Parameter Format
ALWAYS use | syntax for string parameters:

<TOOL_CALL>
name: execute_shell
arguments:
    command: |
        git status --porcelain
</TOOL_CALL>

# 💡 Best Practices
- Start execution immediately when ready
- No need to ask for permission to begin
- Use proper string formatting
- Monitor progress and adjust
- Request help when stuck

# ⚠️ Common Mistakes to Avoid
- Multiple tool calls at once
- Missing | for string parameters
- Assuming tool results
- Creating fictional dialogues
- Proceeding without required info
"""

class ToolRegistry(OutputHandler):

    def name(self) -> str:
        return "TOOL_CALL"

    def can_handle(self, response: str) -> bool:
        if self._extract_tool_calls(response):
            return True
        return False
    
    def prompt(self) -> str:
        """Load tools"""
        tools = self.get_all_tools()
        if tools:
            tools_prompt = "## Available tools:\n"
            for tool in tools:
                tools_prompt += f"- Name: {tool['name']}\n"
                tools_prompt += f"  Description: {tool['description']}\n"
                tools_prompt += f"  Parameters: {tool['parameters']}\n"
            tools_prompt += tool_call_help
            return tools_prompt
        return ""
    
    def handle(self, response: str) -> Tuple[bool, Any]:
        tool_calls = self._extract_tool_calls(response)
        if len(tool_calls) > 1:
            PrettyOutput.print(f"操作失败：检测到多个操作。一次只能执行一个操作。尝试执行的操作：{', '.join([tool_call['name'] for tool_call in tool_calls])}", OutputType.WARNING)
            return False, f"Call failed: Handle multiple tool calls, please ONLY handle one tool call at a time."
        if len(tool_calls) == 0:
            return False, ""
        tool_call = tool_calls[0]
        return False, self.handle_tool_calls(tool_call)

    def __init__(self):
        """Initialize tool registry"""
        self.tools: Dict[str, Tool] = {}
        # Load built-in tools and external tools
        self._load_builtin_tools()
        self._load_external_tools()
        # Ensure max_token_count is an integer
        self.max_token_count = int(get_max_token_count() * 0.8)

    def use_tools(self, name: List[str]):
        """Use specified tools"""
        missing_tools = [tool_name for tool_name in name if tool_name not in self.tools]
        if missing_tools:
            PrettyOutput.print(f"工具 {missing_tools} 不存在，可用的工具有: {', '.join(self.tools.keys())}", OutputType.WARNING)
        self.tools = {tool_name: self.tools[tool_name] for tool_name in name}

    def dont_use_tools(self, names: List[str]):
        """Remove specified tools from the registry"""
        self.tools = {name: tool for name, tool in self.tools.items() if name not in names}

    def _load_builtin_tools(self):
        """Load tools from the built-in tools directory"""
        tools_dir = Path(__file__).parent
        
        # Iterate through all .py files in the directory
        for file_path in tools_dir.glob("*.py"):
            # Skip base.py and __init__.py
            if file_path.name in ["base.py", "__init__.py", "registry.py"]:
                continue
                
            self.register_tool_by_file(str(file_path))

    def _load_external_tools(self):
        """Load external tools from ~/.jarvis/tools"""
        external_tools_dir = Path.home() / '.jarvis/tools'
        if not external_tools_dir.exists():
            return
            
        # Iterate through all .py files in the directory
        for file_path in external_tools_dir.glob("*.py"):
            # Skip __init__.py
            if file_path.name == "__init__.py":
                continue
                
            self.register_tool_by_file(str(file_path))

    def register_tool_by_file(self, file_path: str):
        """Load and register tools from a specified file
        
        Args:
            file_path: The path of the tool file
            
        Returns:
            bool: Whether the tool is loaded successfully
        """
        try:
            p_file_path = Path(file_path).resolve()  # Get the absolute path
            if not p_file_path.exists() or not p_file_path.is_file():
                PrettyOutput.print(f"文件不存在: {p_file_path}", OutputType.ERROR)
                return False
                
            # Add the parent directory to sys.path temporarily
            parent_dir = str(p_file_path.parent)
            sys.path.insert(0, parent_dir)
            
            try:
                # Import the module using standard import mechanism
                module_name = p_file_path.stem
                module = __import__(module_name)
                
                # Find the tool class in the module
                tool_found = False
                for item_name in dir(module):
                    item = getattr(module, item_name)
                    # Check if it is a class and has the necessary attributes
                    if (isinstance(item, type) and 
                        hasattr(item, 'name') and 
                        hasattr(item, 'description') and 
                        hasattr(item, 'parameters') and
                        hasattr(item, 'execute') and 
                        item.name == module_name):

                        if hasattr(item, "check"):
                            if not item.check():
                                continue
                        
                        # Instantiate the tool class
                        tool_instance = item()
                        
                        # Register the tool
                        self.register_tool(
                            name=tool_instance.name,
                            description=tool_instance.description,
                            parameters=tool_instance.parameters,
                            func=tool_instance.execute
                        )
                        tool_found = True
                        break
                        
                if not tool_found:
                    return False
                    
                return True
                
            finally:
                # Remove the directory from sys.path
                sys.path.remove(parent_dir)
                
        except Exception as e:
            PrettyOutput.print(f"从 {Path(file_path).name} 加载工具失败: {str(e)}", OutputType.ERROR)
            return False
    @staticmethod
    def _extract_tool_calls(content: str) -> List[Dict]:
        """Extract tool calls from content.
        
        Args:
            content: The content containing tool calls
            
        Returns:
            List[Dict]: List of extracted tool calls with name and arguments
            
        Raises:
            Exception: If tool call is missing necessary fields
        """
        # Split content into lines
        data = re.findall(r'<TOOL_CALL>(.*?)</TOOL_CALL>', content, re.DOTALL)
        ret = []
        for item in data:
            try:
                msg = yaml.safe_load(item)
                if 'name' in msg and 'arguments' in msg:
                    ret.append(msg)
            except Exception as e:
                continue
        return ret

    def register_tool(self, name: str, description: str, parameters: Dict, func: Callable):
        """Register a new tool"""
        self.tools[name] = Tool(name, description, parameters, func)

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get a tool"""
        return self.tools.get(name)

    def get_all_tools(self) -> List[Dict]:
        """Get all tools in Ollama format definition"""
        return [tool.to_dict() for tool in self.tools.values()]

    def execute_tool(self, name: str, arguments: Dict) -> Dict[str, Any]:
        """Execute a specified tool"""
        tool = self.get_tool(name)
        if tool is None:
            return {"success": False, "stderr": f"Tool {name} does not exist, available tools: {', '.join(self.tools.keys())}", "stdout": ""}
        return tool.execute(arguments)

    def handle_tool_calls(self, tool_call: Dict) -> str:
        """Handle tool calls, only process the first tool"""
        try:
            # Only process the first tool call
            name = tool_call["name"]
            args = tool_call["arguments"]

            tool_call_help = """
# 🛠️ 工具使用系统
您正在使用一个需要精确格式和严格规则的工具执行系统。

# 📋 工具调用格式

<TOOL_CALL>
name: 工具名称
arguments:
    param1: 值1
    param2: 值2
</TOOL_CALL>

# ❗ 关键规则
1. 每次只使用一个工具
   - 一次只执行一个工具
   - 等待结果后再进行下一步

2. 严格遵守格式
   - 完全按照上述格式
   - 使用正确的YAML缩进
   - 包含所有必需参数

3. 结果处理
   - 等待执行结果
   - 不要假设结果
   - 不要创建虚假响应
   - 不要想象对话

4. 信息管理
   - 如果信息不足，询问用户
   - 跳过不必要的步骤
   - 如果卡住，请求指导
   - 不要在没有完整信息的情况下继续

# 📝 字符串参数格式
始终使用 | 语法表示字符串参数：

<TOOL_CALL>
name: execute_shell
arguments:
    command: |
        git status --porcelain
</TOOL_CALL>

# 💡 最佳实践
- 准备好后立即开始执行
- 无需请求许可即可开始
- 使用正确的字符串格式
- 监控进度并调整
- 遇到困难时请求帮助

# ⚠️ 常见错误
- 同时调用多个工具
- 字符串参数缺少 |
- 假设工具结果
- 创建虚构对话
- 在没有所需信息的情况下继续
"""
            
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    PrettyOutput.print(f"工具参数格式无效: {name} {tool_call_help}", OutputType.ERROR)
                    return ""

            # Display tool call information
            PrettyOutput.section(f"执行工具: {name}", OutputType.TOOL)
            params = "参数:\n"
            if isinstance(args, dict):
                for key, value in args.items():
                    params += f"{key} = {value}\n"
            else:
                params += f"{args}"

            PrettyOutput.print(params, OutputType.INFO)
            
            # Execute tool call
            result = self.execute_tool(name, args)

            stdout = result["stdout"]
            stderr = result.get("stderr", "")
            output_parts = []
            if stdout:
                output_parts.append(f"输出:\n{stdout}")
            if stderr:
                output_parts.append(f"错误:\n{stderr}")
            output = "\n\n".join(output_parts)
            output = "无输出和错误" if not output else output
            
            # Process the result
            if result["success"]:
                
                PrettyOutput.section("执行成功", OutputType.SUCCESS)
                
                # If the output exceeds 4k characters, use a large model to summarize
                if get_context_token_count(output) > self.max_token_count:
                    try:
                        PrettyOutput.print("输出过长，正在总结...", OutputType.PROGRESS)
                        model = PlatformRegistry.get_global_platform_registry().get_normal_platform()
                        
                        # If the output exceeds the maximum context length, only take the last part
                        max_count = self.max_token_count
                        if get_context_token_count(output) > max_count:
                            output_to_summarize = output[-max_count:]
                            truncation_notice = f"\n(注意：由于输出过长，仅总结最后 {max_count} 个字符)"
                        else:
                            output_to_summarize = output
                            truncation_notice = ""

                        prompt = f"""请总结以下工具的执行结果，提取关键信息和重要结果。注意：
1. 保留所有重要的数值、路径、错误信息等
2. 保持结果的准确性
3. 用简洁的语言描述主要内容
4. 如果有错误信息，确保包含在总结中

工具名称: {name}
执行结果:
{output_to_summarize}

请提供总结:"""

                        summary = model.chat_until_success(prompt)
                        output = f"""--- 原始输出过长，以下是总结 ---{truncation_notice}

{summary}

--- 总结结束 ---"""
                        
                    except Exception as e:
                        PrettyOutput.print(f"总结失败: {str(e)}", OutputType.ERROR)
                        output = f"输出过长 ({len(output)} 字符)，建议查看原始输出。\n前300字符预览:\n{output[:300]}..."
            
            else:
                PrettyOutput.section("执行失败", OutputType.WARNING)
                PrettyOutput.print(result["stderr"], OutputType.WARNING)
            return output
            
        except Exception as e:
            PrettyOutput.print(f"工具执行失败：{str(e)}", OutputType.ERROR)
            return f"工具调用失败: {str(e)}"
