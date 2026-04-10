"""
tests/conftest.py

Shared pytest configuration and fixtures for the Phase 1.5 test suite.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "accessibility: checks whether model backends are reachable")
    config.addinivalue_line("markers", "integration: tests that make real API calls (require GROQ_API_KEY)")


def pytest_addoption(parser):
    parser.addoption(
        "--include-large-models",
        action="store_true",
        default=False,
        help="Include optional large models (70B+) in model comparison tests.",
    )


@pytest.fixture
def include_large_models(request):
    """Return True if --include-large-models was passed on the CLI."""
    return request.config.getoption("--include-large-models")
