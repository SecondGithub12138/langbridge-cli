import copy
import json


def parse_json_string(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def extract_reasoning_items(output):
    return [copy.deepcopy(item) for item in output if item.get("type") == "reasoning"]


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
