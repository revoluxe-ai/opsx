"""Тесты routing dispatcher."""

from __future__ import annotations

from agents.verify.dispatcher.routing import classify_dispatch_route


def test_route_pyright_static():
    assert classify_dispatch_route({"repair_workflow": "WF_PYRIGHT_TYPE"}) == "static"


def test_route_pytest_test():
    assert classify_dispatch_route({"repair_workflow": "WF_PYTEST_FAILURE"}) == "test"
