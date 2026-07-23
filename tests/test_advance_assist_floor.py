from ironlog.engine.advance import performed_assist_floor


def test_performed_assist_floor_descending_ladder_floors_to_harder_clean_value():
    ladder = [20, 15, 10, 5, 0]

    assert performed_assist_floor(20.0, ladder, [15.0]) == 15


def test_performed_assist_floor_ascending_ladder_uses_index_not_magnitude():
    ladder = [0, 5, 10, 15, 20]

    assert performed_assist_floor(5.0, ladder, [10.0]) == 10


def test_performed_assist_floor_holds_with_no_more_advanced_clean_value():
    ladder = [20, 15, 10, 5, 0]

    assert performed_assist_floor(10.0, ladder, []) == 10.0
    assert performed_assist_floor(10.0, ladder, [15.0, 20.0]) == 10.0


def test_performed_assist_floor_holds_when_current_none_or_off_ladder():
    ladder = [20, 15, 10, 5, 0]

    assert performed_assist_floor(None, ladder, [15.0]) is None
    assert performed_assist_floor(17.5, ladder, [15.0]) == 17.5


def test_performed_assist_floor_ignores_off_ladder_logged_values():
    ladder = [20, 15, 10, 5, 0]

    assert performed_assist_floor(20.0, ladder, [12.5]) == 20.0
    assert performed_assist_floor(20.0, ladder, [12.5, 15.0]) == 15


def test_performed_assist_floor_picks_most_advanced_clean_value():
    ladder = [20, 15, 10, 5, 0]

    assert performed_assist_floor(20.0, ladder, [15.0, 10.0, 20.0]) == 10
