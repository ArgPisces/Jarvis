import re
from typing import Dict, Any, List, Tuple
import os
from jarvis.jarvis_agent.output_handler import OutputHandler
from jarvis.jarvis_platform.registry import PlatformRegistry
from jarvis.jarvis_tools.git_commiter import GitCommitTool
from jarvis.jarvis_tools.read_code import ReadCodeTool
from jarvis.jarvis_tools.execute_shell_script import ShellScriptTool
from jarvis.jarvis_utils import OutputType, PrettyOutput, get_multiline_input, has_uncommitted_changes, is_confirm_before_apply_patch, user_confirm


class PatchOutputHandler(OutputHandler):
    def name(self) -> str:
        return "PATCH"

    def handle(self, response: str) -> Tuple[bool, Any]:
        return False, apply_patch(response)
    
    def can_handle(self, response: str) -> bool:
        if _parse_patch(response):
            return True
        return False
    
    def prompt(self) -> str:
        return """
# 🛠️ Contextual Code Patch Specification

Use <PATCH> blocks to specify code changes:
--------------------------------
<PATCH>
File: [file_path]
Reason: [change_reason]
```language_identifier
[contextual_code_snippet]
```
--------------------------------

Rules:
1. Code snippets must include sufficient context (3 lines before/after)
2. Only show modified code sections
3. Preserve original indentation and formatting
4. For new files, provide complete code
5. When modifying existing files, retain surrounding unchanged code

Example:
<PATCH>
File: src/utils/math.py
Reason: Fix zero division handling
```python
def safe_divide(a, b):
    # Add parameter validation
    if b == 0:
        raise ValueError("Divisor cannot be zero")
    return a / b
```
</PATCH>
"""


def _parse_patch(patch_str: str) -> Dict[str, List[Dict[str, Any]]]:
    """解析新的上下文补丁格式"""
    result = {}
    patches = re.findall(r'<PATCH>\n?(.*?)\n?</PATCH>', patch_str, re.DOTALL)
    
    for patch in patches:
        file_match = re.search(r'^File:\s*(.+)$', patch, re.MULTILINE)
        reason_match = re.search(r'^Reason:\s*(.+)$', patch, re.MULTILINE)
        code_match = re.search(r'^(```.*?\n)(.*?)(\n```)?$', patch, re.DOTALL)
        
        if not file_match or not code_match:
            PrettyOutput.print("无效的补丁格式", OutputType.WARNING)
            continue

        filepath = file_match.group(1).strip()
        reason = reason_match.group(1).strip() if reason_match else ""
        code = code_match.group(2).strip() + '\n'  # 保留原始格式
        
        if filepath not in result:
            result[filepath] = []
        result[filepath].append({
            'filepath': filepath,
            'reason': reason,
            'content': code
        })
    return result


def apply_patch(output_str: str) -> str:
    """Apply patches to files"""
    try:
        patches = _parse_patch(output_str)
    except Exception as e:
        PrettyOutput.print(f"解析补丁失败: {str(e)}", OutputType.ERROR)
        return ""
    
    # 按文件逐个处理
    for filepath, patch_list in patches.items():
        file_ret = ""
        try:
            PrettyOutput.print(f"应用补丁到文件: {filepath}", OutputType.INFO)
            handle_code_operation(filepath, patch_list)
            PrettyOutput.print(f"文件 {filepath} 处理完成", OutputType.SUCCESS)
        except Exception as e:
            revert_file(filepath)  # 回滚单个文件
            PrettyOutput.print(f"文件 {filepath} 处理失败: {str(e)}", OutputType.ERROR)
    
    final_ret = ""
    diff = get_diff()
    if diff:
        PrettyOutput.print(diff, OutputType.CODE, lang="diff")
    if diff and handle_commit_workflow():
        final_ret += "✅ The patches have been applied"
    else:
        final_ret += "❌ I don't want to commit the code"

    # 用户确认最终结果
    PrettyOutput.print(final_ret, OutputType.USER)
    if user_confirm("是否使用此回复？", default=True):
        return final_ret
    return get_multiline_input("请输入自定义回复")

