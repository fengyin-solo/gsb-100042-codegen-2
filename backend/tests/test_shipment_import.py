"""发运单批量导入的暂存、冲突与并发幂等测试。"""
from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor

from app.services.shipment import ShipmentService
from app.store import store


class ShipmentImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ShipmentService()
        self.token = os.getpid()
        self.staged_files: list[str] = []

    def tearDown(self) -> None:
        for path in self.staged_files:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def csv(self, rows: list[str], prefix: str) -> bytes:
        header = "运单编号,发货方,收货方,发运批次,货物名称"
        return ("\n".join([header, *rows]).replace("PREFIX", prefix)).encode("utf-8")

    def stage(self, content: bytes, name: str = "shipment.csv"):
        job = self.service.stage_import(name, content)
        self.staged_files.append(job["staged_file"])
        self.assertTrue(os.path.exists(job["staged_file"]))
        return job

    def test_stage_validates_required_fields_and_file_duplicates(self) -> None:
        prefix = f"VALID-{self.token}-"
        job = self.stage(self.csv([
            f"PREFIX1,甲仓,乙店,PREFIXB,三文鱼",
            f"PREFIX1,甲仓,乙店,PREFIXB,重复",
            f",缺少运单,乙店,PREFIXB,空编号",
        ], prefix))

        self.assertEqual(job["status"], "staged")
        self.assertEqual(job["valid"], 1)
        self.assertEqual(job["invalid"], 2)
        self.assertEqual(job["rows"][1]["message"], f"文件内运单编号重复，首次出现在第 2 行")

    def test_commit_drives_recalculated_summary_list_and_detail(self) -> None:
        prefix = f"COMMIT-{self.token}-"
        job = self.stage(self.csv([f"PREFIX1,甲仓,乙店,PREFIXB,三文鱼"], prefix))
        result, _ = self.service.commit_import(job["job_id"])

        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["entries"][0]["运单编号"], f"{prefix}1")
        self.assertEqual(result["entries"][0]["发运批次"], f"{prefix}B")
        summary = next(item for item in result["batch_summary"] if item["发运批次"] == f"{prefix}B")
        self.assertEqual(summary["运单数"], 1)
        self.assertEqual(summary["待发运"], 1)
        listed, total = self.service.list_entries(batch=f"{prefix}B")
        self.assertEqual(total, 1)
        self.assertEqual(self.service.get_entry(int(listed[0]["id"]))["运单编号"], f"{prefix}1")

    def test_malformed_csv_remains_staged_for_retry(self) -> None:
        job = self.stage("运单编号,发货方\nPREFIX1,甲仓".encode("utf-8"), "missing-header.csv")

        self.assertEqual(job["status"], "invalid")
        self.assertIn("缺少必要列", job["message"])
        self.assertTrue(os.path.exists(job["staged_file"]))

    def test_concurrent_commit_allows_only_one_batch_per_waybill(self) -> None:
        prefix = f"CONC-{self.token}-"
        first_job = self.stage(self.csv([f"PREFIX-OLD,甲仓,乙店,PREFIX-OLD-B,原单"], prefix))
        first_result, _ = self.service.commit_import(first_job["job_id"])
        original_id = first_result["entries"][0]["id"]

        content = self.csv([
            f"PREFIX-OLD,冲突方,冲突店,PREFIX-NEW-B,冲突行",
            f"PREFIX-A,甲仓,乙店,PREFIX-NEW-B,虾",
            f"PREFIX-B,甲仓,乙店,PREFIX-NEW-B,贝",
        ], prefix)
        job_a = self.stage(content, "a.csv")
        job_b = self.stage(content, "b.csv")

        with ThreadPoolExecutor(max_workers=2) as pool:
            committed = list(pool.map(
                lambda job: self.service.commit_import(job["job_id"]),
                [job_a, job_b],
            ))

        self.assertEqual(sum(result[0]["imported"] for result in committed), 2)
        concurrent_rows = [
            row
            for result, _ in committed
            for row in result["rows"]
            if row["waybill_no"] in {f"{prefix}-A", f"{prefix}-B"}
        ]
        self.assertEqual(sum(row["status"] == "imported" for row in concurrent_rows), 2)
        self.assertEqual(sum(row["status"] == "conflict" for row in concurrent_rows), 2)
        old_rows = [
            row
            for result, _ in committed
            for row in result["rows"]
            if row["waybill_no"] == f"{prefix}-OLD"
        ]
        self.assertTrue(all(row["status"] == "conflict" for row in old_rows))
        self.assertTrue(all(row["entry_id"] == original_id for row in old_rows))

        waybills = [row["运单编号"] for row in store.snapshot("shipment")]
        self.assertEqual(waybills.count(f"{prefix}-A"), 1)
        self.assertEqual(waybills.count(f"{prefix}-B"), 1)

    def test_retry_commit_is_idempotent(self) -> None:
        prefix = f"RETRY-{self.token}-"
        job = self.stage(self.csv([f"PREFIX1,甲仓,乙店,PREFIXB,三文鱼"], prefix))
        first, _ = self.service.commit_import(job["job_id"])
        again, message = self.service.commit_import(job["job_id"])

        self.assertEqual(first["imported"], 1)
        self.assertEqual(again["imported"], 1)
        self.assertIn("未重复", message)
        waybills = [row["运单编号"] for row in store.snapshot("shipment")]
        self.assertEqual(waybills.count(f"{prefix}1"), 1)


if __name__ == "__main__":
    unittest.main()
