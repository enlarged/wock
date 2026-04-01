from functools import wraps


def command_meta(description: str, syntax: str, example: str, permissions: str = "none"):
    """Decorator to attach command metadata for uniform help and error responses."""
    def decorator(func):
        func.command_description = description
        func.command_syntax = syntax
        func.command_example = example
        func.command_permissions = permissions

        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        return wrapper

    return decorator
