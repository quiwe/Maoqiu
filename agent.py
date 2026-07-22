import os                    # 导入操作系统模块（本文件未实际使用，预留用的）
import json                  # 导入JSON模块，用于解析AI返回的工具调用参数
import subprocess            # 导入子进程模块，用于在终端执行系统命令
from openai import OpenAI    # 从openai库导入OpenAI客户端类，用于调用大模型API

CONFIG_FILE = "config.json"  # 配置文件名，存储API密钥和其他配置

def guide_setup():
    print("\n" + "="*40)
    print("欢迎使用毛球 AI 助手！检测到这是首次运行，请进行基础配置。")
    print("="*40)

    api_key = input("\n1.请输入您的 API KEY（必填）：").strip()
    while not api_key:
        print("API KEY 不能为空，请重新输入。")
        api_key = input("请输入您的 API KEY（必填）：").strip()

    base_url = input("\n2.请输入 API 的基础地址(必填)：").strip()
    while not base_url:
        print("API 基础地址不能为空，请重新输入。")
        base_url = input("请输入 API 的基础地址(必填)：").strip()

    model_name = input("\n3. 请输入模型名称(必填)： ").strip()
    while not model_name:
        print("模型不能为空，请重新输入。")
        model_name = input("请输入模型名称(必填)： ").strip()
    #将配置打包成字典
    config_data = {
        "api_key": api_key,
        "base_url": base_url,
        "model_name": model_name
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:  # 打开配置文件，写入模式
        json.dump(config_data, f, indent=4)  # 将配置字典写入文件，格式化为JSON

    print("\n✅ 配置已成功保存到 config.json！下次启动将自动加载。")
    print("="*40 + "\n")
    return config_data

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return guide_setup()  # 如果配置文件不存在，调用引导设置函数
    else:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:  # 打开配置文件，读取模式
            return json.load(f)  # 解析JSON内容为Python字典并返回


config = load_config()  # 加载配置文件，如果不存在则引导用户设置
client = OpenAI(
    api_key=config["api_key"],  # API密钥（用于身份验证）
    base_url=config["base_url"]                # API的基础地址（小米MiMo的服务地址）
)

def run_cmd(cmd):  # 定义执行终端命令的函数，接收一个命令字符串参数
    print(f"\n毛球正在执行命令{cmd}")  # 打印正在执行的命令，给用户看
    try:  # 开始异常捕获
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True,encoding="utf-8", errors="ignore")  # 执行命令，shell=True允许执行shell语法，capture_output=True捕获输出，text=True以字符串返回
        return result.stdout or result.stderr or "命令执行成功，但是没有输出文字"  # 优先返回标准输出，没有则返回错误输出，都没有则返回默认提示
    except Exception as e:  # 捕获所有异常
        return f"运行报错了: {str(e)}"  # 返回错误信息字符串

# 定义工具列表，告诉大模型有哪些函数可以调用（Function Calling机制）
tools_menu = [{
    "type": "function",           # 工具类型为函数
    "function": {                 # 函数的具体定义
        "name": "run_cmd",        # 函数名，必须和Python中定义的函数名一致
        "description": "在终端执行一条命令",  # 函数功能描述，大模型根据这个决定何时调用
        "parameters": {           # 函数参数定义
            "type": "object",     # 参数类型为对象
            "properties": {       # 对象的属性列表
                "cmd": {          # cmd参数的定义
                    "type": "string",              # 参数类型为字符串
                    "description": "要执行的命令"   # 参数描述，帮助大模型理解该传什么
                }
            },
            "required": ["cmd"]   # 必填参数列表，cmd是必传的
        }
    }
}]

def main():  # 定义主函数，程序的入口
    print("毛球启动成功！")  # 启动提示
    messages = [{                 # 初始化对话历史列表
    "role": "system",             # 角色为system，设定AI的行为准则
    "content": "你是一个运行在 Windows 系统电脑上的 AI 助手。需要查询文件或运行代码时，请使用 run_cmd 工具。终端命令请尽量使用 Windows cmd 或 PowerShell 语法（例如使用 dir 代替 ls）。"  # system prompt：告诉AI它的角色和任务
}]

    while True:  # 无限循环，持续接收用户输入
        user_input = input("\n You > ")  # 获取用户的输入
        if user_input.lower() == "exit":  # 如果用户输入exit（不区分大小写）
            print("毛球已退出。")  # 打印退出提示
            break  # 跳出循环，结束程序
        messages.append({"role": "user", "content": user_input})  # 把用户输入追加到对话历史中

        while True:  # 内层循环，处理AI的回复和工具调用
            response = client.chat.completions.create(  # 调用大模型API，发起对话请求
                model=config["model_name"],    # 使用的模型名称（小米MiMo v2.5 Pro）
                messages=messages,         # 传入完整的对话历史
                tools=tools_menu           # 传入可用的工具列表，让AI知道可以调用哪些函数
            )

            ai_msg = response.choices[0].message  # 取第一条返回的消息（AI的回复）

            if ai_msg.tool_calls:  # 如果AI决定调用工具（即需要执行命令）
                tool_call = ai_msg.tool_calls[0]  # 取第一个工具调用（这里只有一个工具）

                try:
                    arguments = json.loads(tool_call.function.arguments)  # 解析AI返回的JSON参数字符串为Python字典
                    cmd_to_run = arguments.get("cmd", "")
                except json.JSONDecodeError:
                    cmd_to_run = ""

                real_result = run_cmd(cmd_to_run)  # 用AI生成的命令参数，真正执行命令，拿到执行结果

                messages.append(ai_msg)  # 把AI的工具调用消息追加到对话历史（OpenAI要求的格式）
                messages.append({        # 把命令执行结果作为工具返回值追加到对话历史
                    "role": "tool",      # 角色为tool，表示这是工具的返回结果
                    "tool_call_id": tool_call.id,  # 关联对应的工具调用ID（让AI知道这是哪次调用的结果）
                    "content": real_result            # 工具执行的实际结果内容
                })
                """
                final_response = client.chat.completions.create(  # 再次调用大模型，让AI根据命令执行结果生成最终回复
                    model=config["model_name"],  # 使用的模型名称
                    messages=messages        # 传入更新后的完整对话历史
                )
                print(f"\n毛球的最终回复: {final_response.choices[0].message.content}")  # 打印AI的最终回复
                """
            else:  # 如果AI没有调用工具，说明是普通对话
                print(f"\n 毛球 > {ai_msg.content}")  # 直接打印AI的回复内容
                messages.append({"role": "assistant", "content": ai_msg.content})  # 把AI回复追加到对话历史
                break  # 跳出内层循环，回到外层循环等待用户下一次输入

if __name__ == "__main__": 
    try:
        main() 
    except KeyboardInterrupt:
        print("\n\n接收到退出指令，毛球已安静退出。下次见！")