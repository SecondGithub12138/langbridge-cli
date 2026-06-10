import getpass
import copy
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from langbridge_cli.tools import TOOL_SCHEMAS, TOOLS


API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.1-codex"
CONFIG_DIR = Path.home() / ".langbridge"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history"
MAX_AGENT_STEPS = 20
MAX_TOOL_SUMMARY_OUTPUT_CHARS = 300
WORKSPACE_ROOT = Path.cwd().resolve()
RUNS_DIR = WORKSPACE_ROOT / "session-history"
WRITE_TOOLS = {"create_file", "edit_file", "install_python_packages"}
SYSTEM_PROMPT = """You are langbridge-cli, a concise coding agent. Help the user implement
software step by step.

Follow these behavioral guidelines when writing, reviewing, or refactoring code:

1. Think before coding.
- State assumptions explicitly. If uncertain, ask.
- Present multiple plausible interpretations instead of choosing silently.
- Point out simpler approaches and push back when warranted.
- If something is unclear, name what is unclear before proceeding.

2. Simplicity first.
- Write the minimum code needed to solve the request.
- Do not add unrequested features, abstractions, flexibility, or configurability.
- Do not add handling for impossible scenarios.
- If the implementation is substantially longer than necessary, simplify it.

3. Make surgical changes.
- Touch only what the request requires and match the existing style.
- Do not refactor, reformat, or remove unrelated code.
- Remove only unused code created by your own changes.
- Every changed line should trace directly to the user's request.

4. Work toward verifiable goals.
- Translate requests into concrete success criteria.
- For bugs, reproduce the problem and verify the fix.
- For behavior changes, add or update focused tests when practical.
- For multi-step work, state a brief plan with a verification step for each item.
- Continue until the result is verified; report anything you could not verify.

These guidelines favor caution over speed. Use judgment for trivial tasks.

Before calling tools, briefly explain what you intend to learn or accomplish.
Give only a concise user-facing rationale, not private chain-of-thought."""


def main():
    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    run_log_path = create_run_log_path()
    session = create_prompt_session() if sys.stdin.isatty() else None

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    print(f"langbridge-cli using {model}")
    print(f"Agent loop log: {run_log_path}")
    print("Type /exit to quit.\n")

    turn_id = 0
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

        turn_id += 1
        messages.append({"role": "user", "content": text})
        reply, steps = run_agent(api_key, model, messages, run_log_path, turn_id)
        messages.append({"role": "assistant", "content": reply})
        tool_summary = summarize_turn_steps(steps)
        if tool_summary:
            messages.append({"role": "assistant", "content": tool_summary})
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
    return RUNS_DIR / f"{timestamp}.json"


def create_prompt_session():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return PromptSession(history=FileHistory(str(HISTORY_PATH)))


def read_user_input(session):
    if session is not None:
        return session.prompt("langbridge> ")
    return input("langbridge> ")


def run_agent(api_key, model, messages, run_log_path, turn_id):
    agent_input = list(messages)
    initial_agent_input = copy.deepcopy(agent_input)
    steps = []

    for step in range(MAX_AGENT_STEPS):
        data = create_response(api_key, model, agent_input)
        output = data.get("output", [])
        tool_calls = [item for item in output if item.get("type") == "function_call"]
        step_output = copy.deepcopy(output)
        print_step_trace(output, include_message=bool(tool_calls))

        if not tool_calls:
            steps.append({"step": step, "output": step_output})
            reply = extract_output_text(output)
            write_turn_log(run_log_path, turn_id, initial_agent_input, steps, reply)
            return reply, steps

        agent_input.extend(output)
        for call in tool_calls:
            tool_output = run_tool_call(call)
            agent_input.append(tool_output)
            step_output.append(tool_output)

        steps.append({"step": step, "output": step_output})

    reply = "Agent stopped because it reached the maximum tool-call steps."
    write_turn_log(run_log_path, turn_id, initial_agent_input, steps, reply)
    return reply, steps


