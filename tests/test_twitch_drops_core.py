from functionality.twitch_drops.twitch_drops import (
	GQLOperation,
	_merge_data,
	is_first_party_validate,
	ANDROID_CLIENT_ID,
)


def test_gql_operation_with_variables_merges_copy():
	ops = GQLOperation("TestOp", "abc123", variables={"foo": "bar"})
	merged = ops.with_variables({"foo": "override", "baz": 1})
	assert merged is not ops
	assert merged["variables"]["foo"] == "override"
	assert merged["variables"]["baz"] == 1


def test_merge_data_prefers_primary_values():
	a = {"id": 1, "nested": {"value": "primary", "other": 1}}
	b = {"nested": {"value": "secondary", "extra": 2}, "new": 3}
	result = _merge_data(a, b)
	assert result["nested"]["value"] == "primary"
	assert result["nested"]["extra"] == 2
	assert result["new"] == 3


def test_is_first_party_validate():
	payload = {"client_id": ANDROID_CLIENT_ID}
	assert is_first_party_validate(payload) is True
	assert is_first_party_validate({"client_id": "other"}) is False
