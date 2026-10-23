from minicode.v01_text_protocol import find_tool_call


def test_finds_tool_tag():
    text = 'Let me look.\n<tool name="read_file">{"path": "app/main.py"}</tool>'
    assert find_tool_call(text) == ("read_file", {"path": "app/main.py"})


def test_plain_answer_has_no_tool():
    assert find_tool_call("All done: I added the test.") is None
