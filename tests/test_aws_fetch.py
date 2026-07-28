from hascore.scanners.aws_fetch import _collect_next_token, _paginate


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return iter(self.pages)


class FakeClient:
    def __init__(self, pages):
        self._pages = pages

    def get_paginator(self, op):
        return FakePaginator(self._pages)


def test_paginate_concatenates_pages_by_key():
    client = FakeClient([{"Items": [1, 2]}, {"Items": [3]}, {"Other": [9]}])
    assert _paginate(client, "any_op", "Items") == [1, 2, 3]


def test_collect_next_token_follows_tokens():
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        if "NextToken" not in kwargs:
            return {"Connections": [1, 2], "NextToken": "t1"}
        assert kwargs["NextToken"] == "t1"
        return {"Connections": [3]}

    result = _collect_next_token(fake_call, "Connections")

    assert result == [1, 2, 3]
    assert len(calls) == 2
    assert "NextToken" not in calls[0]
    assert calls[1] == {"NextToken": "t1"}


def test_collect_next_token_single_page():
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return {"Connections": [1]}

    result = _collect_next_token(fake_call, "Connections")

    assert result == [1]
    assert len(calls) == 1
