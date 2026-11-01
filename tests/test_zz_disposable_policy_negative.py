"""DO NOT MERGE: deliberately failing test for the disposable acceptance PR #52."""
import unittest


class DisposableNegativeAcceptance(unittest.TestCase):
    def test_required_check_failure_must_block_merge(self):
        self.fail("intentional failure: required 'test' check must block merge")
