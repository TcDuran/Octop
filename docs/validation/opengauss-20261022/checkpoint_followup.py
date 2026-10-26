"""Validate an opt-in checkpoint read prototype in disposable databases."""

import os

import verify as v


def suite():
    v.case(
        "native_bytea_array_aggregation",
        lambda: v.query("SELECT array_agg(ARRAY['channel'::bytea,'type'::bytea,'payload'::bytea])"),
    )
    v.case(
        "json_base64_aggregation_alternative",
        lambda: v.query(
            "SELECT json_agg(ARRAY[encode('channel'::bytea,'base64'),encode('payload'::bytea,'base64')])"
        ),
        scope="prototype",
    )
    v.checkpoint_tests()


if __name__ == "__main__":
    os.environ["OCTOP_VERIFY_RESULTS_FILE"] = "checkpoint-results.json"
    os.environ["OCTOP_VERIFY_CHECKPOINT_JSON_READ"] = "1"
    v.main(suite)
