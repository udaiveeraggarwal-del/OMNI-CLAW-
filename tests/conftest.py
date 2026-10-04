"""
Pytest configuration and test fixtures for OmniAgent unit tests.
"""

import os
import sys
from pathlib import Path
import pytest

# Ensure omniagent package root is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from omniagent.core.models import ToolResult
from omniagent.core.router import FunctionTool, ToolRegistry, ToolRouter
from omniagent.core.providers.factory import ProviderFactory


@pytest.fixture
def mock_openai():
    return ProviderFactory.create_mock("openai")


@pytest.fixture
def mock_anthropic():
    return ProviderFactory.create_mock("anthropic")


@pytest.fixture
def mock_gemini():
    return ProviderFactory.create_mock("gemini")


@pytest.fixture
def sample_tool_registry():
    registry = ToolRegistry()

    def data_fetcher_fn(query: str):
        # Simulates fetching data based on query
        return {"query": query, "raw_data": [10, 20, 30, 40]}

    def data_analyzer_fn(values: list):
        # Simulates statistical analysis
        nums = [float(v) for v in values]
        mean_val = sum(nums) / len(nums) if nums else 0.0
        sum_val = sum(nums)
        return {"mean": mean_val, "sum": sum_val, "count": len(nums)}

    fetcher_tool = FunctionTool(
        name="data_fetcher",
        description="Fetches raw numeric dataset for a search query.",
        fn=data_fetcher_fn,
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    analyzer_tool = FunctionTool(
        name="data_analyzer",
        description="Computes statistical summary (mean, sum) over numbers.",
        fn=data_analyzer_fn,
        parameters_schema={
            "type": "object",
            "properties": {"values": {"type": "array", "items": {"type": "number"}}},
            "required": ["values"],
        },
    )

    registry.register(fetcher_tool)
    registry.register(analyzer_tool)
    return registry


@pytest.fixture
def sample_tool_router(sample_tool_registry):
    return ToolRouter(registry=sample_tool_registry)
