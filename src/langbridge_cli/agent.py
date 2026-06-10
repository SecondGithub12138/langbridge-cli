import copy
import json
import sys
import urllib.error
import urllib.request

from langbridge_cli.config import API_URL, MAX_AGENT_STEPS, WRITE_TOOLS
from langbridge_cli.logging import write_turn_log
from langbridge_cli.parse import extract_output_text, print_step_trace
from langbridge_cli.tools import TOOL_SCHEMAS, TOOLS


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
