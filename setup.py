from setuptools import setup


setup(
    name="scholarfetch",
    version="0.1.0",
    description="Multi-engine scholarly fetch CLI and MCP server",
    py_modules=["scholarfetch_cli", "scholarfetch", "scholarfetch_mcp", "scholarfetch_fastmcp"],
    entry_points={
        "console_scripts": [
            "scholarfetch=scholarfetch_cli:main",
            "scholarfetch-mcp=scholarfetch_mcp:main",
            "scholarfetch-fastmcp=scholarfetch_fastmcp:main",
        ]
    },
)
