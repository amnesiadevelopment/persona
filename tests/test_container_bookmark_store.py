import ast
import inspect

from src.core.container import Container


def test_bookmark_store_defined_once():
    tree = ast.parse(inspect.getsource(Container))
    cls = tree.body[0]
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert names.count("bookmark_store") == 1


def test_bookmark_store_is_property():
    assert isinstance(Container.bookmark_store, property)