def create_response(api_key, model, agent_input):
    body = json.dumps(
        {
            "model": model,
            "input": agent_input,
            "tools": TOOL_SCHEMAS,
            "reasoning": {"summary": "auto"},
        }
    ).encode()
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
        if name in WRITE_TOOLS and not approve_write_tool(name, arguments):
            raise PermissionError(f"{name} was not approved")
        output = TOOLS[name](**arguments)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def approve_write_tool(name, arguments):
    if not sys.stdin.isatty():
        return False

    print(f"\nApprove write tool: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    answer = input("Run this tool? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def write_turn_log(run_log_path, turn_id, initial_agent_input, steps, assistant_reply):
    record = {
        "turn_id": turn_id,
        "user": extract_turn_user_input(initial_agent_input),
        "input": initial_agent_input,
        "steps": format_log_steps(steps),
        "assistant": assistant_reply,
    }
    records = []
    if run_log_path.exists():
        records = json.loads(run_log_path.read_text(encoding="utf-8"))

    records.append(record)
    run_log_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_log_steps(steps):
    return [
        {
            "step": step["step"],
            "thought": extract_reasoning_summaries(step.get("output", [])),
            "action": format_actions(step.get("output", [])),
            "observation": format_observations(step.get("output", [])),
        }
        for step in steps
    ]


def format_actions(output):
    actions = []
    for item in output:
        if item.get("type") == "function_call":
            actions.append(
                {
                    "call_id": item.get("call_id"),
                    "name": item.get("name"),
                    "arguments": parse_json_string(item.get("arguments") or "{}"),
                }
            )
        elif item.get("type") == "message":
            actions.append({"type": "message", "content": extract_output_text([item])})
    return actions


def format_observations(output):
    return [
        {
            "call_id": item.get("call_id"),
            "output": item.get("output", ""),
        }
        for item in output
        if item.get("type") == "function_call_output"
    ]


def parse_json_string(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def summarize_turn_steps(steps):
    lines = []
    for step in steps:
        output = step.get("output", [])
        calls = {
            item["call_id"]: item
            for item in output
            if item.get("type") == "function_call" and item.get("call_id")
        }
        results = {
            item["call_id"]: item.get("output", "")
            for item in output
            if item.get("type") == "function_call_output" and item.get("call_id")
        }
        for call_id, call in calls.items():
            name = call.get("name", "unknown")
            arguments = call.get("arguments", "{}")
            result = truncate_text(results.get(call_id, ""), MAX_TOOL_SUMMARY_OUTPUT_CHARS)
            lines.append(f"- {name}({arguments}): {result}")

    if not lines:
        return ""
    return "Tool summary from the previous turn:\n" + "\n".join(lines)


def truncate_text(text, max_chars):
    compact = " ".join(str(text).split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + "..."


def print_step_trace(output, include_message=False):
    thoughts = [extract_output_text(output)] if include_message else []
    thoughts = [thought for thought in thoughts if thought]
    if not thoughts:
        thoughts = extract_reasoning_summaries(output)

    for thought in thoughts:
        print(f"\nThought: {thought}")

    for item in output:
        if item.get("type") != "function_call":
            continue
        name = item.get("name", "unknown")
        arguments = item.get("arguments") or "{}"
        print(f"Action: {name}({arguments})")


def extract_reasoning_summaries(output):
    return [
        content["text"]
        for item in output
        if item.get("type") == "reasoning"
        for content in item.get("summary", [])
        if content.get("type") == "summary_text" and content.get("text")
    ]


def extract_turn_user_input(agent_input):
    for message in reversed(agent_input):
        if message.get("role") == "user":
            return message["content"]
    raise ValueError("agent_input has no user message")


def extract_output_text(output):
    return "".join(
        content["text"]
        for item in output
        for content in item.get("content", [])
        if content["type"] == "output_text"
    )


if __name__ == "__main__":
    main()
