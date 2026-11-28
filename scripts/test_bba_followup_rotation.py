from datetime import datetime, timezone
import unittest
from unittest.mock import patch
import bba_project_followup as f

class RotationTests(unittest.TestCase):
    def test_consecutive_days_advance_a_full_batch(self):
        rows=[dict(projectId=f"case{i:03}", title=f"Development project {i}",projectHorizon="unknown") for i in range(100)]
        registry=dict(projects=[dict(projectIDs=["known"], aliases=["Original name","Revised name"])])
        batches=[]
        for day in [27,28]:
            with patch.object(f,"datetime") as clock:
                clock.now.return_value=datetime(2026,11,day,tzinfo=timezone.utc)
                tasks=f.queries(rows,registry)
            self.assertEqual(len(tasks),12)
            batches.append({task["projectIDs"][0] for task in tasks[2:]})
        self.assertFalse(batches[0] & batches[1])
        self.assertEqual(len(batches[0] | batches[1]),20)
