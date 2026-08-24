from product_factory_app.experiments.harness import select_winner


def test_winner_prioritizes_acceptance_then_total_path_cost() -> None:
    records = [
        {
            "candidate": "expensive",
            "accepted_result": True,
            "quality_score": 0.94,
            "total_cost_usd": 0.02,
            "time_to_acceptance_seconds": 0.002,
        },
        {
            "candidate": "balanced",
            "accepted_result": True,
            "quality_score": 0.94,
            "total_cost_usd": 0.006,
            "time_to_acceptance_seconds": 0.007,
        },
        {
            "candidate": "cheap-failure",
            "accepted_result": False,
            "quality_score": 0.0,
            "total_cost_usd": 0.001,
            "time_to_acceptance_seconds": None,
        },
    ]

    assert select_winner(records)["candidate"] == "balanced"
