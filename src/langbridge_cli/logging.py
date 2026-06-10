import json
import urllib.request

from langbridge_cli.config import API_URL, MAX_SESSION_SUMMARY_INPUT_CHARS, MAX_TOOL_SUMMARY_OUTPUT_CHARS
from langbridge_cli.parse import (
    extract_output_text,
    extract_reasoning_items,
    extract_reasoning_summaries,
    extract_turn_user_input,
    parse_json_string,
    truncate_text,
)
from langbridge_cli.session import read_session_log


def write_turn_log(run_log_path, turn_id, initial_agent_input, steps, assistant_reply):
    record = {
        "turn_id": turn_id,
        "user": extract_turn_user_input(initial_agent_input),
        "input": initial_agent_input,
        "steps": format_log_steps(steps),
        "assistant": assistant_reply,
    }
    session_log = {"summary": "", "turns": []}
    if run_log_path.exists():
        session_log = read_session_log(run_log_path)

    session_log["turns"].append(record)
    run_log_path.write_text(
        json.dumps(session_log, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_session_summary(api_key, model, run_log_path):
    session_log = read_session_log(run_log_path)
    if session_log["summary"]:
        return

    session_log["summary"] = create_session_summary(api_key, model, session_log["turns"])
    run_log_path.write_text(
        json.dumps(session_log, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_session_summary(api_key, model, records):
    prompt = (
        "Summarize this coding-agent CLI session as a short title for a session picker. "
        "Return only the title, no punctuation wrapper, under 12 words.\n\n"
        f"{session_summary_input(records)}"
    )
    data = create_text_response(
        api_key,
        model,
        [
            {"role": "system", "content": "You write concise session titles."},
            {"role": "user", "content": prompt},
        ],
    )
    return extract_output_text(data.get("output", [])).strip()


def create_text_response(api_key, model, agent_input):
    body = json.dumps({"model": model, "input": agent_input}).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def session_summary_input(records):
    lines = []
    for record in records[-5:]:
        user = record.get("user")
        assistant = record.get("assistant")
        if user:
            lines.append(f"User: {truncate_text(user, 300)}")
        if assistant:
            lines.append(f"Assistant: {truncate_text(assistant, 300)}")

    return truncate_text("\n".join(lines), MAX_SESSION_SUMMARY_INPUT_CHARS)


def format_log_steps(steps):
    return [
        {
            "step": step["step"],
            "reasoning": extract_reasoning_items(step.get("output", [])),
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
