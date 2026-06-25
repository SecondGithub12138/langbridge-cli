from langbridge_cli.config import HANDOVER_PATH


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "update_plan",
        "description": (
            "Write the full handover plan to the fixed handover document. "
            "Use it to record the component-level subtasks, their status "
            "(TODO / IN_PROGRESS / DONE), and a short note on where the work "
            "stands and what to do next. This overwrites the whole document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Full markdown content of the handover plan.",
                }
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


@tool("update_plan")
def update_plan(content):
    HANDOVER_PATH.write_text(content, encoding="utf-8")
    return f"Updated handover plan ({len(content)} chars) at {HANDOVER_PATH.name}."


def read_handover():
    if not HANDOVER_PATH.exists():
        return ""
    return HANDOVER_PATH.read_text(encoding="utf-8")
