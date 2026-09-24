"""Read MCP tool-result fields across the SDK and FastMCP client versions."""


def result_field(response, *names):
    for name in names:
        try:
            return getattr(response, name)
        except AttributeError:
            continue
    raise AttributeError(f"tool result has none of the expected fields: {', '.join(names)}")
