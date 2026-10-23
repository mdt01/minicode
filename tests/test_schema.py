from minicode.schema import tool_schema
from minicode.tools import Workspace


def test_schema_from_signature_and_docstring(tmp_path):
    schema = tool_schema("edit_file", Workspace(tmp_path).edit_file)
    fn = schema["function"]
    assert fn["name"] == "edit_file"
    assert fn["parameters"]["required"] == ["path", "old_text", "new_text"]
    assert "exact occurrence" in fn["description"]


def test_optional_arguments_are_not_required(tmp_path):
    schema = tool_schema("list_files", Workspace(tmp_path).list_files)
    assert schema["function"]["parameters"]["required"] == []
