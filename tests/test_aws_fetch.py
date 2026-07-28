from hascore.scanners.aws_fetch import _paginate


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