def revert_file(filepath: str):
    """增强版git恢复，处理新文件"""
    import subprocess
    try:
        # 检查文件是否在版本控制中
        result = subprocess.run(
            ['git', 'ls-files', '--error-unmatch', filepath],
            stderr=subprocess.PIPE
        )
        if result.returncode == 0:
            subprocess.run(['git', 'checkout', 'HEAD', '--', filepath], check=True)
        else:
            if os.path.exists(filepath):
                os.remove(filepath)
        subprocess.run(['git', 'clean', '-f', '--', filepath], check=True)
    except subprocess.CalledProcessError as e:
        PrettyOutput.print(f"恢复文件失败: {str(e)}", OutputType.ERROR)

# 修改后的恢复函数
def revert_change():
    import subprocess
    subprocess.run(['git', 'reset', '--hard', 'HEAD'], check=True)
    subprocess.run(['git', 'clean', '-fd'], check=True)

# 修改后的获取差异函数
def get_diff() -> str:
    """使用git获取暂存区差异"""
    import subprocess
    try:
        result = subprocess.run(
            ['git', 'diff', '--staged'],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"获取差异失败: {str(e)}"

def handle_commit_workflow()->bool:
    """Handle the git commit workflow and return the commit details.
    
    Returns:
        tuple[bool, str, str]: (continue_execution, commit_id, commit_message)
    """
    if is_confirm_before_apply_patch() and not user_confirm("是否要提交代码？", default=True):
        revert_change()
        return False

    git_commiter = GitCommitTool()
    commit_result = git_commiter.execute({})
    return commit_result["success"]

def get_modified_line_ranges() -> Dict[str, Tuple[int, int]]:
    """Get modified line ranges from git diff for all changed files.
    
    Returns:
        Dictionary mapping file paths to tuple with (start_line, end_line) ranges
        for modified sections. Line numbers are 1-based.
    """
    # Get git diff for all files
    diff_output = os.popen("git show").read()
    
    # Parse the diff to get modified files and their line ranges
    result = {}
    current_file = None
    
    for line in diff_output.splitlines():
        # Match lines like "+++ b/path/to/file"
        file_match = re.match(r"^\+\+\+ b/(.*)", line)
        if file_match:
            current_file = file_match.group(1)
            continue
            
        # Match lines like "@@ -100,5 +100,7 @@" where the + part shows new lines
        range_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if range_match and current_file:
            start_line = int(range_match.group(1))  # Keep as 1-based
            line_count = int(range_match.group(2)) if range_match.group(2) else 1
            end_line = start_line + line_count - 1
            result[current_file] = (start_line, end_line)
    
    return result
# New handler functions below ▼▼▼



def handle_code_operation(filepath: str, patch: List[Dict[str, Any]]) -> str:
    """处理基于上下文的代码片段"""
    try:
        if not os.path.exists(filepath):
            # 新建文件
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(patch['content'])
            return ""

        old_file_content = ReadCodeTool().execute({"files": [{"path": filepath}]})
        if not old_file_content["success"]:
            return f"文件读取失败: {old_file_content['stderr']}"
        
        prompt = f"""
You are a code reviewer, please review the following code and merge the code with the context.

Original Code:
{old_file_content["stdout"]}

Patch:
"""
        for patch_item in patch:
            prompt += f"""
Patch:
{patch_item["content"]}
"""
        prompt += f"""
Please merge the code with the context and return the fully merged code.

Output Format:
```[language]
[merged_code]
```
"""
        response = PlatformRegistry().get_codegen_platform().chat_until_success(prompt)
        merged_code = re.search(r"```.*?\n(.*)```", response, re.DOTALL).group(1)
        if not merged_code:
            return f"代码合并失败: {response}"
        # 写入合并后的代码
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(merged_code)
            
        return ""
    except Exception as e:
        return f"文件操作失败: {str(e)}"

def shell_input_handler(user_input: str, agent: Any) -> Tuple[str, bool]:
    lines = user_input.splitlines()
    cmdline = [line for line in lines if line.startswith("!")]
    if len(cmdline) == 0:
        return user_input, False
    else:
        script = '\n'.join([c[1:] for c in cmdline])
        PrettyOutput.print(script, OutputType.CODE, lang="bash")
        if user_confirm(f"是否要执行以上shell脚本？", default=True):
            ShellScriptTool().execute({"script_content": script})
            return "", True
        return user_input, False
    

