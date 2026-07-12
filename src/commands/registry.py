_COMMAND_REGISTRY: dict[str, callable] = {}

def command(name: str | None = None):
    def decorator(fn):
        cmd_name = name or fn.__name__
        _COMMAND_REGISTRY[cmd_name] = fn
        return fn
    return decorator

def get_registered_commands() -> dict[str, callable]:
    print(f"Registered commands: {list(_COMMAND_REGISTRY.keys())}")
    return dict(_COMMAND_REGISTRY)