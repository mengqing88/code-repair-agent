from target import rank_users


def test_empty_list_returns_empty_list():
    assert rank_users([]) == []


def test_equal_scores_keep_input_order():
    users = [{"name": "a", "score": 5}, {"name": "b", "score": 5}]
    assert [user["name"] for user in rank_users(users)] == ["a", "b"]


def test_input_is_not_modified():
    users = [{"name": "a", "score": 1}]
    rank_users(users)
    assert users == [{"name": "a", "score": 1}]
