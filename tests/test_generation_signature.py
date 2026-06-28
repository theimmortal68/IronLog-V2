from ironlog.generation.signature import compute_signature, meets_novelty, signature_distance

def test_identical_signatures_distance_zero():
    s = compute_signature([1, 2, 3], ["hyp"], ["myo"], ["pull"], ["s1"])
    assert signature_distance(s, s) == 0.0

def test_different_exercise_set_dominates_distance():
    a = compute_signature([1, 2, 3], ["hyp"], [], ["pull"], ["s1"])
    b = compute_signature([4, 5, 6], ["hyp"], [], ["pull"], ["s1"])
    # exercise-set weight is 0.40; fully-disjoint sets => >= 0.40 distance
    assert signature_distance(a, b) >= 0.40

def test_meets_novelty_threshold():
    a = compute_signature([1, 2, 3], ["hyp"], [], ["pull"], ["s1"])
    b = compute_signature([4, 5, 6], ["str"], ["myo"], ["push"], ["s2"])
    assert meets_novelty(a, [b], threshold=0.30) is True
    assert meets_novelty(a, [a], threshold=0.30) is False
