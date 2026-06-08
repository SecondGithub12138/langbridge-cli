import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory


API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.1-codex"
CONFIG_DIR = Path.home() / ".langbridge"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history"
RUNS_DIR = CONFIG_DIR / "runs"
MAX_AGENT_STEPS = 8
MAX_FILE_BYTES = 20_000
WORKSPACE_ROOT = Path.cwd().resolve()


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "list_dir",
        "description": "List files and directories under the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to the current workspace.",
                    "default": ".",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a text file under the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the current workspace.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]

TOOLS = {}


def main():
    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    run_log_path = create_run_log_path()
    session = create_prompt_session() if sys.stdin.isatty() else None

    messages = [
        {
            "role": "system",
            "content": "You are langbridge-cli, a concise coding agent. Help the user implement software step by step.",
        }
    ]

    print(f"langbridge-cli using {model}")
    print(f"Agent loop log: {run_log_path}")
    print("Type /exit to quit.\n")

    while True:
        try:
            text = read_user_input(session)
        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            break

        if text.strip() == "/exit":
            break

        messages.append({"role": "user", "content": text})
        reply = run_agent(api_key, model, messages, run_log_path)
        messages.append({"role": "assistant", "content": reply})
        print(f"\n{reply}\n")


def load_api_key():
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return api_key

    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())["api_key"]

    api_key = getpass.getpass("Enter Codex API key: ")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"api_key": api_key}, indent=2))
    CONFIG_PATH.chmod(0o600)
    return api_key


def create_run_log_path():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return RUNS_DIR / f"{timestamp}.jsonl"


def create_prompt_session():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return PromptSession(history=FileHistory(str(HISTORY_PATH)))


def read_user_input(session):
    if session is not None:
        return session.prompt("langbridge> ")
    return input("langbridge> ")


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


def resolve_workspace_path(path):
    target = (WORKSPACE_ROOT / path).resolve()
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise ValueError("Path must stay inside the current workspace")
    return target


@tool("list_dir")
def list_dir(path="."):
    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such directory: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    entries = []
    for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        kind = "directory" if child.is_dir() else "file"
        entries.append({"name": child.name, "type": kind})

    return json.dumps({"path": str(target.relative_to(WORKSPACE_ROOT)), "entries": entries}, indent=2)


@tool("read_file")
def read_file(path):
    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such file: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    data = target.read_bytes()
    truncated = len(data) > MAX_FILE_BYTES
    data = data[:MAX_FILE_BYTES]

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {path}")

    if truncated:
        text += f"\n\n[truncated after {MAX_FILE_BYTES} bytes]"
    return text


def run_agent(api_key, model, messages, run_log_path):
    agent_input = list(messages)

    for step in range(1, MAX_AGENT_STEPS + 1):
        request_summary = summarize_request(agent_input)
        data = create_response(api_key, model, agent_input)
        output = data.get("output", [])
        tool_calls = [item for item in output if item.get("type") == "function_call"]

        if not tool_calls:
            write_loop_log(run_log_path, step, request_summary, data, [])
            return extract_output_text(output)

        agent_input.extend(output)
        tool_outputs = []
        for call in tool_calls:
            tool_output = run_tool_call(call)
            tool_outputs.append(tool_output)
            agent_input.append(tool_output)

        write_loop_log(run_log_path, step, request_summary, data, tool_outputs)

    return "Agent stopped because it reached the maximum tool-call steps."


def create_response(api_key, model, agent_input):
    body = json.dumps({"model": model, "input": agent_input, "tools": TOOL_SCHEMAS}).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        data = json.loads(error.read())
        raise RuntimeError(data.get("error", {}).get("message", "OpenAI request failed"))

    return data


def run_tool_call(call):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = json.loads(call.get("arguments") or "{}")
        if name not in TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        output = TOOLS[name](**arguments)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def write_loop_log(run_log_path, step, request_summary, response, tool_outputs):
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "step": step,
        "request": request_summary,
        "response": response,
        "tool_outputs": tool_outputs,
    }
    with run_log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def summarize_request(agent_input):
    return {
        "input_items": len(agent_input),
        "last_item_types": [item.get("type") or item.get("role") for item in agent_input[-5:]],
        "tools": [schema["name"] for schema in TOOL_SCHEMAS],
    }


def extract_output_text(output):
    return "".join(
        content["text"]
        for item in output
        for content in item.get("content", [])
        if content["type"] == "output_text"
    )


if __name__ == "__main__":
    main()
