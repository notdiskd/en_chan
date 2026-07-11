import inspect
from typing import get_type_hints

TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}

def function_to_tool(fn) -> dict:
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    doc = inspect.getdoc(fn) or ""

    description = doc.split("Args:")[0].strip()

    arg_descriptions = {}
    if "Args:" in doc:
        args_section = doc.split("Args:")[1]
        for line in args_section.strip().splitlines():
            line = line.strip()
            if ":" in line:
                name, desc = line.split(":", 1)
                arg_descriptions[name.strip()] = desc.strip()

    properties = {}
    required = []
    for name, param in sig.parameters.items():
        param_type = TYPE_MAP.get(hints.get(name, str), "string")
        properties[name] = {
            "type": param_type,
            "description": arg_descriptions.get(name, ""),
        }
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            properties[name]["default"] = param.default

    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }